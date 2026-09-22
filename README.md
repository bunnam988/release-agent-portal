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

## Deploying to CNAP

`cnap-webservice.yaml` + `cnap-secret.yaml.template` are ready to use —
schema confirmed directly against `cnap.comcast.net/cnap`'s own API
reference and sample manifests, not guessed:

1. Copy `cnap-secret.yaml.template` to `cnap-secret.yaml` (gitignored —
   never commit the real one) and fill in the real values (the 4
   credential files' full contents, the portal passwords, a real random
   session secret, your git identity).
2. `kubectl apply -f cnap-secret.yaml -n corenw-att` — the `WebService`
   references these by name, so they need to exist first.
3. `kubectl apply -f cnap-webservice.yaml -n corenw-att`.

How the credentials actually get into the container: the 4 files
(`ccp_jira.env`, `gerrit.netrc`, `gh-hosts.yml`, `opencode-auth.json`) are
claimed via `serviceClaims` with `type: servicebinding.io`, which mounts
each Secret key as a file under `/bindings/creds-files/<key>` —
`docker-entrypoint.sh` copies them from there into the paths this app
actually expects, **only if** a runtime volume mount (the local
`docker-compose.yml` path) hasn't already provided a real one, so this
doesn't affect local dev at all. `PORTAL_ADMIN_PASSWORD`,
`PORTAL_SESSION_SECRET`, and the git-identity vars go straight in as env
vars via `env[].valueFrom.secretKeyRef`.

Other notes for whoever deploys this:
- Only **one** container needs a public route/ingress — there's only one
  container, period. `opencode-server` never gets its own network
  endpoint even inside the pod, so there's no internal-only-routing
  question to solve. `spec.ingress.public: true` is what actually turns
  this on (confirmed default is `false`).
- `spec.autoscale: {min: 1, max: 1}` is deliberate, not an oversight — see
  `backend/app/routers/sessions.py`'s `mutating_lock`. A second replica
  would let two mutating workflow runs collide on the same git state, and
  since opencode-server lives inside this same container, scaling this
  service scales opencode-server too, not just the stateless API/frontend
  part.
- **Still genuinely unresolved**: no dedicated persistent-volume concept
  was found for a plain `WebService` in the docs available at the time
  this was written. `/workspace/.stable2-meta-sync` needs to survive pod
  restarts (see `DESIGN.md`'s "Data Storage" table) — ask directly rather
  than assume ephemeral storage is acceptable.

## Updating the release-agent submodule

When `release-agent` gets new skill/agent changes:

```bash
cd release-agent
git pull origin main
cd ..
git add release-agent
git commit -m "Update release-agent submodule"
```
