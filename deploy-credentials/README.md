# deploy-credentials/

**The current CNAP deployment approach**, not a temporary measure anymore
-- all credentials (the 4 files, plus portal passwords/session
secret/git identity) are baked into the image at build time, mirroring a
teammate's own working CNAP deployment pattern (`jira_autotriage`). See
`DESIGN.md`'s "Credential status" section for the full trade-off
discussion (this is a real, accepted trade-off: worse than a proper CNAP
`Secret`/`serviceClaims` mechanism -- see `cnap-secret.yaml.template`,
kept as a reference for reverting later -- better than committing
credentials to git, which was also considered and rejected).

This replaced the `serviceClaims`/`servicebinding.io` approach after it
hit real friction in a live deployment attempt
(`APPLICATION_INVALID_SECRET_REF`, plus an unconfirmed mount-path
question that was never resolved before this switch).

## Priority order in `docker-entrypoint.sh`, highest to lowest

1. **Runtime volume mount** (local `docker-compose.yml`) -- always wins,
   completely unaffected by anything below.
2. **Baked into the image** (this directory, copied in at build time) --
   used for everything on CNAP.

## Files this expects (none of them tracked in git -- see `.gitignore`)

| File to place here | Copied to (only if a runtime mount didn't already provide it) |
|---|---|
| `ccp_jira.env` | `/workspace/ccp_jira.env` |
| `gerrit.netrc` | `/root/.netrc` |
| `gh-hosts.yml` | `/root/.config/gh/hosts.yml` |
| `opencode-auth.json` | `/root/.local/share/opencode/auth.json` |
| `config.env` | Sourced directly as env vars (`PORTAL_ADMIN_PASSWORD`, `PORTAL_USER_PASSWORD`, `PORTAL_SESSION_SECRET`, `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL`, `RDKB_SAT_CLIENT_ID`, `RDKB_SAT_CLIENT_SECRET`, `GH_MESH_USER`) -- only for whichever of these aren't already set some other way |

`RDKB_SAT_CLIENT_ID` / `RDKB_SAT_CLIENT_SECRET` are read by
`backend/app/llm_token_proxy.py` (see that file for the full rationale)
to fetch/refresh the Flow LLM gateway's 24h bearer token -- see
`release-agent/sat-url.txt` for the exact token-fetch command these
values came from (also gitignored, never committed).

`GH_MESH_USER` names which account stored in `gh-hosts.yml` is the Mesh
Components (`rdk-gdcs` org) GitHub identity -- a different account than
the default core-nw (`rdkcentral`) one. **`gh-hosts.yml` must contain
both accounts' tokens** (gh's own hosts.yml format supports multiple
users per host in one file -- no second credential file needed, just log
in with both accounts before generating this file). See DESIGN.md "Mesh
Components release support" for the full rationale and the `gh auth
switch` mechanism that actually uses this.

**Never commit the real files.** Rebuilding the image after placing real
files here is required for them to actually be included -- same as any
other Dockerfile change.
