# RDK Release Management — Process Specification

## Purpose
Define the success criteria, constraints, and data contracts for the
agentic RDK release management pipeline for the support/stable2 branch.

## Configuration
All environment-specific values (branch names, URLs, paths) are in
`../config/config.yaml`. Read that file before starting any command.

---

## Command: /main-tagging

### Objective
Scan all component repositories for commits since the last run,
resolve their Jira tickets and statuses, and update the
open_source_commits_monitor.xlsx with a new sheet for this release cycle.

### Pre-conditions
- [ ] `config/config.yaml` is readable
- [ ] GitHub MCP is authenticated
- [ ] Jira MCP is authenticated
- [ ] `open_source_commits_monitor.xlsx` exists at the path in config

### Date Range Logic
1. Check if `last_run.txt` exists in the project root
   - If YES: read it as `start_dt` (ISO format)
   - If NO: ask user for start date. Accept formats: DD-MM-YYYY, DD/MM/YYYY
2. `end_dt` is always current UTC timestamp
3. After successful completion: write `end_dt` to `last_run.txt`
4. Display the date range before scanning:
   `Scanning commits: {start_dt} → {end_dt}`

### Steps
1. Clone `meta-rdk-broadband` from Gerrit using config values
2. Parse `generic-srcrev.inc` (see `./references/srcrev-parsing.md`)
   → produces: component name → current SRCREV mapping
3. For each component:
   a. Find the `.bb` recipe file → extract GitHub repo URL from SRC_URI
   b. Fetch commits from GitHub (branch: develop) within date range
   c. Filter: only commits with a PR number in the title
   d. For each commit: extract Jira ID by checking in this order:
      1. PR title
      2. PR description/body
      3. Commit message
   Use the first Jira ID pattern found. If none found, mark as "NO-JIRA"
   and record: PR number, commit SHA, component — report at end.
4. For each Jira ID found:
   a. Fetch Jira status, type, parent, linked tickets via Jira MCP
   b. Check parent Jira status (must note if parent is not RM Approved)
   c. Check all linked/dependent Jira statuses and flag any that are open/blocked
   d. Check RM Approved status using this logic:
   - Current status is RM Approved or beyond (Ready for Release Test,
     Ready for Patch Test, Verified in Patch Stable, Verified in Release)
     → mark rm_approved_ever = "Yes"
   - Current status has moved BACK from RM Approved (e.g. back to Open,
     In Progress, Code Review) even if changelog shows it was once RM Approved
     → mark rm_approved_ever = "Moved Back" — do NOT treat as approved
   - Never reached RM Approved → mark rm_approved_ever = "No"
   e. Fetch comments for dependency notes
5. Update `open_source_commits_monitor.xlsx`:
   - Add new sheet named: `{start_dt formatted MMM DD} - {end_dt formatted MMM DD}`
   - Sheet columns: S.No, Repo, Github link, Total Commits, Day, PR #,
     Commit #, Jira, RM Approved, Jira Status, Jira Type,
     Parent Jira, Parent Status, Linked Jira IDs, Linked Statuses,
     Notes (from comments)
   - Apply existing formatting: purple header, color-coded status cells,
     hyperlinks for Github and Jira columns, merged cells for component groups
6. Initialize/update `release_session.yaml` (see Data Contract below)
7. Load previous `release_session.yaml` if exists → carry forward
   `jiras_carry_forward` list into new session
8. Write current end_dt (ISO format) to last_run.txt in project root,
   overwriting previous value. This is used as start_dt on next run.


### Success Criteria
- [ ] All components from srcrev.inc were processed (or failure reason logged)
- [ ] open_source_commits_monitor.xlsx has a new sheet with correct date range name
- [ ] Sheet has data for all components that had commits in the date range
- [ ] Components with no commits show "NA" rows (not missing entirely)
- [ ] All Jira IDs are resolved with status, parent, linked info
- [ ] release_session.yaml is created/updated with current cycle data
- [ ] last_run.txt is updated with end_dt
- [ ] Final report printed: N components, M commits, K Jiras found,
      P Jiras carried forward from previous cycle

### Constraints
- Do NOT delete or overwrite existing sheets in the xlsx file
- Do NOT modify generic-srcrev.inc during this command
- Do NOT push anything to Gerrit during this command
- If a GitHub repo URL cannot be found for a component, log it and continue
- If a Jira ID cannot be resolved, mark as "NA" and continue — do not stop
- Clone directory must be cleaned up after scan completes
- Any commit where no Jira ID could be found must be collected and printed
  in a summary at the end: "Commits with no Jira ID found:"
  Format: Component | PR # | Commit SHA | Date
  This is a notification only — do not stop processing because of it.
