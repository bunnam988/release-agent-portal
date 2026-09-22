"""Thin async wrapper around the headless opencode server's HTTP + SSE API.

Deliberately uses opencode's LEGACY `/session/*` endpoints, not the newer
`/api/session/*` ("v2"/"next") ones. Verified by hand against opencode
1.18.7: the `/api/session/*` family fails every GitHub Copilot call with
`400 bad request: missing required Authorization header`, while the
identical request against `/session/*` succeeds and returns real model
output. This looks like a provider-auth wiring bug in that opencode
version's newer API, not anything about our credentials/config -- worth
re-checking on every opencode upgrade in case it's fixed upstream.

Stays close to the raw API rather than re-modeling every event type:
opencode's global event stream (`GET /event`) is filtered to this session's
events and passed through to the frontend mostly unchanged (see
`stream_session_events`) so the UI can render whatever opencode emits
without this layer having to track its internal message/part schema. The
pieces we DO need to interpret (starting a run, replying, permission gates)
get typed helpers.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import settings


class OpencodeClient:
    def __init__(self) -> None:
        auth = None
        if settings.opencode_password:
            auth = (settings.opencode_username, settings.opencode_password)
        self._auth = auth
        self._base_url = settings.opencode_url.rstrip("/")
        # Serializes workflow starts that touch shared repo-checkout state
        # (e.g. .stable2-meta-sync/meta-rdk-broadband). See DESIGN.md D-risk
        # on concurrent runs. Phase-1 approach: one lock for all mutating
        # workflows; revisit if this becomes a throughput problem.
        self.mutating_lock = asyncio.Lock()

    def _client(self, timeout: float | None = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, auth=self._auth, timeout=timeout)

    async def health(self) -> bool:
        async with self._client(timeout=5.0) as client:
            resp = await client.get("/api/health")
            return resp.status_code == 200

    async def mcp_status(self) -> dict[str, bool]:
        """Which MCP servers (Jira, etc.) are actually connected right now.

        Checked before starting a workflow so a user never sees the agent
        try, fail, and tell them to run CLI commands and "restart OpenCode"
        -- remediation text written for a developer at a terminal, useless
        to someone using the hosted UI. See DESIGN.md "pre-flight
        integration check".
        """
        async with self._client(timeout=15.0) as client:
            resp = await client.get("/mcp")
            resp.raise_for_status()
            return {name: info.get("status") == "connected" for name, info in resp.json().items()}

    async def create_session(self, title: str | None = None, agent: str | None = None) -> dict[str, Any]:
        async with self._client() as client:
            body: dict[str, Any] = {}
            if agent:
                body["agent"] = agent
            if title:
                body["title"] = title
            resp = await client.post("/session", json=body)
            resp.raise_for_status()
            return resp.json()

    async def send_prompt(self, session_id: str, text: str, agent: str | None = None) -> dict[str, Any]:
        # Unlike the v2 API's /prompt (which admits the message and returns
        # immediately, streaming the turn asynchronously), legacy
        # POST /session/{id}/message BLOCKS until the whole agent turn
        # finishes -- which for a real skill run (reads files, runs
        # scripts, calls Jira/GitHub/Gerrit) can take minutes. Callers that
        # need the HTTP response to return promptly should schedule this
        # with asyncio.create_task rather than awaiting it directly; the
        # caller can still watch progress via stream_session_events.
        #
        # `agent` matters on EVERY call, not just the first: confirmed by
        # testing directly against a running opencode instance that
        # POST .../session (create) correctly sets the session's agent,
        # but POST .../message with no `agent` field silently resets it
        # back to "build" for that turn (and the session's own stored
        # agent along with it) -- found the hard way when every custom
        # orchestrator run turned out to have actually been running as
        # the generic build agent the whole time. Callers must pass the
        # workflow's agent on every send, not just when starting.
        body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if agent:
            body["agent"] = agent
        async with self._client(timeout=None) as client:
            resp = await client.post(f"/session/{session_id}/message", json=body)
            resp.raise_for_status()
            return resp.json()

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            resp = await client.get("/session")
            resp.raise_for_status()
            return resp.json()

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Full message history for a session (role + parts), so revisiting
        a past run from the History page shows its actual content instead
        of an empty transcript (the SSE stream is live-only, nothing
        replays on a fresh subscribe).
        """
        async with self._client() as client:
            resp = await client.get(f"/session/{session_id}/message")
            resp.raise_for_status()
            return resp.json()

    async def list_permissions(self, session_id: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            resp = await client.get("/permission")
            resp.raise_for_status()
            return [p for p in resp.json() if p.get("sessionID") == session_id]

    async def reply_permission(self, session_id: str, request_id: str, allow: bool) -> None:
        async with self._client() as client:
            resp = await client.post(
                f"/session/{session_id}/permissions/{request_id}",
                json={"response": "once" if allow else "reject"},
            )
            resp.raise_for_status()

    async def list_questions(self, session_id: str) -> list[dict[str, Any]]:
        """Distinct from permissions: some skills use opencode's structured
        multi-choice `question` tool (e.g. "which Jira label should I use?")
        rather than a bash/edit permission gate.
        """
        async with self._client() as client:
            resp = await client.get("/question")
            resp.raise_for_status()
            return [q for q in resp.json() if q.get("sessionID") == session_id]

    async def reply_question(self, request_id: str, answers: list[list[str]]) -> None:
        async with self._client() as client:
            resp = await client.post(f"/question/{request_id}/reply", json={"answers": answers})
            resp.raise_for_status()

    async def reject_question(self, request_id: str) -> None:
        async with self._client() as client:
            resp = await client.post(f"/question/{request_id}/reject")
            resp.raise_for_status()

    async def stream_session_events(self, session_id: str) -> AsyncIterator[bytes]:
        """Filter opencode's global SSE stream (`GET /event`) down to this
        session and re-emit as `data: {...}\n\n` chunks the frontend can
        consume directly via EventSource.
        """
        async with self._client(timeout=None) as client:
            async with client.stream("GET", "/event") as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        evt = json.loads(line[len("data:") :])
                    except ValueError:
                        continue
                    props = evt.get("properties", {})
                    if props.get("sessionID") not in (None, session_id):
                        continue
                    yield f"{line}\n\n".encode()
