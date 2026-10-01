---
name: stable2-release-tracking-ticket
description: "Use as the final phase of stable2-meta-sync-orchestrator to create the bi-weekly stable2 sync tracking Jira Task and link all successfully cherry-picked tickets to it with a 'deploys' link. Reads cherry_picks_done from release_session.yaml. Supports --dry-run and --test-repo."
license: Comcast
argument-hint: "Optional: '--dry-run' for preview only, '--test-repo' to restrict repo filtering to approved repos"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /stable2-release-tracking-ticket

> **⚠️ THERE IS NO DEDICATED SCRIPT FOR THIS SKILL** — do not look for or try
> to run `stable2_release_tracking_ticket.py`/`.sh` or anything similar; it
> does not exist. Every Jira read/write here goes through `scripts/jira_rest.py`
> below, called directly — nothing else.

## Purpose
Create the bi-weekly stable2 sync tracking Jira Task and add outgoing `deploys`
links to every Jira ticket that was successfully cherry-picked this cycle.

## Critical Constraints — Read First
- Use `python3 scripts/jira_rest.py` (the shared Jira REST helper, credentials from
  `ccp_jira.env`) for all Jira writes. Never call Jira MCP tools or read
  `JIRA_TOKEN`/etc. directly. If `ccp_jira.env` is missing or the script reports a
  credential error, STOP and tell the user exactly that.
- No dedicated script exists for this skill. The only script referenced anywhere in
  this file is `scripts/jira_rest.py` above — do not assume a separate one exists.
- Read `release_session.yaml` for `cherry_picks_done`, `cycle_start`, and `cycle_end`.
- Only tickets in `cherry_picks_done` qualify for linking. Do NOT link tickets that
  were skipped, conflicted, or not present in `cherry_picks_done`.
- Always print the full confirmation table before any Jira mutation.
- Do NOT transition the created ticket to any status — leave it as `New`.
- If `--dry-run`: print all planned actions, create no Jira tickets, add no links.

## Test-Repo Mode
If `--test-repo` is present, apply the same approved-repo filtering used in the
rest of the meta-sync flow when interpreting `cherry_picks_done` repo context.
No change to Jira project or ticket creation — this flag affects only repo-level
filtering of the cherry_picks_done list.

## Inputs
Required file in project root:
- `release_session.yaml`

Fields read from `release_session.yaml`:
- `cycle_start`    — ISO datetime; used as the **start date** of the title range
- `cherry_picks_done` — list of successfully cherry-picked entries; each entry must include:
  - `ticket`           (Jira key, e.g. `RDKB-66213`)
  - `pr_number`        (integer or string, e.g. `94`)
  - `component`        (component/repo name, e.g. `utopia`)
  - `merge_commit_sha` (40-char SHA)

Read config from `/workspace/config/config.yaml`:
- `jira_browse_url`

## Date Range Computation

- **start_date**: parse `cycle_start` from `release_session.yaml` → format as `DD/MM/YYYY`
- **end_date**: today's date when this skill runs → format as `DD/MM/YYYY`

## Jira Ticket Fields

Use exactly these values (based on reference ticket RDKB-66564):

| Field                | Value                                                                 |
|----------------------|-----------------------------------------------------------------------|
| Project              | RDKB                                                                  |
| Issue type           | Task                                                                  |
| Summary              | `[{start_date}] - [{end_date}] bi-weekly stable2 sync`               |
| Priority             | P1                                                                    |
| Component/s          | `meta-rdk-broadband`                                                  |
| Branch               | `Stable2`                                                             |
| RDK SI impact        | `5 - None`                                                            |
| Risk Score           | `5`                                                                   |
| Patch_worthy         | `Pending`                                                             |
| Blocker              | `Pending`                                                             |
| Regression_in_master | `Pending`                                                             |
| Test Coverage        | `No`                                                                  |
| Resolution Type      | `Fixed`                                                               |
| Description          | `[{start_date}] - [{end_date}] bi-weekly stable2 sync`               |
| All other fields     | Leave at default / unset                                              |

## Workflow

### Step 1 — Validate Inputs
1. Confirm `release_session.yaml` exists. If not, stop with:
   `release_session.yaml not found. Run the cherry-pick phase before this step.`
