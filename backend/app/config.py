"""Runtime configuration for the release-agent-portal backend.

All values are read from the environment so the same image can be deployed
against a local opencode server (docker compose) or the CNAP-hosted one
without a rebuild.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Default matches the single-container deployment (opencode-server
    # and this backend run as two processes in the same container, see
    # Dockerfile + docker-entrypoint.sh) -- override with
    # PORTAL_OPENCODE_URL if ever split back into separate containers.
    opencode_url: str = "http://localhost:4096"
    opencode_username: str = "opencode"
    opencode_password: str = ""

    # Comma-separated list of origins allowed to call this API (the frontend).
    cors_origins: str = "http://localhost:5173"

    # Where the release-agent submodule's own files live -- same container
    # as opencode-server (see docker-entrypoint.sh's `cd /workspace`), read
    # directly here only for UI concerns that need to list something from
    # release-agent's own config (e.g. GET /api/workflows/main-tagging/repos
    # reading config/tracked_repos.yaml for the repo picker) rather than
    # driving a whole opencode session just to list a static file's contents.
    workspace_root: str = "/workspace"

    # provider/model to explicitly request on every session start and
    # every message send (see opencode_client.py -- opencode's legacy
    # /session API does NOT reliably fall back to opencode.json's
    # top-level "model" default; confirmed by a real session coming back
    # on github-copilot/claude-sonnet-4.6 despite that not being the
    # configured default at all, exactly the same class of bug already
    # found and worked around for `agent`). Must match a provider/model
    # actually defined in release-agent/opencode.json's `provider` block.
    model: str = "rdkb-release-agent/claude-4-6-sonnet"

    # Two shared passwords gating the portal, one per role -- still not
    # per-user (the "enter your name" flow handles attribution on top of
    # this), just two shared secrets instead of one. Whichever one is
    # submitted at login determines the role for that session (see
    # routers/auth.py). Both blank = auth disabled entirely, matching the
    # existing opencode_password "blank for local testing" convention.
    # Only admin_password set = a "user" role login is simply never
    # possible, which is fine.
    # NOTE: field names deliberately don't start with "portal_" because
    # Config.env_prefix below already prepends "PORTAL_" -- naming a
    # field "portal_admin_password" would require an env var of
    # PORTAL_PORTAL_ADMIN_PASSWORD, not PORTAL_ADMIN_PASSWORD, which is
    # exactly the bug this comment is here to stop someone (including
    # future me) from reintroducing.
    admin_password: str = ""
    user_password: str = ""
    # Signs the session cookie issued after a successful login (see
    # routers/auth.py). Must be set to a real random value before this is
    # used anywhere reachable beyond a laptop -- an empty/default secret
    # would let anyone forge a valid session cookie.
    session_secret: str = "dev-only-insecure-secret-override-in-production"

    class Config:
        env_prefix = "PORTAL_"


settings = Settings()
