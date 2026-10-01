---
name: main-tagging
description: "Use when running /main-tagging to kick off the RDK biweekly release cycle. Scans all RDK component repositories for new commits since the last run, resolves Jira ticket statuses via the jira_rest.py REST helper, updates open_source_commits_monitor.xlsx with a new release sheet, and initializes the release session yaml. Use for: biweekly release scan, commit monitoring, Jira status fetch, open source monitor update, release session init. Don't use for cherry-picking, tagging support branch, or updating srcrev.inc."
license: Comcast
argument-hint: "Optional: date range with optional time 'Jul 23 2026 09:00 to Jul 27 2026 17:30', date only 'Jun 15 2026 to Jul 08 2026', '--test' for 3 components only, '--dry-run' to scan and update the sheet without git tagging (see scripts/main_tagging_release.py for tagging), '--test-repo' to run only against 6 fixed forked test components, '--test-repo <url>' to run a single allowed fork from that same list"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /main-tagging

## Purpose
Scan all component repositories for commits since the last run, resolve
their Jira tickets and statuses, update the open source commits monitor
spreadsheet, and initialize the release session for this cycle.

## Critical Constraints — Read First
- Use `python3 scripts/jira_rest.py` (the shared Jira REST helper, credentials from
  `ccp_jira.env`) for all Jira operations. Never call Jira MCP tools or read
  `JIRA_TOKEN`/`JIRA_USER`/etc. directly.