2. Load `cherry_picks_done`. If empty, print a warning and ask:
   `No cherry_picks_done entries found. Still create the tracking ticket with no links? [Y/n]`
   If user answers `n`, stop.

### Step 2 — Compute Dates and Build Preview

Compute `start_date` from `cycle_start` and `end_date` from today.

Build the confirmation table — one row per unique `ticket` in `cherry_picks_done`
(deduplicate by ticket; keep all PR rows if a ticket has multiple PRs):

```
──────────────────────────────────────────────────────────────────────────────
 Jira         | PR #  | Component          | Commit SHA (short)
──────────────────────────────────────────────────────────────────────────────
 RDKB-66213   | #94   | utopia             | 3a9108f039
 RDKB-66284   | #101  | ccsp-wifi          | f8d22a1bc4
──────────────────────────────────────────────────────────────────────────────
Total: {N} tickets to link as 'deploys'
```

Then print the proposed ticket:

```
New Jira ticket to create:
  Project    : RDKB
  Type       : Task
  Summary    : [{start_date}] - [{end_date}] bi-weekly stable2 sync
  Priority   : P1
  Component  : meta-rdk-broadband
  Description: [{start_date}] - [{end_date}] bi-weekly stable2 sync
```

### Step 3 — Confirm
Ask:
```
Create this tracking ticket and add {N} 'deploys' links? [Y/n]
```
If user answers `n`, stop with no changes.

### Step 4 — Create Jira Ticket (skip in --dry-run)
Build a JSON fields object from the table in "Jira Ticket Fields" above (project as
`{"key": "RDKB"}`, issuetype as `{"name": "Task"}`, priority as `{"name": "P1"}`,
components as `[{"name": "meta-rdk-broadband"}]`, remaining fields as their plain
string/value), write it to a temp file, then:
```bash
python3 scripts/jira_rest.py create-issue /path/to/fields.json
```
Store the returned `.key` as `{tracking_ticket_key}`.

Print immediately after creation:
```
Created: {tracking_ticket_key}  ({jira_browse_url}{tracking_ticket_key})
```

### Step 5 — Add Deploys Links (skip in --dry-run)
For each unique ticket in `cherry_picks_done`:
- Add a Jira link from `{tracking_ticket_key}` → `{ticket}` with link type `deploys`:
  ```bash
  python3 scripts/jira_rest.py add-link {tracking_ticket_key} {ticket} deploys --direction outward
  ```
- If the link already exists, skip and log as "already linked".
- If the `jira_rest.py` call fails for a specific ticket, log it and continue with the rest.

Print per-ticket result as each link is added:
```
  ✓ deploys → RDKB-66213
  ✓ deploys → RDKB-66284
  ✗ FAILED  → RDKB-99999  (error: issue not found)
```

### Step 6 — Update release_session.yaml
Write the created ticket key into `release_tracking_jira` in `release_session.yaml`:
```yaml
release_tracking_jira: "{tracking_ticket_key}"
```

### Step 7 — Final Report
Print:
```
─────────────────────────────────────────────────────────────────
Stable2 Release Tracking Ticket Summary
Mode:      {DRY-RUN / TEST-REPO / PRODUCTION}
─────────────────────────────────────────────────────────────────
Tracking ticket : {tracking_ticket_key}  (or DRY-RUN: not created)
Date range      : [{start_date}] - [{end_date}]
Tickets linked  : {N_ok}
Already linked  : {N_skip}
Link failures   : {N_fail}
─────────────────────────────────────────────────────────────────
```

If `--dry-run`:
```
DRY-RUN: No Jira ticket was created. No links were added.
Would create: [{start_date}] - [{end_date}] bi-weekly stable2 sync
Would link {N} tickets as 'deploys'.
```

## Verify Completion
Before declaring done, confirm:
- [ ] `cherry_picks_done` was the only source for link candidates
- [ ] Confirmation table was printed and user confirmed before any mutation
- [ ] New ticket left in `New` status (no transition attempted)
- [ ] `release_tracking_jira` updated in `release_session.yaml` after creation
- [ ] `--dry-run` performed no Jira writes
