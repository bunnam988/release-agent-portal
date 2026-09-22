import asyncio
import logging
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..opencode_client import OpencodeClient
from ..routers.auth import get_role
from ..schemas import (
    PermissionDecisionRequest,
    QuestionAnswerRequest,
    ReplyRequest,
    StartSessionRequest,
    StartSessionResponse,
)
from ..workflows import INTEGRATION_LABELS, build_message, get_workflow

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
client = OpencodeClient()
logger = logging.getLogger("release_agent_portal.sessions")

# Legacy opencode's POST /session/{id}/message blocks until the whole agent
# turn finishes (can be minutes for a real skill run). We fire it in the
# background so /api/sessions returns immediately and the caller watches
# progress via /events; keep a strong reference so the task isn't GC'd.
_background_tasks: set[asyncio.Task] = set()


@dataclass
class QueuedRun:
    session_id: str
    label: str
    user: str
    queued_at: float


# Only mutating workflows ever touch these (see client.mutating_lock) --
# purely informational, so the UI can tell a queued user *why* their run
# hasn't started instead of showing a blank, indistinguishable-from-broken
# "Connecting..." screen for however long the run ahead of them takes.
# In-memory only: a backend restart loses queue position display (not
# correctness -- the actual serialization is still the asyncio.Lock
# itself), which is an acceptable tradeoff for a purely informational
# feature. Only meaningful with a single backend process/worker (see
# Dockerfile), same assumption client.mutating_lock already depends on.
_mutating_queue: list[QueuedRun] = []
_current_mutating: QueuedRun | None = None

# opencode resets a session's agent back to "build" on any message send
# that doesn't explicitly re-specify it (see opencode_client.send_prompt's
# docstring) -- every message to a custom-agent session (start AND every
# later reply) must pass the same agent, so track which agent each of our
# sessions was started with. In-memory only, same tradeoff as the queue
# state above: a backend restart loses this for sessions already running,
# so a reply sent after a restart would fall back to no agent (matching
# the old, buggy behavior) rather than crash -- acceptable degradation,
# not a correctness issue for new sessions started after the restart.
_session_agents: dict[str, str] = {}


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            # The HTTP caller (start_session/reply) already returned 200 by
            # the time this runs -- that's the whole point of backgrounding
            # a call that can take minutes. If sending the prompt itself
            # fails (session vanished, opencode died mid-run), there is no
            # HTTP response left to attach this to, so at minimum make sure
            # it's not silently lost to asyncio's "exception never
            # retrieved" warning -- log it so it's actually investigable.
            logger.error("background prompt task failed: %s", exc)

    task.add_done_callback(_on_done)


async def _send_prompt_locked(
    session_id: str, text: str, mutating: bool, label: str = "", user: str = "", agent: str | None = None
) -> None:
    global _current_mutating
    if mutating:
        # Serialize mutating workflows so two runs don't race on the same
        # git worktree / session-state YAML files (see DESIGN.md).
        entry = QueuedRun(session_id, label, user, time.time())
        _mutating_queue.append(entry)
        try:
            async with client.mutating_lock:
                if entry in _mutating_queue:
                    _mutating_queue.remove(entry)
                _current_mutating = entry
                try:
                    await client.send_prompt(session_id, text, agent=agent)
                finally:
                    if _current_mutating is entry:
                        _current_mutating = None
        except BaseException:
            if entry in _mutating_queue:
                _mutating_queue.remove(entry)
            raise
    else:
        await client.send_prompt(session_id, text, agent=agent)


