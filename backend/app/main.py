import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .opencode_client import OpencodeClient
from .routers import auth, sessions, workflows
from .routers.auth import auth_enabled, get_role

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("release_agent_portal.main")

app = FastAPI(title="release-agent-portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,  # required for the session cookie to be sent cross-port
    allow_methods=["*"],
    allow_headers=["*"],
)

# Everything under /api except /api/auth/* and /api/health requires a valid
# session cookie once a portal password is configured (see routers/auth.py
# -- blank password = this is a no-op, same "opt-in" convention as
# opencode_password). Enforced once here rather than a Depends() sprinkled
# across every route in workflows.py/sessions.py so nothing can be added
# later and accidentally left unprotected.
_PUBLIC_PATHS = ("/api/auth/", "/api/health")


@app.middleware("http")
async def enforce_auth(request: Request, call_next):
    if (
        auth_enabled()
        and request.url.path.startswith("/api/")
        and not request.url.path.startswith(_PUBLIC_PATHS[0])
        and request.url.path != _PUBLIC_PATHS[1]
        and get_role(request) is None
    ):
        return JSONResponse(status_code=401, content={"detail": "Not logged in."})
    return await call_next(request)


app.include_router(auth.router)
app.include_router(workflows.router)
app.include_router(sessions.router)


# Every route in this app talks to the opencode server via httpx, and
# none of those calls were individually wrapped in try/except -- an
# unhandled httpx exception used to bubble up as FastAPI's generic,
# JSON-less 500 response, which client.ts's error parsing can't extract a
# useful message from (falls back to "Request failed (500)" no matter
# what actually happened: session not found, opencode down, opencode
# returned a 400, etc). Two handlers here give every route a consistent,
# real error message for free instead of needing per-route try/except.
@app.exception_handler(httpx.HTTPStatusError)
async def handle_opencode_http_error(request: Request, exc: httpx.HTTPStatusError):
    status = exc.response.status_code
    logger.warning("opencode returned %s for %s %s", status, exc.request.method, exc.request.url)
    if status == 404:
        return JSONResponse(
            status_code=404,
            content={"detail": "That session (or a resource in it) no longer exists on the opencode server."},
        )
    return JSONResponse(
        status_code=502,
        content={"detail": f"The opencode server returned an unexpected error (HTTP {status})."},
    )


@app.exception_handler(httpx.RequestError)
async def handle_opencode_unreachable(request: Request, exc: httpx.RequestError):
    logger.error("opencode unreachable: %s", exc)
    return JSONResponse(
        status_code=503,
        content={
            "detail": "The opencode server is unreachable right now. "
            "Please try again shortly or contact your administrator if this persists.",
        },
    )


@app.get("/api/health")
async def health():
    # This endpoint's whole purpose is to report opencode's reachability
    # as *data* in a 200 response -- it must never itself fail just
    # because opencode is down, which is exactly what the global
    # httpx.RequestError handler above would otherwise do to it.
    try:
        opencode_ok = await OpencodeClient().health()
    except httpx.RequestError:
        opencode_ok = False
    return {"ok": True, "opencode_reachable": opencode_ok}


# ── Serve the built React frontend (single-container deployment) ──────────
# Only active when the frontend has actually been built into this image --
# absent in plain local `uvicorn` dev (nothing built it), present in the
# combined Dockerfile image used for CNAP (which COPYs the built dist to
# /app/frontend/dist, a sibling of /app/app -- this file's own directory
# -- not /workspace/frontend/dist; verified against the actual running
# container after getting this wrong once with one `.parent` too many).
# Registered last, deliberately: every `/api/*` route above already
# claimed its exact path, so this only ever catches requests nothing
# else matched.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="static-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        # A genuinely unmatched /api/* path should still 404 as JSON, not
        # silently return the HTML app -- only fall back to the SPA shell
        # for real page routes (React Router handles /history,
        # /sessions/:id, etc. client-side once index.html loads).
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
