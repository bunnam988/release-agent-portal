# Design: Hosting the release-agent on CNAP with a web UI

## Context

`release-agent` is a folder of `SKILL.md` / `AGENT.md` files executed today by
engineers running the real `opencode` CLI on their laptop. Skills call out to
Jira (via Comcast's Flow MCP gateway, per-user OAuth), GitHub, and Gerrit
(`~/.netrc`), and read/write local state files (`*.yaml`, `pr_list.yaml`,
`stable2_session_state.yaml`, a git worktree under `.stable2-meta-sync/`,
etc.). The workflows are conversational: `AGENT.md` orchestrators print a
report and a `Proceed to Phase N? [Y/n]` prompt, wait for the next chat
message, and continue — there is no separate "confirm" tool call to hook into.

Goal: run this the same way, but headless on a CNAP container, driven from a
web UI instead of a terminal.

### Findings from investigation (this session)

- Real `opencode` ships a headless server: `opencode serve` exposes a full
  REST + SSE API (`GET /doc` for the OpenAPI spec) — sessions, prompts,
  streaming events, permission/question requests, skill/agent/command
  listing. This is the integration point; we do not need to reimplement the
  agent loop.
- **Bug fixed in this session:** opencode only auto-loads project skills from
  `.opencode/skills/` and project agents from `.opencode/agent/<name>.md`
  (flat files). This repo's skills lived in `.agents/skills/*/SKILL.md`
  (fixed via `opencode.json` → `skills.paths: [".agents/skills"]`, no file
  moves needed) and its two orchestrators lived in
  `.agents/agents/<name>/AGENT.md` (a shape opencode's agent loader never
  scanned — **these were not loading in real opencode at all**). Moved to
  `.opencode/agent/<name>.md`, added `mode: primary`, and fixed the
  `../../../config/config.yaml` reference (which assumed path resolution
  relative to the agent file, wrong at the new, shallower path) to the
  root-relative `config/config.yaml`, matching how every other path in these
  files is written (e.g. `scripts/cherry_pick_to_stable2.py`).
- Skills still use the same root-relative convention (`../../../config/...`
  from 3 levels deep) — untouched, since they weren't moved and the model is
  shown each skill's absolute `location` alongside its content, so it can
  resolve those correctly.

### Local end-to-end verification (follow-up session)

Ran the full stack locally (`docker compose up`) and drove a real workflow
(`stable2-candidates`) through it end-to-end — UI → backend → opencode →
GitHub Copilot model → permission gate → question gate → clean stop. Found
and fixed three more issues along the way:

- **opencode 1.18.7's newer `/api/session/*` API ("v2"/"next") is broken for
  the `github-copilot` provider**: every prompt call failed with
  `400 bad request: missing required Authorization header`, reproduced
  consistently in a clean container with a freshly-issued token (so it's not
  a stale-credential problem). The **legacy `/session/*` API works
  correctly** and returns real model output. `backend/app/opencode_client.py`
  now talks to the legacy endpoints exclusively; re-check this on every
  opencode upgrade in case it's fixed upstream, since the legacy API could be
  deprecated later.
- **Legacy `POST /session/{id}/message` blocks until the whole agent turn
  finishes** (unlike the v2 API's fire-and-forget `/prompt`), which for a
  real skill run can be minutes. `routers/sessions.py` now fires it via
  `asyncio.create_task` (fire-and-forget) so `POST /api/sessions` returns
  immediately; the caller watches progress over `/events`.
- **A second, distinct gate type exists**: opencode's `question` tool (a
  structured multi-choice prompt — e.g. "which Jira label should I use?"),
  separate from the `bash`/`edit` permission-ask gate. Added
  `GET/POST/DELETE /api/sessions/{id}/questions` and matching UI
  (`SessionView.tsx` `QuestionPanel`) alongside the existing permission
  Allow/Deny UI.

**Model provider auth is per-container, not just per-CNAP-account.** Even
locally, a container with no opencode credentials falls back to opencode's
own blocked free-tier model. Getting a real response required running
`opencode providers login -p github-copilot` *inside* the container once
(device-flow: visit a URL, enter a code) — copying a host's `auth.json` in
did not work (Copilot's token exchange needs to rewrite that file, so it
must be mounted read-write; a read-only copy also produced a different,
harder-to-diagnose hang with no error at all). `docker-compose.yml` mounts a
named volume (`opencode-auth`) at `/root/.local/share/opencode` so the login
persists across container restarts locally. **This is the same class of
problem as the Jira Flow OAuth blocker below, one level down the stack**:
CNAP will need either a real login done once against the deployed container,
or an org-level non-interactive Copilot credential — ask whoever administers
Comcast's GitHub Copilot/CNAP integration which is available.

**Confirmed working correctly end-to-end**, including the skill's own safety
behavior: with Copilot authenticated but Jira MCP *not yet* configured in the
container, the model read `config/config.yaml`, asked a real permission
question, asked a real multi-choice question (candidate label), then
correctly detected Jira MCP was unavailable and **stopped itself** with the
exact remediation step (`opencode mcp auth jira-ccp`) instead of guessing or
silently failing. This is the skill's own `SKILL.md` logic working as
designed, not something we built — a good sign the skills are robust when
actually exercised.

### Full real run with Jira MCP connected (second follow-up session)

Once the developer had a working `jira-ccp` Flow OAuth connection on their
laptop, reproduced the same trick used for Copilot: `opencode`'s MCP OAuth
tokens live in a separate file, `~/.local/share/opencode/mcp-auth.json` (not
`auth.json`), keyed by server name with a `tokens` field once fully
authenticated (servers still mid-flow only have `clientInfo`/`codeVerifier`/
`oauthState`). Two things were needed for the container to use it:

1. **`release-agent/opencode.json` had no `mcp` section at all** — the
   container doesn't inherit the developer's global
   `~/.config/opencode/opencode.json`, so `jira-ccp` needs to be declared in
   the *project* config too (added, matching the global config's `type:
   "remote"` / Flow gateway URL / `oauth: {}` shape).
2. **`docker-compose.yml` mounts the host's `mcp-auth.json` read-write** into
   the container (same reasoning as the Copilot `auth.json` mount: local
   testing convenience only, real deployment needs its own Flow-issued
   service credential — see the blocker list below).

With both in place, `GET /mcp` inside the container reported
`{"jira-ccp":{"status":"connected"}}`, and a full `/stable2-candidates` run
through the actual UI backend path executed a **real** `jira_search` JQL
query, fetched 112 tickets (paginating past the 100-result API cap on its
own), filtered 54 already-`*_considered`, and wrote a correct
`stable2_candidates.yaml` with the remaining 58 — identical in shape to what
running this from the CLI would produce. Approvals along the way (multiple
`bash` permission asks, one `question` ask for the candidate label, one
`question` ask to confirm saving) were all handled through
`/api/sessions/{id}/permissions` and `/api/sessions/{id}/questions`, i.e.
through code we wrote, not manual intervention.

**Gap found and fixed while watching this run**: the container had no
`python3` or `gh` (GitHub CLI). Several skills shell out to real Python
scripts (`scripts/cherry_pick_to_stable2.py`, which needs `PyYAML`;
`jira-pr-lookup/scripts/fetch_prs.py`) and to `gh api`/`gh pr`/`gh release`
(`stable2-github-release-tagger`, `stable2-pr-labeler`, `jira-pr-lookup`) —
these are real dependencies the skills assume exist, not just
LLM-in-context logic. Without `python3`, the model improvised working
`node -e` one-liners instead (functionally fine for JSON munging, but not
what the skills are written to do, and would silently diverge for anything
`cherry_pick_to_stable2.py` actually needs to do like multi-repo git
cloning). `release-agent/Dockerfile` now installs `python3` + `python3-yaml`
+ `gh` (via GitHub's official apt repo). Re-run any skill that shells out to
a script after this kind of environment change to make sure nothing else is
silently missing — this was found by reading the actual tool-call transcript
of a real run, not by inspecting the Dockerfile in isolation.

### UX pass after watching real users try it (third follow-up session)

Three problems surfaced from actually clicking through the UI, not from
reading code:

**1. Permission-ask noise.** The original `release-agent/opencode.json` set
`bash: "ask"` for literally every shell command, so a single skill run
produced 5-10+ Allow/Deny prompts for routine, harmless commands (`cat
config.yaml`, `find`). Changed to `bash: "*": "allow"` with only a short
deny/ask list for genuinely destructive patterns (`rm -rf *`, `git push
--force*` → ask; `sudo *` → deny). The `question` tool gates (candidate
label, confirm save, etc.) are untouched — those are the skills' own
intentional decision points and are the only interruptions a user sees now.

**2. Consistent output shape.** `SessionView.tsx` now always shows the same
three things in the same place, regardless of which skill ran: a status
pill (Running / Waiting for your input / ✓ Complete), a prominent "Output"
box showing the latest assistant text (updates live), and a collapsible
"Activity log" for the tool-call/reasoning trace (open while running,
available but out of the way once done).

**3. "What do I do next?"** Detected completion via opencode's
`session.status`/`session.idle` events (busy → idle, no pending
permission/question) and added a green "Run complete" banner with a
**"Run next: `<workflow>`"** button, driven by a new `next_workflow_id`
field on each `Workflow` in `backend/app/workflows.py`, matching the
documented pipeline order (`track-for-stable2` → `stable2-candidates` →
`stable2-status-evaluator` → `jira-pr-lookup` → `stable2-ready-considered-labeler`
→ `stable2-github-release-tagger` → `stable2-srcrev-updater` →
`gerrit-cherrypick-squash`).

**4. Bigger problem found while testing (2) and (3): a hard dead end.**
Jira MCP briefly failed (`SSE error: Non-200 status code (502)` — traced to
the host's own `opencode` process holding a concurrent connection on the
same shared OAuth token; see below). The skill correctly detected this and
stopped, but its remediation text — "run `opencode mcp auth jira-ccp` then
restart OpenCode" — is written for a developer at a CLI, not someone using
the hosted UI, who has no way to act on it. The user then tried replying
"check now" / "Y" into the chat box, and the agent just repeated the same
unhelpful instructions, because from the agent's point of view nothing had
changed. **A "make the chat log look nicer" fix would not have solved
this** — the real fix is to never let the run start at all when a required
integration is down:

- `Workflow.requires_mcp: list[str]` marks which workflows need which MCP
  server (all the Jira-touching ones list `"jira-ccp"`).
- `OpencodeClient.mcp_status()` checks opencode's real `GET /mcp` status.
- `POST /api/sessions` now checks `requires_mcp` against live status
  *before* creating a session, returning `503` with one clear,
  portal-native message ("Jira is currently unavailable, so this workflow
  can't run right now...") instead of ever invoking the agent.
- `GET /api/workflows/integrations` exposes the same status so the
  dashboard can grey out affected workflow cards *before* the user even
  clicks Run, with a plain-language warning instead of raw MCP server ids.
- The frontend's `request()` helper now surfaces FastAPI's `detail` field
  directly (instead of a raw status/body dump), which is the same channel
  this clean error travels through.

**Root cause of the 502, for the record:** `ps aux` on the host showed the
developer's own `opencode` process (their personal interactive session)
still running and holding a connection on the exact same `jira-ccp` OAuth
token mounted into the container. Comcast's Flow gateway appears to reject
a second concurrent SSE session on one token. This is an artifact of the
local-testing shortcut (sharing one human's token between host and
container) and resolved on its own once contention cleared — it is **not**
expected to recur once CNAP has its own dedicated service credential (see
the blocker list above), but the pre-flight check now means that even a
future transient outage fails cleanly instead of producing a dead-end
conversation.

## Goals / Non-Goals

**Goals:**

- Run `opencode serve` headless in a container on CNAP with this repo's
  skills/agents loaded, using a shared service-account identity for
  Jira/GitHub/Gerrit.
- Thin FastAPI backend that turns "click a workflow, answer its prompts" into
  a small set of REST + SSE endpoints, so the frontend never talks to the raw
  opencode API or holds its credentials.
- React SPA that lists the release workflows, starts a run, and renders it as
  a chat transcript (assistant output streamed live, quick Yes/No + free-text
  reply), because the existing `AGENT.md`/`SKILL.md` confirmation gates are
  conversational text, not a structured tool call — trying to regex-parse
  `[Y/n]` out of markdown and turn it into a bespoke wizard is fragile and
  unnecessary when a minimal chat UI models the exact same interaction the
  CLI already has.
- Persist workspace state (`*.yaml`, `.stable2-meta-sync/` worktree,
  `last_run.txt`) across container restarts.
- Give the humans using the UI an identity for audit purposes even though the
  underlying git/Jira/GitHub actions run as one bot account.

**Non-Goals (this phase):**

- Per-user delegated credentials (decided: shared service account for now).
- Rewriting the skills/scripts themselves — only the hosting/invocation layer
  changes.
- Multi-tenant support for other teams' agents — this is scoped to
  `release-agent` only.
- Actually creating CNAP namespaces, secrets, or network policies — I don't
  have access to Comcast's internal CNAP tooling; the checklist below is what
  someone with that access needs to do.

## Decisions

### D1: opencode as the execution engine, not a reimplementation

**Decision:** Run the real `opencode` binary in server mode
(`opencode serve --hostname 0.0.0.0 --port 4096`) inside the container, with
this repo as its working directory. All Jira/GitHub/Gerrit/file logic stays
exactly as written in the `SKILL.md`/`AGENT.md` files and the Python scripts
they call — nothing is ported to a "real" backend.

**Alternative considered:** Reimplement each skill's logic as a plain FastAPI
endpoint (Decision option B from the earlier scoping questions). Rejected —
it would mean re-deriving Jira dependency traversal, Gerrit topic/squash
logic, and the multi-phase confirmation flow that the LLM currently drives
via reasoning over tool output, none of which is simple deterministic code.
It also throws away the ability to keep improving these workflows by editing
markdown, which is presumably why they were built this way.

**Trade-off:** Every UI-triggered action still goes through an LLM call
(cost, latency, and a small amount of non-determinism vs. a hand-written
script). Acceptable — that's already true today from the CLI.

---

### D2: Backend is a thin proxy + curated workflow catalog, not a generic opencode client

**Decision:** `backend/app/workflows.py` defines a static catalog of the
runnable entry points, since the raw `/api/skill` and `/api/agent` listing
mixes in opencode's built-ins (`build`, `plan`, `general`, global skills from
`~/.config/opencode/skills`) that must never be exposed to release-agent
users:

```python
# release-agent-portal/backend/app/workflows.py
from pydantic import BaseModel


class WorkflowArg(BaseModel):
    name: str  # e.g. "dry_run"
    flag: str  # e.g. "--dry-run"
    label: str
    kind: str  # "flag" | "text"
    default: str | None = None


class Workflow(BaseModel):
    id: str
    label: str
    description: str
    agent: str | None = None  # opencode agent to select for this session, if any
    starter_command: str  # e.g. "/main-tagging"
    args: list[WorkflowArg] = []


CATALOG: list[Workflow] = [
    Workflow(
        id="main-tagging",
        label="Main Tagging (biweekly scan)",
        description="Scan RDK component repos since last run, update the "
        "open-source commits monitor, and init the release session.",
        starter_command="/main-tagging",
    ),
    Workflow(
        id="track-for-stable2",
        label="Track for stable2",
        description="Discover newly merged develop commits and label their "
        "Jira tickets track_for_stable2.",
        starter_command="/track-for-stable2",
        args=[
            WorkflowArg(name="dry_run", flag="--dry-run", label="Dry run", kind="flag"),
            WorkflowArg(name="test_repo", flag="--test-repo", label="Test repos only", kind="flag"),
        ],
    ),
    Workflow(
        id="stable2-candidates",
        label="stable2 candidates",
        description="Filter tickets tracked for stable2 but not yet considered.",
        starter_command="/stable2-candidates",
        args=[
            WorkflowArg(name="candidate_label", flag="--candidate-label", label="Candidate label", kind="text"),
        ],
    ),
    # ... one entry per skill in .agents/skills, plus:
    Workflow(
        id="stable2-release-orchestrator",
        label="Full stable2 release orchestration",
        description="Runs the 5-phase stable2 workflow end-to-end with "
        "confirmation gates between phases.",
        agent="stable2-release-orchestrator",
        starter_command="Start the stable2 release orchestration.",
        args=[
            WorkflowArg(name="dry_run", flag="--dry-run", label="Dry run", kind="flag"),
            WorkflowArg(name="test_repo", flag="--test-repo", label="Test repos only", kind="flag"),
        ],
    ),
]
```

The backend composes the initial chat message from `starter_command` + the
args the user picked in the UI (e.g. `/track-for-stable2 --dry-run`), exactly
what an engineer would type at the opencode TUI today.

**Alternative considered:** Let the frontend call `/api/skill` /`/api/agent`
directly and build the catalog client-side. Rejected — couples the UI to
opencode's full surface area (including unrelated global skills/agents),
and means opencode credentials would need to be reachable from the browser.

---

### D3: Session interaction model — proxy chat, not a bespoke wizard

**Decision:** Each workflow run is one opencode session. The backend exposes
`POST /api/sessions` (start), `POST /api/sessions/{id}/reply` (free-text
follow-up), `GET /api/sessions/{id}/events` (SSE transcript), plus **two
separate gate types** discovered by actually running a skill end-to-end (see
"Local end-to-end verification" above) — `GET/POST /api/sessions/{id}/permissions/*`
for tool-level bash/edit asks, and `GET/POST/DELETE /api/sessions/{id}/questions/*`
for opencode's structured multi-choice `question` tool (e.g. "which Jira
label should I use?"), which several skills use for the exact "ask user to
choose 1/2/3" steps their `SKILL.md` describes. See
`backend/app/routers/sessions.py` and `backend/app/opencode_client.py` for
the current implementation (uses opencode's legacy `/session/*` API, not the
newer `/api/session/*` one — see the auth bug noted above).

The frontend renders the streamed assistant text as a chat transcript
(`SessionView.tsx`) with a free-text reply box plus quick "Yes"/"No" buttons
(which just send `"Y"` / `"n"` as the next message — the same thing a human
types at the CLI), a permission Allow/Deny panel, and a question panel with
radio-button options per question, submitted via "Submit answer".

**Rationale:** This mirrors the CLI experience 1:1, so the markdown workflows
need zero changes to run under the UI.

---

### D4: opencode server auth and network exposure

**Updated (later session): collapsed from 3 containers to 1.** Originally
this ran as 3 separate containers (`opencode-server`, `backend`, `frontend`
via nginx), with `opencode-server` bound to `0.0.0.0` and reachable only
over the internal Docker/pod network. That's still *safe* in principle, but
checking a teammate's own working CNAP deployment (a different project,
`jira_autotriage`) showed this team's proven, actually-deployed pattern is
one container per app — their FastAPI backend serves the built React
frontend directly as static files, no separate nginx container at all.
Splitting into 3 containers would have meant 2 of them (`backend`,
`opencode-server`) needing to stay internal-only with no public route, a
pattern that specific CRD (`cnap.comcast.net/v1 WebService`) hasn't been
confirmed to support — real, unresolved unknowns for something holding
every credential this system has.

**Decision now:** one container. `opencode serve` binds to `127.0.0.1` only
(not `0.0.0.0`) — it is not just kept off the public internet, it's not a
network endpoint at all beyond `localhost` inside this single container,
reached exclusively by the FastAPI process in the same container (see
`docker-entrypoint.sh`, which starts both processes and exits the whole
container if either one dies, so a k8s liveness probe restarts cleanly
rather than the pod running half-broken). FastAPI also now serves the built
frontend directly (`backend/app/main.py`'s static-file block), mirroring
that same proven pattern exactly. `OPENCODE_SERVER_PASSWORD` is left as a
no-op knob (harmless to set, unnecessary now that there's no separate
network hop to protect).

**Alternative considered:** Expose opencode's built-in `opencode web` UI
directly. Rejected — it's a generic chat client with no workflow catalog,
Jira/Gerrit-specific safety rails, or scoping to release-agent's skills; it
would also need per-user opencode credentials, which conflicts with the
shared-service-account decision.

**Alternative considered (this session):** Keep the 3-container split and
resolve the internal-only-routing question with CNAP support directly.
Viable, but collapsing to 1 container removes the question entirely rather
than needing an answer to it, and matches a pattern already proven to work
on this exact CNAP setup — lower risk for getting a first real deployment
out. Revisit if `opencode-server`'s resource needs ever outgrow what makes
sense bundled with the API/frontend process.

## Data Storage

No database in phase 1. State is filesystem-based, matching the existing
tool:

| Path (inside the single container) | Contents | Persistence need |
| --- | --- | --- |
| `/workspace` (= the `release-agent` submodule) | skill/agent markdown, scripts, config | baked into the image; redeployed on release |
| `/workspace/*.yaml`, `*_state.yaml`, `last_run.txt` | incremental run state (e.g. `track_for_stable2_state.yaml` bootstraps from latest tag, then only diffs) | **must** survive restarts — mount a CNAP-provided PVC over these paths |
| `/workspace/.stable2-meta-sync/meta-rdk-broadband` | git worktree used for `.inc` file prep | same PVC |
| `~/.netrc`, `ccp_jira.env`, GitHub token | credentials | CNAP secrets, mounted read-only, never baked into the image or committed |
| `~/.local/share/opencode/auth.json` | GitHub Copilot OAuth token | CNAP secret or one-time login against the deployed container — read-write, see "model provider auth" blocker |

Jira no longer needs an entry here — it moved off the Flow OAuth
(`mcp-auth.json`) path entirely, onto `ccp_jira.env`'s plain service-account
REST token (see "Credential status" below), which is already covered by
the `ccp_jira.env` row above.

The image also needs `python3` + `python3-yaml` and `gh` (GitHub CLI)
installed — confirmed required by actually running skills that shell out to
`scripts/cherry_pick_to_stable2.py` and `gh api`/`gh pr`/`gh release`, not
just LLM reasoning. Both are in the root `Dockerfile` (the single-container
build now installs everything release-agent needs directly, rather than
release-agent's own Dockerfile being built separately).

The backend itself is stateless; if we later want run history beyond what
opencode's own `/api/session` list already provides (audit log of *which UI
user* triggered *which* workflow — opencode only knows about the bot
identity), add a small `runs` table (Postgres, or even SQLite on the same PVC
for phase 1) keyed by opencode `session_id`.

## Data Structures

```python
# release-agent-portal/backend/app/schemas.py
from pydantic import BaseModel


class WorkflowSummary(BaseModel):
    id: str
    label: str
    description: str
    args: list[dict]


class StartSessionRequest(BaseModel):
    workflow_id: str
    args: dict[str, str | bool] = {}


class StartSessionResponse(BaseModel):
    session_id: str


class ReplyRequest(BaseModel):
    text: str


class PermissionDecision(BaseModel):
    allow: bool
```

## Interfaces

### REST APIs (backend, consumed by the frontend)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/workflows` | List the curated catalog (id, label, description, args) |
| POST | `/api/sessions` | Start a workflow run → `{session_id}` |
| POST | `/api/sessions/{id}/reply` | Send a follow-up chat message (Y/n, label choice, free text) |
| GET | `/api/sessions/{id}/events` | SSE stream of assistant output for that session |
| GET | `/api/sessions/{id}/permissions` | List pending tool-permission requests |
| POST | `/api/sessions/{id}/permissions/{request_id}` | Allow/deny a pending tool permission |
| GET | `/api/sessions` | List past/active runs (proxied from opencode, filtered to this app's sessions) |

### CLI (unchanged)

Engineers can still run `opencode` locally against this same repo exactly as
before — the `opencode.json`/`.opencode/agent` fix in this session makes the
skills *and* the two orchestrators load correctly from the CLI too.

## Implementation Detail

See `backend/app/` (FastAPI, `opencode_client.py` wraps the opencode HTTP/SSE
API) and `frontend/` (React + Vite: `Dashboard` lists workflows and starts
runs, `SessionView` renders the chat transcript + permission prompts) added
in this repo. Both are scaffolds sized for a first working vertical slice, not
a finished product.

## Migrations

Rollout order:

1. **Local:** land the `opencode.json` / `.opencode/agent` fix (done),
   confirm `opencode run --agent stable2-release-orchestrator` and
   `opencode run "/track-for-stable2"` work from a laptop against real
   credentials. Done.
2. **Container packaging:** build the `release-agent` image
   (`opencode serve`), confirm health check + skill/agent listing over HTTP
   locally via `docker compose up`. Done.
3. **Interim deployment on personal credentials (current phase):** deploy
   with real, working credentials for every integration, but sourced from a
   specific person's own accounts rather than dedicated service accounts —
   see "Credential status" immediately below. Good enough for a real demo
   and even genuine production use in the near term; the explicit plan is to
   swap each one out for a real service account once provisioned, with no
   code changes required to do so (every credential is env-var/volume-mount
   based already — see "Swapping to service accounts later" below).
4. **Service-account credentials (when available):** swap Gerrit, GitHub,
   and GitHub Copilot over from personal to dedicated service-account
   credentials. Jira is already done — see below.
5. **UI hardening**: SSO in front of the frontend is still deliberately
   deferred (your own earlier call) — shared-password + role-based access
   (admin/user) is in place instead. Revisit whether SSO is needed before
   this goes further than an internal demo/early-production audience.

### Credential status (current, not aspirational)

| Integration | Status | Detail |
|---|---|---|
| Jira | **Real service account, done** | `svc-autotriage`, via `ccp_jira.env` + `scripts/jira_rest.py` — verified with real `EDIT_ISSUES`/`LINK_ISSUE`/`CREATE_ISSUES` permissions. Not personal, not MCP. No further work needed here. |
| GitHub | **Interim: personal** | `gh auth login` done once against the deployed container (token in the `gh-auth` volume). Real PRs/releases are created as this person, not a bot. |
| Gerrit | **Interim: personal** | HTTPS credential in a mounted `.netrc`. Real pushes/cherry-picks are authenticated as this person. |
| GitHub Copilot (model provider) | **Interim: personal** | `opencode providers login` done once against the deployed container (token in the `opencode-auth` volume). |

### Swapping to service accounts later (no code changes needed)

Every credential above is wired through an env var or a volume mount, never
hardcoded — swapping to a real service account is purely an operational
step, not a code change:
- **Gerrit**: replace the mounted `.netrc` with one containing the service
  account's HTTPS credential instead of a person's.
- **GitHub**: run `gh auth login` inside the container again with the
  service account (or provision a GitHub App install instead of a personal
  token, which is the better long-term answer for org-repo access).
- **GitHub Copilot**: run `opencode providers login` again with whatever
  non-interactive credential Comcast's Copilot/CNAP administrators provide
  (still an open question on their side — a personal login remains the
  fallback until then).
- **Git commit identity**: set `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`/
  `GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` (see `docker-compose.yml`) to
  the service account's identity instead of whichever person's name is
  there for the interim deployment — `docker-entrypoint.sh` prints a loud
  warning on startup if these are ever left at the unconfigured placeholder,
  but won't warn about a *person's* name being there instead of a bot's,
  since that's the intended interim state right now.

### Open items still outside code (CNAP/infra-owner's call, not mine)

- Whoever handles the actual deployment target (namespace, ingress/TLS,
  secrets/PVC provisioning, egress allowlist to `gerrit.teamccp.com`,
  `github.com`) — explicitly not something I'm tracking further; deployment
  itself is being handled outside this repo's scope.
- Whether/when Comcast provisions real Gerrit/GitHub service-account
  credentials and a non-interactive Copilot credential — still open, tracked
  above as the eventual swap-out target.

## Testing Philosophy

Updated after the follow-up session: with a real GitHub Copilot login inside
the `opencode-server` container, we ran `stable2-candidates` through the
actual UI code path (backend `/api/sessions` → opencode → real model) and it
worked correctly end-to-end, including both gate types (permission and
question) and the skill's own safety stop when Jira MCP wasn't configured
(that skill has since been migrated off MCP entirely — see "Credential
status" above). So the model-reasoning and UI-plumbing layers are now
verified against real model output, not just a trivial prompt.

Update: Jira is no longer untestable — `svc-autotriage`'s real permissions
were verified live (`EDIT_ISSUES`/`LINK_ISSUE`/`CREATE_ISSUES` all `true`),
and every Jira-touching skill now goes through `scripts/jira_rest.py`
against that real credential, not a mock. GitHub and Gerrit are testable
too, wherever personal credentials are mounted in (see "Credential status"
above) — real PRs, releases, and Gerrit changes were exercised end-to-end
during this project (see the main-tagging incident writeup elsewhere in
this history for a concrete example of exactly that).

## Documentation Plan

`release-agent-portal/DESIGN.md` (this file) is the source of truth for the
architecture. `release-agent/opencode.json` and `.opencode/agent/*.md` are
self-documenting via the fix already made. The standalone `release-agent`
repo (published separately at `github.com/bunnam988/release-agent`) has its
own `README.md` covering setup and the skill/agent catalog.

Actual deployment (target environment, ingress, secrets provisioning) is
being handled outside this repo's scope — not something tracked further
here. `docker-compose.yml`'s own comments are the source of truth for which
env vars/mounts a deployer needs to set, and call out clearly which ones
are still personal-credential interim values vs. real service-account-ready
mechanisms.

## Risks / Trade-offs

**Risk:** LLM-driven orchestration is non-deterministic — the same
`/track-for-stable2` run could occasionally behave slightly differently
between two invocations (e.g. slightly different summary wording, or in rare
cases missing an edge case a hand-written script wouldn't).
**Mitigation:** The underlying skills already encode explicit rules and
confirmation gates specifically to constrain this; the UI adds nothing that
changes that risk profile relative to today's CLI usage. If a specific
workflow's step is fully deterministic and safety-critical (e.g. the actual
git cherry-pick), it already lives in a plain Python script the skill calls
into, not in free-form LLM reasoning.

**Risk:** Shared service account means the UI is the only audit trail of
*who* triggered a Gerrit push or GitHub release.
**Mitigation:** Backend must log `{ui_user, workflow_id, session_id,
timestamp}` for every `/api/sessions` POST before step 5 (SSO) ships to
production; don't let this workflow go live for the mutating skills
(`gerrit-cherrypick-squash`, `stable2-github-release-tagger`) until that's in
place.

**Risk:** One opencode server handling concurrent runs that touch the same
git worktree (`.stable2-meta-sync/meta-rdk-broadband`) or shared session-state
YAML files could race.
**Mitigation:** Backend should serialize workflow starts that touch shared
state (simple approach: one Python `asyncio.Lock`, reject a new run with
"a stable2 workflow is already in progress" while one is active) — see
`backend/app/opencode_client.py` `TODO` for where to add this before
production use.
