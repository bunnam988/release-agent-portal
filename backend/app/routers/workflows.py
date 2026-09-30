import os

import yaml
from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..opencode_client import OpencodeClient
from ..routers.auth import get_role
from ..workflows import CATALOG

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
client = OpencodeClient()


@router.get("")
async def list_workflows(request: Request):
    # Filtered server-side (not just hidden/greyed-out client-side) so the
    # "user" role genuinely never learns these exist, on top of
    # POST /api/sessions independently refusing to start them -- see that
    # route for why relying on this list alone wouldn't be real
    # enforcement (a direct API call could otherwise bypass it).
    role = get_role(request)
    visible = CATALOG if role == "admin" else [w for w in CATALOG if not w.admin_only]
    return [w.model_dump(exclude={"starter_command", "agent"}) for w in visible]


@router.get("/main-tagging/repos")
async def main_tagging_repos():
    """The same repo list main_tagging_release.py itself reads from --
    read directly here (not via an opencode session) purely to populate
    the run modal's repo picker (see RunWorkflowModal.tsx), so listing
    repos for the UI doesn't require starting an actual agent turn.
    """
    path = os.path.join(settings.workspace_root, "config", "tracked_repos.yaml")
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="config/tracked_repos.yaml not found -- the repo list can't be loaded right now.",
        )
    repos = []
    for r in data.get("repos", []):
        url = r.get("url")
        if not url:
            continue
        # "org/repo" is what the picker shows and what gets sent back as
        # --repos -- matches the exact format main_tagging_release.py's
        # own --repo/--repos filtering already expects.
        slug = url.rstrip("/").replace("https://github.com/", "")
        repos.append({"slug": slug, "components": r.get("components", [])})
    return repos


@router.get("/integrations")
async def integration_status():
    """Live connectivity for every MCP server any workflow depends on, so
    the dashboard can grey out / warn on workflows that would just fail
    with a confusing agent message right now. See DESIGN.md "pre-flight
    integration check".
    """
    try:
        status = await client.mcp_status()
    except Exception:
        status = {}
    needed = {mcp for w in CATALOG for mcp in w.requires_mcp}
    return {name: status.get(name, False) for name in needed}