---

## Data Contract — release_session.yaml

After /main-tagging completes, release_session.yaml must contain:

```yaml
release_week: <ISO week number>
cycle_start: "<start_dt ISO format>"
cycle_end: "<end_dt ISO format>"
release_branch: "<from config>"
support_branch: "<from config>"

# Components scanned this cycle
components_scanned: []   # list of component names

# Jiras found this cycle with full details
jiras_current_cycle:
  - id: ""
    component: ""
    commit_sha: ""
    commit_date: ""
    pr_number: ""
    status: ""
    jira_type: ""
    parent_jira: ""
    parent_status: ""
    linked_jiras: []
    linked_statuses: []
    rm_approved_ever: ""   # Yes / No / NA
    notes: ""

# Carried forward from previous cycle (not yet RM Approved)
jiras_carry_forward: []

# Populated by /get-jira-approval (empty at this stage)
jiras_labeled: []
jiras_rm_approved: []

# Populated by /cherry-pick-commits (empty at this stage)
cherry_picks_done: []
cherry_picks_skipped: []
cherry_picks_auto_resolved: []

# Populated by /release-version (empty at this stage)
support_tag: ""
gerrit_review_url: ""
release_tracking_jira: ""
```

---

## Stable2 Full Flow (Post Candidate Scan)

Use this sequence after `/main-tagging` when driving stable2 release operations.

### End-to-end sequence
1. `/stable2-release-orchestrator`
2. Internally runs:
   - `/track-for-stable2`
   - `/stable2-candidates`
   - `/stable2-status-evaluator`
   - `/jira-pr-lookup`
   - `/stable2-meta-sync-orchestrator`
3. `stable2-meta-sync-orchestrator` internally runs:
   - `python3 scripts/cherry_pick_to_stable2.py ...` for GitHub PR cherry-picks
   - `/stable2-ready-considered-labeler` (after cherry-pick completion)
   - `/stable2-github-release-tagger`
   - `/stable2-srcrev-updater`
   - `/gerrit-cherrypick-squash`
4. Cherry-pick phase supports partial success; continue tagging/SRCREV for successful repos and report manual actions for failed repos.

### Script-based cherry-pick behavior (before Gerrit)
After `pr_list.yaml` is prepared, run GitHub cherry-pick script first:
1. Read `stable2_status_analysis.yaml` and keep only `readiness == "READY"`
2. Read `pr_list.yaml`
3. Build repo scope from READY PR entries in `pr_list.yaml` (default)
4. Optionally narrow to one repo with `--repo <org/repo_or_url>`
5. Cherry-pick matching READY commits to `support/stable2`
6. On conflict, print conflict details with commit id and instruct manual resolution

Gate condition:
- Process all targeted repos and capture success/failure per repo.
- For repos with conflicts/errors, print failing repo + commit ids and require manual resolution.
- Continue tagging/SRCREV for successful repos only.
- Apply considered label after cherry-pick completion.

Separation rule:
- `python3 scripts/cherry_pick_to_stable2.py` is for GitHub repository cherry-pick flow.
- `/gerrit-cherrypick-squash` is a separate Gerrit workflow for Gerrit-hosted changes and meta-layer review/topic handling.
- These two flows must not be treated as the same operation.

Supported commands:
- `python3 scripts/cherry_pick_to_stable2.py --dry-run`
- `python3 scripts/cherry_pick_to_stable2.py --repo <org/repo_or_url>`
- `python3 scripts/cherry_pick_to_stable2.py --repo <org/repo_or_url> --no-push`
- `python3 scripts/cherry_pick_to_stable2.py --repo <org/repo_or_url> --commits <commit_id>`
- `python3 scripts/cherry_pick_to_stable2.py`

### Required artifacts through the flow
- `stable2_candidates.yaml`
- `stable2_status_analysis.yaml`
- `stable2_ready_tickets.txt`
- `pr_list.yaml`
- `stable2_release_tags.yaml`
- `stable2_srcrev_updates.yaml`

### Full-flow constraints
- Each phase requires explicit user confirmation before mutating Jira/Git/Gerrit.
- `--dry-run` and `--test-repo` must be propagated consistently.
- READY eligibility is sourced from `stable2_status_analysis.yaml`.
- `track_for_stable2_considered` is add-only and applied only to READY tickets.
- Gerrit topic must be shared for normal cherry-picks and meta-layer updates.
