# release-agent-portal

A web UI for [`release-agent`](https://github.com/bunnam988/release-agent)
(the RDKB stable2 release-automation skills/agents) — for people who'd
rather click "Run" than use the `opencode` CLI directly. See `DESIGN.md` in
this repo for the full architecture writeup; this file covers setup,
credentials, and how to build/deploy.

## Architecture, in short

**One container**, two processes inside it:

```
Browser -> :8000 (FastAPI, serves the built React app AND the API)
              |
              `-> opencode-server (127.0.0.1:4096, never a separate network endpoint)
```

- **FastAPI** (`backend/app`) serves the built React frontend as static
  files *and* the `/api/*` routes from the same process/port — the only
  thing a browser ever talks to.
- **opencode-server** runs `opencode serve` with `release-agent`'s
  skills/agents loaded — this is where the real work happens and where
  every credential lives. It's bound to `127.0.0.1` only inside this same
  container (see `docker-entrypoint.sh`), reached exclusively via
  `localhost` from the FastAPI process — never its own network endpoint,
  even inside the container's own namespace.

This intentionally matches a proven one-container-per-app pattern already
used elsewhere on this team's CNAP setup, rather than splitting into
multiple containers that would each need their own `WebService` and
internal-only routing worked out.

## Important: this repo uses a git submodule

`release-agent` is nested here as a git submodule (`./release-agent`), not
a sibling directory — this keeps the whole repo self-contained for a
CI/CD pipeline that only checks out `release-agent-portal`. **Clone with
submodules, or the build will fail** (the Dockerfile's `COPY release-agent/
.` will find an empty directory otherwise):

```bash
git clone --recurse-submodules git@github.com:bunnam988/release-agent-portal.git
# or, if you already cloned without it:
git submodule update --init --recursive
```

You'll need access to both `bunnam988/release-agent-portal` and
`bunnam988/release-agent` (both private) for this to work — ask to be
added as a collaborator on both if you can't clone the submodule.

## Credentials

None of these are stored in this repo. See `DESIGN.md`'s "Credential
status" section for the full picture (short version: Jira is a real
service account already, GitHub/Gerrit/Copilot are running on a specific
person's own credentials as an interim measure until service accounts are
provisioned).

Set up once, inside the running container (or provide the mounted files
`docker-compose.yml` expects — see its own comments for every exact path):

1. **GitHub**: `docker exec -it release-agent-portal-app-1 gh auth login`
2. **GitHub Copilot** (model provider): `docker exec -it release-agent-portal-app-1 opencode auth login`
3. **Gerrit**: a `.netrc` file with a `machine {gerrit_host}` entry, mounted
   read-only (see `docker-compose.yml`'s Gerrit mount comment for exactly
   how to generate a filtered one from a personal `~/.netrc`)
4. **Jira**: copy `release-agent/ccp_jira.env.template` to
   `release-agent/ccp_jira.env` and fill in the real values (this is the
   `svc-autotriage` service-account token — ask whoever owns it)

## Environment variables

Set these on the host before `docker compose up` (see `docker-compose.yml`
for the full comments on each):

| Variable | Purpose | Required? |
|---|---|---|
| `PORTAL_ADMIN_PASSWORD` / `PORTAL_USER_PASSWORD` | Shared-password login, two roles (admin = everything, user = on-demand cherry-pick only) | No — blank disables auth entirely |
| `PORTAL_SESSION_SECRET` | Signs the login session cookie | Yes, if the above are set — must be a real random value |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_NAME` / `GIT_COMMITTER_EMAIL` | Git commit identity for anything the agent commits | No, but the container prints a loud startup warning if left unset (see `docker-entrypoint.sh`) |

## Running locally

```bash
docker compose up -d --build
```

Then run the credential setup steps above once. Visit `http://localhost:8000`.

`GET /api/health` reports both this process's own health and whether it
can reach `opencode-server` over localhost — wire this as the
liveness/readiness probe wherever this ends up deployed.

## Building the image for deployment

```bash
docker build -t <registry>/release-agent-portal:<tag> .
```

One image, built from the repo root (not a subdirectory) — the Dockerfile
handles building the frontend, installing the backend's Python deps, and
setting up opencode-server, all as part of the same build.

Notes for whoever deploys this to Kubernetes/CNAP:
- Only **one** container needs a public route/ingress — there's only one
  container, period. `opencode-server` never gets its own network
  endpoint even inside the pod, so there's no internal-only-routing
  question to solve.
- Needs persistent storage at `/workspace/.stable2-meta-sync` (a real PVC,
  not the local named volume `docker-compose.yml` uses) so incremental run
  state survives pod restarts — see `DESIGN.md`'s "Data Storage" table for
  the full list of paths that need to persist.
- Credentials (GitHub token, Gerrit `.netrc`, `ccp_jira.env`, Copilot auth)
  should become real k8s Secrets mounted at the same paths
  `docker-compose.yml` uses today, not baked into the image.
- Only one *mutating* workflow (anything that pushes to GitHub/Gerrit or
  writes Jira) runs at a time — see `backend/app/routers/sessions.py`'s
  `mutating_lock` — so this should stay a **single replica**. A second
  replica would let two mutating runs collide on the same git state, and
  since opencode-server lives inside this same container, scaling this
  service scales opencode-server too, not just the stateless API/frontend
  part.
- A draft `cnap-webservice.yaml` is included, based on the one real
  example of this team's `WebService` CRD usage available at the time it
  was written — several fields (secrets, volumes, replica count) are
  flagged inline as unconfirmed; treat it as a starting point to correct
  against real CNAP docs, not something to apply as-is.

## Updating the release-agent submodule

When `release-agent` gets new skill/agent changes:

```bash
cd release-agent
git pull origin main
cd ..
git add release-agent
git commit -m "Update release-agent submodule"
```
