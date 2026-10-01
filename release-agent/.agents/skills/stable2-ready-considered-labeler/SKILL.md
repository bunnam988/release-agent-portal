---
name: stable2-ready-considered-labeler
description: "Use after status-evaluator to add a considered label (default track_for_stable2_considered) on READY Jira tickets before stable2 meta-sync phases. Reads READY tickets from stable2_status_analysis.yaml, joins repo/PR context from pr_list.yaml, prints full preview with confirmation, then applies Jira labels. Supports --dry-run, --test-repo, and --label to override the default label."
license: Comcast
argument-hint: "Optional: '--label <name>' to use a different Jira label instead of the default track_for_stable2_considered, '--dry-run' for preview only, '--test-repo' to restrict to approved test repos, '--test-repo <approved_fork_url>' to narrow to one approved repo"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /stable2-ready-considered-labeler

> **⚠️ THERE IS NO DEDICATED SCRIPT FOR THIS SKILL** — do not look for or try
> to run `stable2_ready_considered_labeler.py`/`.sh` or anything similar; it
> does not exist. Every Jira read/write here goes through `scripts/jira_rest.py`
> below, called directly — nothing else.

## Purpose
Add a considered label (resolved as `{label}`, default `track_for_stable2_considered`, override with `--label <name>`) to Jira tickets that are already marked `READY` by status-evaluator.

This skill:
1. Reads READY tickets from `stable2_status_analysis.yaml`
2. Joins repo and PR context from `pr_list.yaml`
3. Prints the full ticket list (Jira, repo, PR, readiness/status, label state)
4. Waits for explicit confirmation
5. Adds `{label}` using `python3 scripts/jira_rest.py add-label`
6. Prints end summary with updated Jira list

## Resolve Label
- If the user passed `--label <name>`, use that exact string as `{label}`.
- Otherwise, default `{label}` to `track_for_stable2_considered`.
- Validate label format: only letters, digits, `_`, `-`, `.`. If invalid, ask the user for a corrected value.
- Print the resolved label before doing anything else: `Using Jira label: {label}`

## Critical Constraints — Read First
- Source of truth for eligibility is `readiness == "READY"` in `stable2_status_analysis.yaml`
- Do NOT re-evaluate RM Approved or other statuses in this skill
- Use `python3 scripts/jira_rest.py` (the shared Jira REST helper, credentials from
  `ccp_jira.env`) for all Jira reads/writes. Never call Jira MCP tools or read
  `JIRA_TOKEN`/etc. directly. If `ccp_jira.env` is missing or the script reports a
  credential error, STOP and tell the user exactly that.
- Label operations are add-only. Never remove or overwrite labels
- Always print the entire target list before mutation and require user confirmation
- If `--dry-run` is present, print exact planned actions and do NOT mutate Jira
- No dedicated script exists for this skill. The only script referenced anywhere in
  this file is `scripts/jira_rest.py` above — do not assume a separate one exists.

## Test-Repo Mode
If user passed `--test-repo`, restrict scope to this fixed allowlist:

- `https://github.com/Suganya-Sugumar/data-model-cli`
- `https://github.com/Suganya-Sugumar/test-and-diagnostic`
- `https://github.com/Suganya-Sugumar/xconf-client`
- `https://github.com/Suganya-Sugumar/moca-agent`
- `https://github.com/Suganya-Sugumar/utopia`
- `https://github.com/bunnam988/provisioning-and-management`

Rules:
- `--test-repo` (no URL): keep READY tickets only if their mapped repo is in allowlist
- `--test-repo <url>`: validate URL is allowlisted, then keep only that single repo
- If URL is not allowlisted: stop with
  `Invalid test repo URL. Only the approved test repo URLs are allowed in --test-repo mode.`

Print this banner in test mode:

```text
╔════════════════════════════════════════════════════════════════════╗
║  TEST-REPO MODE                                                   ║
║  Scope is restricted to approved forked components only.          ║
║  Real rdkcentral repos will NOT be touched by this step.          ║
╚════════════════════════════════════════════════════════════════════╝
```

## Dry-Run Mode
If user passed `--dry-run`:
- Execute all parsing, joining, filtering, and preview reporting
- Do NOT add labels in Jira
- Print final summary with `Would update` list

## Inputs
Required files in project root:
- `stable2_status_analysis.yaml`
- `pr_list.yaml`

Expected fields:
- `stable2_status_analysis.yaml` entries include at least `ticket` and `readiness`
- `pr_list.yaml` entries include at least `ticket`, `repo`, and `number` (or `url`)

## Workflow

### Step 1 — Parse Arguments
Read:
- `--label <name>` (see Resolve Label above)
- `--dry-run`
- `--test-repo`
- optional `--test-repo <approved_fork_url>`

### Step 2 — Load and Join Data
1. Load READY tickets from `stable2_status_analysis.yaml` where `readiness == "READY"`
2. Load `pr_list.yaml`
3. Build one or more rows per READY ticket with:
   - Jira key
   - readiness
   - status text from status-analysis entry (if present, else `NA`)
   - component/repo from matching PR entries (or `NA`)
   - PR number(s) from matching PR entries (or `NA`)

If no READY tickets are found, stop with:
`No READY Jira tickets found in stable2_status_analysis.yaml.`

### Step 3 — Apply Test-Repo Filtering (if enabled)
- In test mode, keep rows only for allowed repo scope
- For rows with no repo mapping, keep them only when not narrowing to a single URL
- Print final filtered row count and unique Jira count

### Step 4 — Read Current Jira Labels
For each unique READY Jira key, read current labels and determine action:
```bash
python3 scripts/jira_rest.py get-issue <KEY> --fields labels
```
- `already_labeled`: label already present in `.fields.labels`
- `to_add`: label missing

### Step 5 — Preview Table and Confirmation
Print full table before mutation:

```text
READY Jira Preview for {label}

Jira Key     | Readiness | Status        | Repo                     | PR #      | Action
------------------------------------------------------------------------------------------
RDKB-12345   | READY     | RM Approved   | rdkcentral/utopia        | #94       | ADD
RDKB-55555   | READY     | RM Approved   | rdkcentral/ccsp-wifi     | #140,#141 | ALREADY_LABELED
```

Then ask:
`Add {label} to Jira tickets marked ADD? [Y/n]`

If user answers `n`, stop with no changes.

### Step 6 — Apply Label Updates
If not dry-run and confirmed:
- For each Jira with action `ADD`, add the label:
  ```bash
  python3 scripts/jira_rest.py add-label <KEY> {label}
  ```
- For `ALREADY_LABELED`, skip and report as unchanged

If dry-run:
- Do not mutate Jira
- Report what would be added

### Step 7 — Final Report
Print summary:

```text
─────────────────────────────────────────────
READY Considered Labeling Summary
Mode:           {DRY-RUN / TEST-REPO / PRODUCTION}
READY Jiras:    {N}
To Add:         {A}
Already Labeled:{B}
Updated:        {U} (0 in dry-run)
─────────────────────────────────────────────
```

Then print list:
- Updated Jira list (or Would-update list in dry-run)
- Already-labeled Jira list

## Verify Completion
Before declaring done, verify:
- [ ] All tickets came from `readiness == "READY"`
- [ ] Full preview table was printed before any mutation
- [ ] Explicit confirmation was collected before applying labels
- [ ] `--dry-run` performed no Jira writes
- [ ] `--test-repo` filtering was applied when requested
