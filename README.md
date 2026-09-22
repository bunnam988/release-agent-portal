# release-agent-portal

A web UI for [`release-agent`](https://github.com/bunnam988/release-agent)
(the RDKB stable2 release-automation skills/agents) — for people who'd
rather click "Run" than use the `opencode` CLI directly. See `DESIGN.md` in
this repo for the full architecture writeup; this file covers setup,
credentials, and how to build/deploy.

## Architecture, in short

Three containers, one Docker network:

```
Browser -> frontend (nginx, :8080) -> backend (FastAPI, :8000) -> opencode-server (:4096, internal only)
```

- **frontend** — React SPA, served as static files by nginx. The only
  thing a browser talks to.
- **backend** — thin FastAPI proxy/orchestration layer. Never touches
  Jira/GitHub/Gerrit itself.
- **opencode-server** — runs `opencode serve` with `release-agent`'s
  skills/agents loaded; this is where the real work happens and where all
  credentials live.

## Important: this repo uses a git submodule

`release-agent` is nested here as a git submodule (`./release-agent`), not
a sibling directory — this keeps the whole repo self-contained for a
CI/CD pipeline that only checks out `release-agent-portal`. **Clone with
submodules, or the `opencode-server` build will fail** (its Dockerfile
build context is `./release-agent`, which will be an empty directory
otherwise):

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

Set up once, inside the running `opencode-server` container (or provide
the mounted files docker-compose.yml expects — see its own comments for
every exact path):

1. **GitHub**: `docker exec -it <opencode-server container> gh auth login`
2. **GitHub Copilot** (model provider): `docker exec -it <opencode-server container> opencode auth login`
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
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_NAME` / `GIT_COMMITTER_EMAIL` | Git commit identity for anything the agent commits | No, but the container prints a loud startup warning if left unset (see `release-agent/docker-entrypoint.sh`) |

## Running locally

```bash
docker compose up -d --build
```

Then `docker exec -it release-agent-portal-opencode-server-1 gh auth login`
(and the Copilot/Jira/Gerrit steps above) once. Visit `http://localhost:8080`.

`GET /api/health` on the backend (`:8000`) reports both the backend's own
health and whether it can reach `opencode-server` — wire this as the
liveness/readiness probe wherever this ends up deployed.

## Building images for deployment

Each service builds independently — there's no top-level image, just the
three from `docker-compose.yml`:

```bash
docker build -t <registry>/release-agent-opencode-server:<tag> ./release-agent
docker build -t <registry>/release-agent-backend:<tag> ./backend
docker build -t <registry>/release-agent-frontend:<tag> ./frontend
```

Notes for whoever deploys this to Kubernetes/CNAP:
- `opencode-server` is **not** meant to be publicly routable — only
  `backend` should ever reach it (see the architecture diagram above).
  `backend` is the only service that needs external ingress (fronted by
  `frontend`, or `frontend` proxies to it — see `frontend/nginx.conf`).
- `opencode-server` needs persistent storage at `/workspace/.stable2-meta-sync`
  (a real PVC, not the local named volume `docker-compose.yml` uses) so
  incremental run state survives pod restarts — see `DESIGN.md`'s "Data
  Storage" table for the full list of paths that need to persist.
- Credentials (GitHub token, Gerrit `.netrc`, `ccp_jira.env`, Copilot auth)
  should become real k8s Secrets mounted at the same paths
  `docker-compose.yml` uses today, not baked into any image.
- Only one *mutating* workflow (anything that pushes to GitHub/Gerrit or
  writes Jira) runs at a time — see `backend/app/routers/sessions.py`'s
  `mutating_lock` — so a single `opencode-server` replica is the right
  starting point; don't scale it horizontally without addressing that
  first.

## Updating the release-agent submodule

When `release-agent` gets new skill/agent changes:

```bash
cd release-agent
git pull origin main
cd ..
git add release-agent
git commit -m "Update release-agent submodule"
```