- If `ccp_jira.env` is missing or `jira_rest.py` reports a credential error, STOP
  immediately and tell the user exactly that (see the script's own error message).
- Use GitHub MCP or gh CLI for GitHub operations. NEVER use GITHUB_TOKEN env var directly.
- Do NOT read any credentials from environment variables or .bashrc.

## Dry-Run Mode
If the user passed `--dry-run`:
- Execute Steps 1–5 fully (read tracked repos, fetch commits, resolve Jira, **update xlsx**)
- Execute Steps 6–8 fully (update session yaml, update last_run.txt, cleanup)
- This skill no longer does any git/tagging itself — see Step 10 below for
  where that now lives (`scripts/main_tagging_release.py`, which has its
  own `--dry-run`)
- Make clear: "No git commands were executed. The above is a preview only."

## Test-Repo Mode
If the user passed `--test-repo` (with or without an optional URL argument):

Use this fixed allowlist only:
```
https://github.com/Suganya-Sugumar/data-model-cli
https://github.com/Suganya-Sugumar/test-and-diagnostic
https://github.com/Suganya-Sugumar/xconf-client
https://github.com/Suganya-Sugumar/moca-agent
https://github.com/Suganya-Sugumar/utopia
https://github.com/bunnam988/provisioning-and-management
```

Print a prominent banner at the start:
```
╔════════════════════════════════════════════════════════════════════╗
║  TEST-REPO MODE                                                   ║
║  Scope is restricted to 6 approved forked components only.        ║
║  Real rdkcentral repos will NOT be touched.                       ║
╚════════════════════════════════════════════════════════════════════╝
```

Behavior changes:
1. **Skip Step 1's `tracked_repos.yaml` read** — use the allowlist below instead
2. Build `test_components` from the allowlist above, where each entry has:
   - `name`: repo name (data-model-cli, test-and-diagnostic, xconf-client, moca-agent, utopia, provisioning-and-management)
   - `github_url`: full allowlisted fork URL
3. If user passed only `--test-repo` (no URL):
   - **Replace the component list** with all 6 allowlisted components
4. If user passed `--test-repo <url>`:
   - Validate `<url>` is one of the 6 allowlisted URLs
   - If not allowlisted: STOP with message:
   `Invalid test repo URL. Only the 6 approved fork URLs are allowed in --test-repo mode.`
   - If valid: **Replace the component list** with that single allowlisted component
5. In Step 2: use `https://api.github.com/repos/{org}/{repo}` for each selected test component
   Use each fork's **develop** branch if it exists, otherwise use that fork's default branch
6. In Step 3-4: Jira extraction and lookup proceed exactly as normal
7. In Step 5: update xlsx exactly as normal (1 sheet with 1 or 5 components, based on mode)
8. In Step 10 (tagging): use each selected fork URL for tag lookups and git operations.
   Push/tag/release only in those selected fork repos — no Gerrit involved.

Test-repo mode and dry-run mode can be combined:
`--test-repo --dry-run`
- Scans only the 6 allowlisted forks, updates xlsx, and skips tagging git commands (dashboard only)

## Before Starting
1. Read `/workspace/config/config.yaml` — used only for `commits_xlsx` and any
   other non-Gerrit values referenced later in this skill. This skill does
   not read `gerrit_host`/`gerrit_repo`/`release_branch` anymore — see
   Step 1, which reads the pre-resolved `tracked_repos.yaml` instead.
2. Read `/workspace/release_session.yaml` if it exists — load `jiras_carry_forward`
   from the previous cycle
3. Tell the user what date range will be scanned (see Date Range below)

## Date Range
Determine `start_dt` and `end_dt` using this priority order:

**Priority 1 — Inline argument provided by user**
If the user provided a date range when invoking the skill, parse it directly.

Accepted formats (date part):
- `MMM DD YYYY` → `Jul 23 2026`
- `DD-MM-YYYY` or `DD/MM/YYYY` → `23-07-2026`
- `YYYY-MM-DD` → `2026-07-23`

Accepted formats (optional time part, appended after the date):
- 24-hour: `HH:MM` or `HH:MM:SS` → `09:00`, `17:30:00`
- The time is treated as UTC

Examples of full arguments:
- `Jun 15 2026 to Jul 08 2026` → start=2026-06-15 00:00:00 UTC, end=2026-07-08 23:59:59 UTC
- `Jul 23 2026 09:00 to Jul 27 2026 17:30` → start=2026-07-23 09:00:00 UTC, end=2026-07-27 17:30:00 UTC
- `23-07-2026 09:00 to 27-07-2026 17:30` → same as above

Rules:
- The word `to` separates start and end datetime strings
- If **time is provided**: use it exactly as given (UTC)
- If **no time provided for start**: default to `00:00:00 UTC`
- If **no time provided for end**: default to `23:59:59 UTC`
- Do NOT read `last_run.txt` in this case
- Do NOT update `last_run.txt` at the end (preserve it for the next regular run)

**Priority 2 — No argument, use last_run.txt**
If no date argument was provided:
- Check if `last_run.txt` exists in the project root
  - If YES: read it as `start_dt` (ISO format — may include time)
  - If NO: ask the user:
    "No previous run found. Enter start date/time (e.g. 23-07-2026 09:00) or press Enter
     to use default (2025-08-22):"
- Set `end_dt` = current UTC timestamp (including current time, not just date)
- Update `last_run.txt` with `end_dt` in full ISO format (including time) after successful run

**Always:**
Print the date range before proceeding and ask user to confirm:
```
Scanning commits: {start_dt} → {end_dt}
Proceed? [Y/n]
```

## Step 1 — Get Component List

Resolve repo scope from the same pre-resolved, manually-maintained list
`track-for-stable2` uses — do NOT clone `meta-rdk-broadband` or parse
`generic-srcrev.inc` here anymore:

1. Read `/workspace/config/tracked_repos.yaml`.
2. Use every `repos[].url` entry as a component, with `component` = repo
   basename and `github_url` = the entry's `url`. This file already
   includes `additional_components` from config.yaml (e.g. `secure-upnp`)
   folded in — do not separately re-add `additional_components` here.
3. Use `unresolved_components` (if present) to report components with no
   known GitHub URL. **Do NOT drop them from the list** — they must still
   appear in the sheet with NA values, matching the file's own notes on
   why they have no URL.
4. If `/workspace/config/tracked_repos.yaml` is missing or has no `repos`
   entries, STOP and tell the user exactly that — do NOT fall back to
   cloning `meta-rdk-broadband` or to a guessed repo list. If a repo is
   added to the meta-layer later, a human updates `tracked_repos.yaml`
   directly; this skill does not attempt to detect or add new repos on
   its own.
5. Print: `Found {N} components to process ({X} with GitHub URL, {Y} with no URL)`

## Step 2 — Fetch Commits Per Component

For each component with a valid GitHub URL:

1. Convert GitHub URL to API URL:
   `https://github.com/rdkcentral/X` → `https://api.github.com/repos/rdkcentral/X`

2. Fetch commits from branch `develop`, paginated (100 per page):
   `GET /repos/rdkcentral/{repo}/commits?sha=develop&per_page=100&page={n}`

3. For each commit returned:
   - Parse commit date
   - If commit date < start_dt: stop paging for this component
   - If commit date is NOT within [start_dt, end_dt]: skip
   - If commit date is within range:
     - Check if PR number exists in commit title (pattern: `#\d+`)
     - If no PR number: skip this commit
     - If PR number found:
       - Store the PR number **including the `#` prefix** exactly as matched (e.g. `#94`, not `94`)
       - Store the **full 40-character commit SHA** from the `sha` field. Never truncate it.
       - Keep this commit for Jira extraction

4. If GitHub API returns 429 (rate limit): stop fetching for this component,
   log it, continue to next component. Report all rate-limited components
   at the end.

5. If GitHub API returns 401 (Unauthorized): Stop all GitHub fetches immediately.
   Tell the user: "GitHub authentication failed. Please run:
   gh auth login -h github.com -w -s repo
   then re-run /main-tagging"
   Do not continue — exit the skill.

6. If GitHub API returns any other error: log component + error code,
   continue to next component.

## Step 3 — Extract Jira ID Per Commit

For each commit with a PR number:

Jira key extraction rule (generic, not RDK-only): `([A-Z0-9]+-\d+)`
- Examples that MUST match: `RDKB-64184`, `XB10-2860`, `CBR2-1234`, `TCXB7-7222`

1. Check PR title for Jira ID pattern: `[A-Z0-9]+-\d+`
2. If not found: fetch PR details and check PR description/body
3. If not found: check commit message lines
4. If found anywhere: record the Jira ID
5. If NOT found anywhere: record as "NO-JIRA" with:
   - Component name
   - PR number
   - Commit SHA
   - Commit date
   These will be reported at the end.

## Step 4 — Fetch Jira Details

For all unique Jira IDs found, fetch with the shared REST helper, requesting the
changelog too (needed for the RM-Approved check in step 4 below):

```bash
python3 scripts/jira_rest.py get-issue <KEY> \
  --fields status,issuetype,parent,issuelinks,summary --expand changelog
```

For each Jira ID:
1. Get: status, issue type, parent Jira key, linked issue keys, summary (call above)
2. Get parent Jira status (one level up only) — same call on the parent key
3. Get each linked Jira's status (one level only — do not recurse) — same call per linked key
4. Check RM Approved status using this logic, reading `.changelog.histories` from the
   response above (each history entry has `.items[]` with `field: "status"` transitions):
   - Current status is one of: "RM Approved", "Ready for Release Test",
     "Ready for Patch Test", "Verified in Patch Stable", "Verified in Release"
     → rm_approved = "Yes"
   - Current status has moved back FROM one of the above (check changelog)
     → rm_approved = "Moved Back" — NOT treated as approved
   - Never reached RM Approved in changelog
     → rm_approved = "No"
5. Fetch comments and look for dependency language:
   ```bash
   python3 scripts/jira_rest.py get-comments <KEY>
   ```
   Scan `.comments[].body` for:
   "goes with", "depends on", "needs", "blocked by", "cherry-pick",
   "companion", "paired with", "part of"
   Extract the relevant sentence and any other Jira IDs mentioned.
   If nothing relevant found: notes = "NA"

6. If any `jira_rest.py` call reports an HTTP 401: Stop all Jira fetches immediately.
   Tell the user: "Jira authentication failed. Check the credentials in ccp_jira.env
   and try again."
   Do not continue — exit the skill.

7. If a Jira API call returns data for a **different Jira ID** than the one requested
   (e.g., you request RDKB-62906 and the API returns TCXB8-4149 data), this is a known
   Jira ID migration pattern where a ticket was moved or renumbered.
   **Do NOT skip it.** Fill the row with whatever data was returned by the API.
   Record the original Jira ID (as found in the PR) in the Jira column.

8. If a Jira ID cannot be resolved (HTTP 404, genuine error, fake ID like TICKET-123):
   mark all fields as "NA", log it, continue. Include in the end report.

## Step 5 — Update Spreadsheet

CRITICAL: Open the EXISTING file at the exact path in config.yaml (commits_xlsx).
- NEVER create a new file or save to a different filename or location.
- NEVER create files named open_source_monitor_updated.xlsx or any variant.
- ALWAYS open the existing open_source_commits_monitor.xlsx and add a new sheet to it.
- Save back to the SAME original path only.

1. Create a new sheet named: `{start_dt MMM DD} - {end_dt MMM DD}`
   Example: `Jul 02 - Jul 16`
   Insert the new sheet at position 0 (first/leftmost tab) — not at the end.
   Do NOT overwrite or delete any existing sheets.

2. Write these columns (in order):
   S.No | Repo | Github link | Total Commits | Day | PR # | Commit # |
   Jira | RM Approved | Jira Status | Jira Type | Parent Jira |
   Parent Status | Linked Jira IDs | Linked Statuses | Notes (from comments)

3. Group rows by component:
   - Merge cells A, B, C, D vertically for all rows of the same component
   - S.No increments per component (not per row). Write as an **integer**, not a string.
   - Total Commits = number of commits for that component in this date range.
     Write as an **integer**, not a string.
   - Components with no commits in range **or no GitHub URL at all**: write one row with
     `NA` in Day, PR#, Commit#, Jira and all Jira-related columns.
     The Github link column shows the URL if known, or `NA` if none.
     **Every component from srcrev.inc plus additional_components must appear
     in the sheet. Never silently drop a component.**
   - PR# column must include the `#` prefix (e.g. `#94`, not `94`).
     If no PR, write `NA`.

4. Apply formatting:
   - Header row: bold, purple fill (#DDA0DD), left-aligned, thin border
   - All data rows: left-aligned, wrap text, thin border
   - RM Approved column: green fill for "Yes", red fill for "No"
   - Jira Status column: color code by status:
     - Green: Done, Closed, Resolved, RM Approved, Ready for Release Test,
               Ready for Patch Test, Verified in Patch Stable, Verified in Release
     - Orange: Verified in Sprint
     - Blue: Ready for Sprint Test, Ready for Sprint, Code Dev, New, In Progress
     - Yellow: In Review, Code Review
     - Red: Open, To Do, Backlog
   - Parent Status column: same color coding as Jira Status
   - Github link column: clickable hyperlink, blue underline
   - Jira column: clickable hyperlink to {jira_browse_url}{jira_id}, blue underline

5. Auto-fit column widths with these maximums:
   Repo: 25, Github link: 30, PR#: 8, Jira: 15,
   RM Approved: 12, Jira Status: 22, Parent Status: 22,
   Linked Jira IDs: 18, Linked Statuses: 18, Notes: 50

## Step 6 — Update Release Session

Write/update `release_session.yaml` in the project root.
Follow the data contract in
`/workspace/specs/release-process.spec.md`.

Key points:
- Load previous session's `jiras_carry_forward` if it exists
- Populate `jiras_current_cycle` with all Jiras found this run
- Leave `jiras_labeled`, `jiras_rm_approved`, `cherry_picks_*` as empty lists
- Set `cycle_start` and `cycle_end` from the date range used

## Step 7 — Update last_run.txt

if last_run.txt file doesn't exist, create one.
Write `end_dt` in ISO format to `last_run.txt` in the project root.
Overwrite any existing content.
## Step 8 — Cleanup

Nothing to clean up — Step 1 no longer clones `meta-rdk-broadband` (it
reads `config/tracked_repos.yaml` instead), so there is no temporary
clone directory left behind by this skill.

## Step 9 — Final Report

Print a summary:
```
─────────────────────────────────────────────
/main-tagging complete
Date range:    {start_dt} → {end_dt}
Components:    {N} processed, {X} skipped (no GitHub URL — see tracked_repos.yaml unresolved_components)
Commits found: {M} total across all components
Jiras found:   {K} unique Jira IDs resolved
Carry-forward: {P} Jiras from previous cycle
Sheet created: {sheet_name} in open_source_commits_monitor.xlsx
─────────────────────────────────────────────
```

If any commits had no Jira ID found, print:
```
Commits with no Jira ID:
Component       | PR #  | Commit SHA | Date
──────────────────────────────────────────
{component}     | #{pr} | {sha}      | {date}
```

If any Jira IDs could not be resolved, print:
```
Jira IDs that could not be resolved:
{jira_id} — Component: {component}, PR: #{pr}
```

## Step 10 — Main Branch Tagging (moved out of this skill)

Main-branch tagging is **no longer done here**. It previously duplicated
what the real, actually-used `extract_list_2.py` + `release_3.py` scripts
did (via xlsx-derived repo scope), which drifted from what those scripts
actually did in practice.

Tagging now lives entirely in `scripts/main_tagging_release.py` — a
standalone script that determines which repos need tagging on its own
(comparing `develop` against each repo's latest semver tag via the GitHub
API, using `config/tracked_repos.yaml` for repo scope, no xlsx involved)
and runs the same git-flow release process. See that script's own
docstring for full behavior and `python3 scripts/main_tagging_release.py
--help` for usage.

Do not reimplement tagging logic here. If you need to tag components,
run:
```bash
python3 scripts/main_tagging_release.py [--dry-run] [--test-repo] [--repo org/repo]
```

**Timeout:** this checks all 41 tracked repos one at a time against the
GitHub API (each check takes a few seconds), so a full run over the whole
tracked-repo list can take several minutes even in `--dry-run`. Use a bash
timeout of **at least 10 minutes (600000 ms)** on the first attempt — do
not start with a short default and retry after it gets killed partway
through; that just wastes the time it already spent on the repos it got
through before being killed. `--repo <single-repo>`/`--test-repo` runs are
much faster and don't need this.

Also invoke it as `python3`, not `python` — this container has no bare
`python` binary.

## Verify Completion
Before declaring done, check all success criteria in
`/workspace/specs/release-process.spec.md` under
"Command: /main-tagging → Success Criteria".
Report any criterion that was not met.

