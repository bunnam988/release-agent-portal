# release-agent

Opencode skills and agents that automate the RDK Broadband (RDKB) biweekly
release cycle: tracking merged PRs against Jira, evaluating stable2
cherry-pick readiness, tagging GitHub releases, updating `generic-srcrev.inc`
/ `generic-pkgrev.inc`, and cherry-picking the corresponding Gerrit changes
under a shared topic.

This is **not application code you run directly** — it's a set of
instructions (`SKILL.md` / `AGENT.md` files, written in natural language +
example commands) that an [opencode](https://opencode.ai) agent reads and
executes step by step, with real tool access (bash, git, `gh`, Jira REST).
The Python scripts under `scripts/` are the one exception — real code the
skills shell out to for git/GitHub/Jira operations, not just LLM reasoning.

There's a companion web UI (`release-agent-portal`, a separate repo) that
wraps this project in a FastAPI backend + React frontend for people who don't
want to run the `opencode` CLI directly. This repo works standalone via the
CLI too — the portal is optional.

## How it fits together

```
you (CLI) ──▶ opencode ──▶ reads .agents/skills/*, .opencode/agent/*
                              │
                              ├─▶ shells out to scripts/*.py (git, GitHub, Jira REST)
                              ├─▶ gh CLI (GitHub)
                              ├─▶ git + ~/.netrc (Gerrit HTTPS)
                              └─▶ scripts/jira_rest.py (Jira REST, ccp_jira.env)
```

- **`.agents/skills/`** — single-purpose skills. Each is a `SKILL.md`: a
  spec describing exactly what to do, step by step, with example commands
  and expected output formats. Invoked directly as `/skill-name` in an
  opencode session, or chained together by an orchestrator agent.
- **`.opencode/agent/`** — custom orchestrator agents (`AGENT.md`). These
  chain multiple skills together with confirmation gates between phases, so
  a whole multi-step release cycle can run under one session.
- **`scripts/`** — real Python the skills shell out to for git/GitHub/Jira
  operations that need actual code (not just an LLM reading tool output),
  e.g. resolving the next semver tag, running the git-flow release process,
  or making Jira REST calls.
- **`config/`** — static settings (branch names, Gerrit host, the resolved
  list of tracked component repos) that skills read as defaults, overridable
  per run via CLI flags.
- **`specs/`** — background docs on how some of the trickier logic (e.g.
  resolving `SRCREV` entries to GitHub repos) was originally worked out.

## Prerequisites

- [opencode](https://opencode.ai) installed and able to run a model (this
  project is configured for `github-copilot/claude-sonnet-5` in
  `opencode.json` — change it if you use a different provider)
- `git`, [`gh`](https://cli.github.com) (GitHub CLI), `python3`, `git-flow`,
  and [`auto-changelog`](https://github.com/cookpete/auto-changelog) on your
  `PATH`
- Three separate credentials, none of which are stored in this repo:
  1. **GitHub** — `gh auth login` once; skills use the `gh` CLI, never a raw
     `GITHUB_TOKEN`.
  2. **Gerrit** — an HTTPS credential in `~/.netrc` (a `machine
     {gerrit_host}` entry, see `config/config.yaml`'s `gerrit_host`). Skills
     never read Gerrit credentials from anywhere else.
  3. **Jira** — copy `ccp_jira.env.template` to `ccp_jira.env` in the repo
     root and fill in `JIRA_BASE_URL`, `JIRA_API_VERSION`, `JIRA_USER`,
     `JIRA_TOKEN`. Every skill reads Jira credentials through
     `scripts/jira_rest.py` and only through that file — never hardcoded,
     never any other env var. `ccp_jira.env` is git-ignored; it must never
     be committed.

## Running a skill

From the repo root, with `opencode` pointed at this directory:

```bash
opencode
> /track-for-stable2 --dry-run
```

Most skills support `--dry-run` (preview only, no writes) and `--test-repo`
(restrict scope to a small set of approved fork repos instead of the real
`rdkcentral` org — safe to experiment against). See each skill's
`argument-hint` in its frontmatter for its exact flags.

To run the full multi-phase pipeline instead of one skill at a time, use one
of the orchestrator agents (see below) — these prompt for confirmation
between phases so nothing runs unattended.

## Skills

| Skill | What it does |
|---|---|
| `track-for-stable2` | Discovers newly merged PR commits on `develop` and adds a tracking label to their Jira tickets. Runs incrementally using saved per-repo state. |
| `stable2-candidates` | Filters Jira tickets with a chosen candidate label that aren't yet marked `*_considered`. Read-only. |
| `stable2-status-evaluator` | Fetches detailed Jira status (RM Approved, parent/linked tickets, dependencies) for candidates and groups them by readiness. Read-only. |
| `jira-pr-lookup` | Finds all merged PRs to `develop` for a Jira ticket, including its subtasks and dependency tickets. |
| `stable2-ready-considered-labeler` | Adds a "considered" label to READY tickets before the stable2 meta-sync phases, with a preview + confirmation gate. |
| `stable2-github-release-tagger` | Creates `support/stable2` GitHub release tags for eligible repos, with changelog notes generated from each repo's base semver tag. |
| `stable2-srcrev-updater` | Resolves GitHub SHAs for eligible repos and updates `generic-srcrev.inc` in the meta-rdk-broadband workspace. |
| `gerrit-cherrypick-squash` | Cherry-picks Jira-linked Gerrit changes to a target branch across multiple repos, squashes, and tags with a shared Gerrit topic. |
| `stable2-release-tracking-ticket` | Creates the bi-weekly stable2 sync Jira tracking ticket and links every successfully cherry-picked ticket to it. |
| `stable2-pr-labeler` | Adds a GitHub label to PRs to trigger GitHub's own cherry-pick automation to `support/stable2`. |
| `main-tagging` | Kicks off the biweekly release cycle: scans all tracked repos for new commits, resolves Jira status, updates the open-source commits monitor spreadsheet, and initializes the release session. |

## Orchestrator agents

| Agent | What it does |
|---|---|
| `stable2-release-orchestrator` | Runs the full stable2 release pipeline end to end (track → filter → evaluate → collect PRs → meta sync), with a confirmation gate between each phase. |
| `stable2-meta-sync-orchestrator` | The second half of the pipeline above, invoked as its final phase: GitHub cherry-pick, tracking ticket creation, considered-labeling, GitHub tagging, SRCREV update, and Gerrit cherry-pick/squash — all under one shared topic. |
| `on-demand-cherry-pick` | A standalone, on-demand path for one or more Jira tickets outside the biweekly cycle: cherry-picks their PRs to a branch you choose, computes a hotfix tag per repo, updates SRCREV/PKGREV, and syncs the linked Gerrit changes. Fully isolated from the biweekly pipeline's own state files. |

## Scripts

| Script | Used by | What it does |
|---|---|---|
| `scripts/main_tagging_release.py` | `main-tagging` | Runs the actual git-flow release/tagging process across all tracked repos that need it. |
| `scripts/cherry_pick_to_stable2.py` | `gerrit-cherrypick-squash` (indirectly) | Populates `cherry_picks_done` state used by the tracking-ticket skill. |
| `scripts/jira_rest.py` | every Jira-touching skill | Shared Jira REST helper (search, get issue, get comments, add label, add link, create issue) — credentials from `ccp_jira.env`, auto-paginates past Jira's per-request result cap. |

## Config files

- `config/config.yaml` — branch names, Gerrit/GitHub settings, tag-naming
  rules, file paths. Update `release_branch` every quarter; everything else
  changes rarely.
- `config/tracked_repos.yaml` — the resolved, manually-maintained list of
  GitHub repos `track-for-stable2` scans. Not auto-synced — if a new
  component is added to the meta-layer, add it here by hand (see
  `specs/srcrev-parsing.md` for how it was originally resolved).

## Contributing

This is an internal tool — skills are plain markdown specs, so the easiest
way to change behavior is usually to edit the relevant `SKILL.md`/`AGENT.md`
directly and re-run it with `--dry-run` first. `scripts/*.py` are plain
Python 3 with no third-party dependencies beyond `pyyaml`; `python3 -m
py_compile scripts/*.py` is enough to catch syntax errors before running
anything for real.
