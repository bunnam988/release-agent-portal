"""Local reverse proxy that injects a fresh SAT bearer token into every
request before forwarding it to Comcast's Flow LLM gateway.

Adapts the lazy refresh-with-expiry-cache pattern from jira_autotriage's
JiraClassifierService (a teammate's separate project) -- that pattern
works there because the token refresh happens inline in the same
process that actually calls the model. opencode is a *different*
process that only reads its configured apiKey once at startup, so a
refreshed token would never reach it without a restart -- which risks
killing a live session if the timing is unlucky.

Instead, opencode's provider baseURL points at this proxy (127.0.0.1
only, never a separate network endpoint) instead of the real gateway
directly. This proxy does the same lazy check-and-refresh on every
request and forwards with a guaranteed-fresh token -- zero restarts
needed anywhere, for opencode or this proxy itself.

Runs as its own process -- see docker-entrypoint.sh.
"""

import logging
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm_token_proxy")

REAL_BASE_URL = os.environ.get(
    "RDKB_LLM_BASE_URL",
    "https://flow.api.de.comcast.com/orgs/rdkb-release-agent/modelgws/rdkb-middleware-release-agent/openai/v1",
).rstrip("/")
SAT_URL = os.environ.get("RDKB_SAT_URL", "https://sat-prod.codebig2.net/v2/oauth/token")

app = FastAPI(title="rdkb-llm-token-proxy")

# Module-level cache -- same shape as JiraClassifierService's _sat_token /
# _sat_token_expiry: refresh lazily, a few minutes before actual expiry,
# not on a fixed timer.
_token: str | None = None
_token_expiry: float = 0.0
_REFRESH_BUFFER_SECONDS = 300


async def _fetch_token() -> str:
    client_id = os.environ["RDKB_SAT_CLIENT_ID"]
    client_secret = os.environ["RDKB_SAT_CLIENT_SECRET"]
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            SAT_URL,
            headers={
                "Content-Type": "application/json",
                "X-Client-Id": client_id,
                "X-Client-Secret": client_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("access_token")
    if not token:
        raise RuntimeError("SAT response missing 'access_token'")

    global _token, _token_expiry
    _token = token
    _token_expiry = time.time() + int(data.get("expires_in", 3600)) - _REFRESH_BUFFER_SECONDS
    logger.info("SAT token refreshed (expires_in=%s)", data.get("expires_in"))
    return token


async def _get_token() -> str:
    if _token and time.time() < _token_expiry:
        return _token
    return await _fetch_token()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request):
    token = await _get_token()
    body = await request.body()
    # Strip headers that must be recomputed for the outgoing request
    # (host/content-length would otherwise reflect this proxy, not the
    # real upstream; authorization gets replaced with the real token).
    forward_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in ("host", "authorization", "content-length")
    }
    forward_headers["Authorization"] = f"Bearer {token}"

    target_url = f"{REAL_BASE_URL}/{path}"

    client = httpx.AsyncClient(timeout=300.0)
    upstream_request = client.build_request(
        request.method,
        target_url,
        params=request.query_params,
        headers=forward_headers,
        content=body,
    )
    upstream_response = await client.send(upstream_request, stream=True)

    async def stream_and_close():
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_and_close(),
        status_code=upstream_response.status_code,
        headers={
            k: v
            for k, v in upstream_response.headers.items()
            if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")
        },
    )