@router.post("", response_model=StartSessionResponse)
async def start_session(http_request: Request, req: StartSessionRequest):
    try:
        workflow = get_workflow(req.workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # The real enforcement point for role-based access (see
    # routers/workflows.py, which only filters what the "user" role *sees*
    # -- this is what actually stops a "user"-role session from starting
    # an admin-only workflow even via a direct API call bypassing the UI).
    if workflow.admin_only and get_role(http_request) != "admin":
        raise HTTPException(status_code=403, detail="This workflow requires admin access.")

    missing = [
        arg.label
        for arg in workflow.args
        if arg.required and not str(req.args.get(arg.name, "")).strip()
    ]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required field(s): {', '.join(missing)}")

    if workflow.requires_mcp:
        # Fail fast with one clear, portal-native message instead of
        # letting the agent try, fail, and tell the user to run CLI
        # commands and "restart OpenCode" -- remediation text meaningless
        # to someone who only has this web UI. See DESIGN.md "pre-flight
        # integration check".
        status = await client.mcp_status()
        down = [m for m in workflow.requires_mcp if not status.get(m)]
        if down:
            names = ", ".join(INTEGRATION_LABELS.get(m, m) for m in down)
            raise HTTPException(
                status_code=503,
                detail=f"{names} is currently unavailable, so this workflow can't run right now. "
                "Please try again shortly or contact your administrator if this persists.",
            )

    message = build_message(workflow, req.args)
    session = await client.create_session(title=f"{workflow.label} \u2014 {req.user}", agent=workflow.agent)
    if workflow.agent:
        _session_agents[session["id"]] = workflow.agent
    _fire_and_forget(
        _send_prompt_locked(session["id"], message, workflow.mutating, workflow.label, req.user, workflow.agent)
    )

    logger.info(
        "session started",
        extra={"user": req.user, "workflow_id": req.workflow_id, "session_id": session["id"]},
    )
    return StartSessionResponse(session_id=session["id"])


@router.post("/{session_id}/reply")
async def reply(session_id: str, req: ReplyRequest):
    # Must keep re-passing the same agent on every reply, not just the
    # initial message -- see opencode_client.send_prompt's docstring.
    _fire_and_forget(client.send_prompt(session_id, req.text, agent=_session_agents.get(session_id)))
    return {"ok": True}


@router.get("/{session_id}/queue")
async def queue_status(session_id: str):
    """Lets a session's own page ask "is my run actually going, or am I
    queued behind someone else's mutating run" -- see _mutating_queue.
    Returns not_queued for anything that was never mutating, or that has
    already been dequeued (running or finished)."""
    if _current_mutating is not None and _current_mutating.session_id == session_id:
        return {"status": "running"}
    for i, entry in enumerate(_mutating_queue):
        if entry.session_id == session_id:
            blocker = _current_mutating if _current_mutating is not None else _mutating_queue[0]
            ahead = i + (1 if _current_mutating is not None else 0)
            return {
                "status": "queued",
                "ahead_count": ahead,
                "blocked_by": {"label": blocker.label, "user": blocker.user},
            }
    return {"status": "not_queued"}


@router.get("/{session_id}/events")
async def stream_events(session_id: str):
    return StreamingResponse(
        client.stream_session_events(session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{session_id}/permissions")
async def pending_permissions(session_id: str):
    return await client.list_permissions(session_id)


@router.post("/{session_id}/permissions/{request_id}")
async def reply_permission(session_id: str, request_id: str, decision: PermissionDecisionRequest):
    await client.reply_permission(session_id, request_id, decision.allow)
    return {"ok": True}


@router.get("/{session_id}/questions")
async def pending_questions(session_id: str):
    return await client.list_questions(session_id)


@router.post("/{session_id}/questions/{request_id}")
async def answer_question(session_id: str, request_id: str, req: QuestionAnswerRequest):
    await client.reply_question(request_id, req.answers)
    return {"ok": True}


@router.delete("/{session_id}/questions/{request_id}")
async def decline_question(session_id: str, request_id: str):
    await client.reject_question(request_id)
    return {"ok": True}


@router.get("")
async def list_sessions():
    return await client.list_sessions()


@router.get("/{session_id}/messages")
async def get_messages(session_id: str):
    return await client.get_messages(session_id)
