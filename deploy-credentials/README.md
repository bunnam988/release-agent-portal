# deploy-credentials/

**Local-dev-only fallback now** -- real credentials on CNAP come from
Comcast xVault via `cnap-webservice.yaml`'s `serviceClaims` (see that
file and `DESIGN.md`'s "Credential status" section). This directory
used to be the actual production mechanism (credentials baked into the
image at build time) after an earlier plain-`Secret`/`serviceClaims`
attempt hit `APPLICATION_INVALID_SECRET_REF` -- that failure turned out
to be from binding a Kubernetes `Secret` object directly, which isn't
actually how CNAP's serviceClaims works (confirmed against CNAP's real
docs: `service.path` must be a Vault/xVault path). xVault is a
different, documented mechanism, not a retry of the same thing -- see
`cnap-secret.yaml.template`, now superseded.

Keep this directory around anyway: it's still the easiest way to run a
full local build (`docker build`, not just `docker compose up`, which
can use a runtime volume mount instead) without an xVault binding
available, and it's the last-resort fallback if a real CNAP deploy's
xVault binding is ever misconfigured.

## Priority order in `docker-entrypoint.sh`, highest to lowest

1. **Runtime volume mount** (local `docker-compose.yml`) -- always wins,
   completely unaffected by anything below.
2. **Comcast xVault**, via CNAP's `servicebinding.io` mount under
   `/bindings/release-agent-portal-files/<key>` -- the real production
   mechanism on CNAP (see `cnap-webservice.yaml`'s serviceClaims block).
3. **Baked into the image** (this directory, copied in at build time) --
   local-dev fallback only now; also what a CNAP deploy falls back to if
   its xVault binding isn't set up yet.

## Files this expects (none of them tracked in git -- see `.gitignore`)

| File to place here | xVault secret key (same value, `release-agent-portal-files` claim) | Copied to (only if neither a runtime mount nor an xVault binding already provided it) |
|---|---|---|
| `ccp_jira.env` | `ccp-jira-env` | `/workspace/ccp_jira.env` |
| `gerrit.netrc` | `gerrit-netrc` | `/root/.netrc` |
| `gh-hosts.yml` | `gh-hosts-yml` | `/root/.config/gh/hosts.yml` |
| `opencode-auth.json` | `opencode-auth-json` | `/root/.local/share/opencode/auth.json` |
| `config.env` | n/a -- separate `release-agent-portal-config` claim, `type: env`, injects these directly as real container env vars on CNAP, no file involved | Sourced directly as env vars (`PORTAL_ADMIN_PASSWORD`, `PORTAL_USER_PASSWORD`, `PORTAL_SESSION_SECRET`, `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL`, `RDKB_SAT_CLIENT_ID`, `RDKB_SAT_CLIENT_SECRET`, `GH_MESH_USER`) -- only for whichever of these aren't already set some other way |

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
