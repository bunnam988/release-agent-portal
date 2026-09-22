# deploy-credentials/

**Temporary diagnostic measure, not the long-term plan.** Re-added to
isolate two separate open questions from each other:
1. Does the app actually work correctly once deployed to CNAP at all?
2. Is the `servicebinding.io` mount path guess in `docker-entrypoint.sh`
   (`/bindings/<claim>/<key>` or `$SERVICE_BINDING_ROOT/<claim>/<key>`)
   actually correct?

Baking real credentials into the image (this mechanism) answers (1)
without depending on (2) being right yet -- see `DESIGN.md`'s "Credential
status" section for the full trade-off discussion (this is a real,
accepted trade-off: worse than the `serviceClaims`/`servicebinding.io`
mechanism `cnap-webservice.yaml` already uses, better than committing
credentials to git, which was also considered and rejected).

## Priority order in `docker-entrypoint.sh`, highest to lowest

1. **Runtime volume mount** (local `docker-compose.yml`) -- always wins,
   completely unaffected by anything below.
2. **CNAP `servicebinding.io` mount** (`$SERVICE_BINDING_ROOT` or
   `/bindings`) -- used if present and step 1 didn't already provide it.
3. **Baked into the image** (this directory, copied in at build time) --
   last resort, used only if neither of the above provided a real file.

This means: once the `servicebinding.io` mount path is confirmed and
fixed (if it needs fixing), step 2 will take over automatically and this
fallback becomes dead weight -- safe to remove entirely at that point.

## Files this expects (none of them tracked in git -- see `.gitignore`)

| File to place here | Copied to (only if nothing higher-priority provided it) |
|---|---|
| `ccp_jira.env` | `/workspace/ccp_jira.env` |
| `gerrit.netrc` | `/root/.netrc` |
| `gh-hosts.yml` | `/root/.config/gh/hosts.yml` |
| `opencode-auth.json` | `/root/.local/share/opencode/auth.json` |

**Never commit the real files.** Rebuilding the image after placing real
files here is required for them to actually be included -- same as any
other Dockerfile change.
