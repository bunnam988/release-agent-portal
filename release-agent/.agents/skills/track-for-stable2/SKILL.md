---
name: track-for-stable2
description: "Use when you want to discover newly merged PR commits on develop and add a tracking label (default track_for_stable2) on Jira tickets. Runs incrementally using saved state per repo. First run bootstraps from the latest tag created on each component repo; subsequent runs fetch only commits after last processed marker."
license: Comcast
argument-hint: "Optional: '--label <name>' to use a different Jira label instead of the default track_for_stable2, '--test-repo' to run only the 6 approved fork repos, '--test-repo <url>' to run one approved fork only, '--dry-run' to print results without updating state, '--reset-state' to rebuild baseline markers"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /track-for-stable2

> **⚠️ THERE IS NO `track_for_stable2.py`, `track_for_stable2.sh`, OR ANY OTHER
> DEDICATED SCRIPT FOR THIS SKILL — do not look for one, do not try to run one,
> do not glob/grep for one. If you find yourself about to run a bash command
> like `./track_for_stable2.sh` or `python3 track_for_stable2.py`, STOP: that
> file does not exist anywhere in this repo and never has. This skill's entire
> logic (commit scanning, state tracking) is done via the exact `git`/`gh`
> commands given below in "Commit Window Rules" and "Step 1-4", executed
> directly by you, one at a time — reasoning through raw git output, not
> delegating to a script. The only script this skill ever invokes is the
> shared `scripts/jira_rest.py` helper, and only for the Jira label steps.**

## Purpose
track-for-stable2 for stable2 tracking.
Detect commits merged into `develop`, extract Jira IDs, and output a clean tracking list.

This phase performs discovery + stable2 tracking label update:
- ✅ Find commits and Jira IDs
- ✅ Add the resolved `{label}` (default `track_for_stable2`, override with `--label <name>`) to Jira IDs discovered from new develop merges
- ✅ If Jira is Task/Sub-task and has parent, add label to parent Jira too
- ✅ Persist incremental state markers

## Resolve Label
- If the user passed `--label <name>`, use that exact string as `{label}`.
- Otherwise, default `{label}` to `track_for_stable2`.
- Validate label format: only letters, digits, `_`, `-`, `.`. If invalid, ask the user for a corrected value.
- Print the resolved label before doing anything else: `Using Jira label: {label}`

## Critical Constraints
- Use GitHub MCP or `gh` CLI for GitHub operations.
- Use `python3 scripts/jira_rest.py` (the shared Jira REST helper, credentials from
  `ccp_jira.env`) for every Jira read/write (label checks and label add). Do NOT call
  Jira MCP tools, raw `curl`, or read `JIRA_TOKEN`/etc. directly — always go through
  this script so credential handling stays in one place.
- If `ccp_jira.env` is missing or `jira_rest.py` reports a credential error, STOP and
  tell the user exactly that (see the script's own error message) rather than falling
  back to any other Jira access method.
- Do NOT remove or rewrite historical run data.
- State updates happen only after a successful full run.
- Jira label updates are ADD-ONLY. Never remove or overwrite existing labels.
- Before any Jira mutation, print target Jira list and require explicit user confirmation.
- No dedicated script exists for this skill's commit-scanning/state logic. The only
  script referenced anywhere in this file is the shared `scripts/jira_rest.py` above —
  everything else (finding merged commits, comparing against saved state, resolving
  Jira IDs) is done directly via `gh`/`git` CLI commands and your own reasoning, not a
  separate `track_for_stable2.py` or similar. Do not assume one exists.

## Modes

### Default mode (production scope)
- Resolve repo scope from the pre-resolved, manually-maintained list:
  - Read `/workspace/config/tracked_repos.yaml`
  - Use the `repos[].url` entries directly as the scan scope — this file
    was resolved once (clone meta-rdk-broadband, parse
    `conf/include/generic-srcrev.inc`, resolve each component's GitHub
    repo URL from recipe `SRC_URI`) and is kept up to date manually
  - Do **not** clone `meta-rdk-broadband` or re-parse `generic-srcrev.inc`
    in this skill — that discovery already happened once and its result
    lives in `tracked_repos.yaml`
  - If `tracked_repos.yaml` is missing, STOP and tell the user: "config/tracked_repos.yaml
    not found. This file must exist with the resolved repo scope — see
    specs/srcrev-parsing.md for how it was produced." Do NOT fall back to
    cloning meta-rdk-broadband automatically.
  - If a repo is added to the meta-layer later, a human updates
    `tracked_repos.yaml` directly (add the component under the right
    `repos[].components` list, or a new `repos[]` entry) — this skill does
    not attempt to detect or add new repos on its own.
- This is the mode used when `--test-repo` is NOT provided.

### Test-repo mode
If user passes `--test-repo`, restrict scope to this allowlist only:
```
https://github.com/Suganya-Sugumar/data-model-cli
https://github.com/Suganya-Sugumar/test-and-diagnostic
https://github.com/Suganya-Sugumar/xconf-client
https://github.com/Suganya-Sugumar/moca-agent
https://github.com/Suganya-Sugumar/utopia
https://github.com/bunnam988/provisioning-and-management
```

Rules:
- `--test-repo` (no URL): run all 6 allowlisted repos.
- `--test-repo <url>`: run only that repo if URL is allowlisted.
- If URL is not allowlisted: STOP with error.

### Dry-run mode
If `--dry-run` is provided:
- Run full detection and reporting.
- Do NOT write state file.
- Do NOT add labels in Jira.

### Reset-state mode
If `--reset-state` is provided:
- Recompute baseline markers as if first run.
- Overwrite existing repo markers only after confirmation.

## State File
Use this state file in project root:
`track_for_stable2_state.yaml`

Schema:
```yaml
last_success_run_utc: ""
repos:
  <owner/repo>:
    mode: "baseline|incremental"
    baseline_source: "latest_component_tag"
    baseline_tag: ""
    baseline_commit_sha: ""
    last_processed_commit_sha: ""
    last_processed_commit_time_utc: ""
    last_run_new_commits: 0
```

## Commit Window Rules

### First run per repo (no state marker)
Bootstrap baseline from latest tag created on that component repo:

**CRITICAL: Tag Selection Algorithm (must be implemented EXACTLY as specified)**

1. **Fetch ALL tags for the repo:**
   ```bash
   git tag -l --sort=-creatordate --format='%(refname:short)|%(creatordate:iso)|%(objectname)'
   ```
   - For annotated tags: uses tagger date
   - For lightweight tags: uses commit date
   - Sort: newest first (-creatordate)

2. **Filter to semver-only tags:**
   - Iterate through tags in order (newest first)
   - For each tag, check if tag name matches: `^[0-9]+\.[0-9]+\.[0-9]+$` (digits and dots only)
   - Examples that MATCH: `1.2.3`, `2.7.1`, `10.0.0`
   - Examples to SKIP: `v1.2.3`, `1.2.3-rc1`, `1.2.3_stable2`, `1.2.3_hotfix`, `release-1.2.3`

3. **Select FIRST matching tag:**
   - The first tag in the sorted list that matches the pattern is selected
   - This is the "latest created" semver tag
   - Store: tag name, creation date, and commit SHA it points to

4. **Get baseline commit SHA:**
   ```bash
   git rev-parse {tag_name}^{commit}
   ```
   - This gives the actual commit SHA the tag points to (peeled)

5. **Find commits after baseline:**
   ```bash
   git rev-list --reverse {baseline_sha}..origin/develop
   ```
   - Only commits reachable from develop but NOT reachable from baseline
   - Baseline commit itself is excluded

**If no semver tag exists:**
- Use merge-base between `main` and `develop` as baseline
- Mark `baseline_source` as `merge_base_main_develop`

**Common pitfalls to avoid:**
- ❌ Don't use `git tag -l --sort=-version:refname` (sorts by version number, not date)
- ❌ Don't filter tags before sorting (you might miss the latest)
- ❌ Don't use commit date when annotated tag has tagger date
- ❌ Don't include baseline commit in the range (use `..` not `...`)

**Debug output (print this for validation):**
```
Tag selection for {repo}:
  All semver tags found (newest first):
    2.3.0 (created: 2026-07-22, SHA: abc1234)
    2.2.0 (created: 2026-04-23, SHA: def5678)
    2.1.0 (created: 2026-01-15, SHA: ghi9012)
  Selected: 2.3.0 (latest by creation date)
```

If no semver tag exists:
- Use merge-base between `main` and `develop` as baseline.
- Mark `baseline_source` as `merge_base_main_develop`.

### Subsequent runs per repo
- Use `last_processed_commit_sha` from state.
- Fetch merged commits on `develop` strictly after that SHA using:
  `{last_processed_commit_sha}..origin/develop`
- Process only new commits.

### Non-negotiable window behavior
- Do NOT use timestamp-only filtering for baseline/incremental windows.
- Always use git commit range filtering from baseline marker SHA.
- If baseline SHA equals current `origin/develop` HEAD, new commit count is 0.
- In that case, do not backfill older history.

## Step 1 — Resolve Repo Scope
1. Parse flags and detect mode.
2. Build final repo list:
  - Production mode:
    1. Read `/workspace/config/tracked_repos.yaml`
    2. Use every `repos[].url` entry as the scan scope
    3. This file is a static, manually-maintained resolution of
       `generic-srcrev.inc` (see the file's own header and
       `specs/srcrev-parsing.md` for how it was originally produced) — do
       NOT clone `meta-rdk-broadband` or re-parse `generic-srcrev.inc` in
       this skill; that work is already done and cached there
   - Test mode: allowlist (all or one URL).
3. Print selected repos and ask confirmation.

Production mode failure handling:
- If `/workspace/config/tracked_repos.yaml` is missing or has no `repos` entries,
  STOP and tell the user exactly that — do NOT fall back to cloning
  meta-rdk-broadband or to a guessed repo list.
- Do NOT prompt for password interactively — this mode does not need
  Gerrit credentials at all now, since there is no meta-layer clone here.

## Step 2 — Load State
1. Read `track_for_stable2_state.yaml` if present.
2. If missing, initialize empty state structure in memory.
3. For each selected repo, determine if it is first-run or incremental-run.

## Step 3 — Fetch New Merged Commits on Develop
For each repo:
1. Resolve baseline marker:
  - First-run: latest created component tag commit SHA.
   - Incremental: `last_processed_commit_sha`.
2. Fetch commits from `develop` after baseline using git range:
  - `git rev-list --reverse --merges {baseline_sha}..origin/develop`
  - If PR merge strategy is squash/rebase and merge commits are absent, fallback:
    `git rev-list --reverse {baseline_sha}..origin/develop` then filter by PR pattern.
  - Build `allowed_sha_set` exactly from `{baseline_sha}..origin/develop`.
3. Keep only merge-related commits relevant for PR flow:
   - Preferred source: PRs merged into develop.
   - Fallback: commits whose title contains `(#\d+)`.
4. Exclusion rule:
  - If a commit SHA equals baseline SHA, exclude it.
  - Never include commits reachable only on the left side of the range.
  - Hard gate: before finalizing candidates, drop any commit whose SHA is NOT in `allowed_sha_set`.
  - Hard gate: if a PR lookup returns a commit/PR not in `allowed_sha_set`, discard it and log `out_of_window_filtered`.
5. For each selected commit capture:
   - repo
   - commit_sha (full 40 chars)
   - commit_time_utc
   - commit_title
   - pr_number (if present)

## Step 4 — Extract Jira IDs
From each candidate commit, extract Jira in this order:
1. PR title
2. PR body
3. Merge commit title/message

PR metadata safety:
- Only query PR metadata for PR numbers derived from already-selected candidate commits.
- Never enumerate or backfill repo-wide PR history for Jira extraction.
- If PR metadata cannot be resolved for a candidate commit, continue with commit message extraction only.

Pattern (generic, not RDK-only): `[A-Z0-9]+-\d+`
Examples that MUST match: `RDKB-64184`, `XB10-2860`, `CBR2-1234`, `TCXB7-7222`

For each commit store:
- jira_id (or `NO-JIRA`)
- extraction_source (`pr_title|pr_body|commit_message|none`)

## Step 5 — Build Tracking Output

**NOTE:** This step prepares data structures for internal use and Jira labeling.
The user-facing report format is specified in Step 8 (detailed per-repo with baseline tags).

Produce a table grouped by repo:

Columns:
- Repo
- PR #
- Commit SHA
- Commit Time (UTC)
- Jira ID
- Extraction Source
- Commit Title

Also produce deduplicated Jira summary:
- total unique Jira IDs (excluding `NO-JIRA`)
- list of Jira IDs and occurrence counts

**Do NOT print this table to the user yet - it's for internal use only.**
The user-facing report comes in Step 8.

## Step 6 — Add `{label}` Jira Label

Target Jira scope:
- Use unique Jira IDs found in Step 4 (exclude `NO-JIRA`).
- For each Jira, fetch jira_type and parent_jira:
  ```bash
  python3 scripts/jira_rest.py get-issue <KEY> --fields issuetype,parent
  ```
  `jira_type` = `.fields.issuetype.name`; `parent_jira` = `.fields.parent.key` (absent if no parent).
- If jira_type is Task or Sub-task and parent_jira exists, include parent_jira too.
- Deduplicate final Jira target set.

Safety confirmation (non-dry-run only):
- Print the full sorted Jira target list before labeling.
- Print summary counts: total targets, primary Jira count, parent Jira count.
- Ask user: `Proceed to add {label} label to these Jira tickets? (yes/no)`
- Only continue when user responds exactly `yes`.
- On `no` (or any non-yes response), skip Step 6 mutations and continue to final report with `Label added: 0`.

Label operation:
- Label to add: `{label}`
- For each target Jira, first read current labels:
  ```bash
  python3 scripts/jira_rest.py get-issue <KEY> --fields labels
  ```
  (`.fields.labels` is the current list.)
- If `{label}` already exists, do NOT add again (count as already-labeled).
- If missing, add it (add-only — this API call never touches other labels):
  ```bash
  python3 scripts/jira_rest.py add-label <KEY> {label}
  ```
- Keep all existing labels unchanged (manual/user labels must be preserved).
- Never remove, overwrite, or reset any existing labels.

Failure handling:
- If `jira_rest.py` exits non-zero for one ticket (its stderr is a JSON `{"error": ...}`),
  log `{jira_id, reason}` and continue.
- Do not fail whole run for per-ticket failures.

Dry-run behavior for Step 6:
- Print target Jira IDs and which would be newly labeled vs already-labeled.
- Do NOT call `jira_rest.py add-label`.
- Do NOT ask for confirmation prompt in dry-run.

## Step 7 — Update State (skip in dry-run)
For each repo:
- If new commits were found:
  - Set `last_processed_commit_sha` to newest processed commit SHA.
  - Set `last_processed_commit_time_utc` to newest commit time.
  - Set `last_run_new_commits` count.
- If none found:
  - If marker already exists: keep existing marker unchanged.
  - If this is first-run and marker does not exist yet:
    - Set `last_processed_commit_sha` = baseline commit SHA
    - Set `last_processed_commit_time_utc` = baseline commit time
    - This prevents repeated backfill on next run.
  - Set `last_run_new_commits: 0`.

Set `last_success_run_utc` at end of successful run.
Write back `track_for_stable2_state.yaml`.

## Step 8 — Final Report

### MANDATORY SECTION 1: Per-Repository Detailed Report

**⚠️ CRITICAL REQUIREMENT - DO NOT SKIP THIS SECTION ⚠️**

This detailed report is MANDATORY. User MUST see baseline information for every repo to validate:
- Which tag was selected (is it correct?)
- Which commits were found (are any missing?)
- Why certain commits were included/excluded

**⚠️ PRINT THIS AS TEXT IN THE CONVERSATION - NOT JUST IN TERMINAL OUTPUT ⚠️**

Terminal output from Python scripts can be collapsed/hidden in the UI. You MUST print this detailed report as regular text in your response so the user can see it without expanding collapsed sections.

**Implementation instructions:**

1. **Load the scan results from the JSON file or data structure**
2. **Iterate through ALL repositories scanned** (even those with 0 new commits)
3. **Print the following information for each repository as TEXT (not just terminal output):**

**A. Repository header (always print):**
```
═════════════════════════════════════════════════════════════════
Repository: {owner/repo}
─────────────────────────────────────────────────────────────────
```

**B. Baseline information (ALWAYS PRINT - THIS IS CRITICAL):**

If first run (baseline from tag):
```
Baseline Type:     Latest semver tag (first run)
Tag Selected:      {tag_name}          ← Example: "2.3.0"
Tag Created On:    {YYYY-MM-DD}        ← Example: "2026-07-22"
Tag Commit SHA:    {8_char_sha}        ← Example: "abc12345"
```

If incremental run (baseline from state):
```
Baseline Type:     Previous processed commit (incremental run)
Last Processed:    {8_char_sha}        ← Example: "xyz98765"
Processed On:      {YYYY-MM-DD HH:MM}  ← Example: "2026-07-30 10:30"
```

**C. New commits section:**
```
New commits found: {N}
```

**D. If N > 0, list ALL commits (one row per commit, NO deduplication):**

Use simple text table format:
```
Commits (listed individually, including NO-JIRA):
  PR #92  | RDKB-66031 | 48538a2 | RDKB-66031: change fallback address...
  PR #91  | NO-JIRA    | 8262cc9 | Revert "fix: change fallback address..."
  PR #89  | NO-JIRA    | 6b8ff78 | fix: change fallback address...
```

**E. If N = 0:**
```
Status: ✓ No new commits (repository up to date)
```

**F. Section footer:**
```
═════════════════════════════════════════════════════════════════

```

**CRITICAL RULES:**
- ✅ Print section for EVERY repo (including those with 0 commits)
- ✅ ALWAYS show baseline tag name and date (user needs this to validate)
- ✅ List EVERY commit as a separate row (don't group by Jira ID)
- ✅ Include NO-JIRA commits in the listing
- ✅ Use 8-character SHAs for readability
- ✅ **Print this as TEXT in your response** (use markdown code blocks if needed)
- ✅ **DO NOT rely on terminal/Python script output alone** (it may be collapsed/hidden)
- ❌ Do NOT create a summary table only (user needs per-repo details)
- ❌ Do NOT deduplicate commits with same Jira ID
- ❌ Do NOT skip repos with 0 new commits
- ❌ Do NOT assume terminal output is visible (always print in conversation text)

**HOW TO PRINT THIS:**

Option 1: Print as markdown text in your response:
```
After running your scan, explicitly print the detailed report like this:

"Here is the detailed per-repository breakdown:

═════════════════════════════════════════════════════════════════
Repository: rdkcentral/lan-manager-lite
─────────────────────────────────────────────────────────────────
Baseline Type:     Latest semver tag (first run)
Tag Selected:      2.1.0
Tag Created On:    2026-06-15
Tag Commit SHA:    abc12345
New commits found: 3

Commits (listed individually, including NO-JIRA):
  PR #92  | RDKB-66031 | 48538a2 | RDKB-66031: change fallback...
  PR #91  | NO-JIRA    | 8262cc9 | Revert \"fix: change...\"
  PR #89  | NO-JIRA    | 6b8ff78 | fix: change fallback...
═════════════════════════════════════════════════════════════════

... (continue for ALL repos)
"
```

Option 2: Save to a temporary file and read it back, then print the contents in your response.

Option 3: Process the data structure and format it as text in your response.

**THE KEY POINT:** The user must see this detailed report in the conversation, not just in collapsible terminal output.

**Example output for validation:**
```
═════════════════════════════════════════════════════════════════
Repository: rdkcentral/lan-manager-lite
─────────────────────────────────────────────────────────────────
Baseline Type:     Latest semver tag (first run)
Tag Selected:      2.1.0
Tag Created On:    2026-06-15
Tag Commit SHA:    abc12345
New commits found: 3

Commits (listed individually, including NO-JIRA):
  PR #92  | RDKB-66031 | 48538a2 | RDKB-66031: change fallback...
  PR #91  | NO-JIRA    | 8262cc9 | Revert "fix: change..."
  PR #89  | NO-JIRA    | 6b8ff78 | fix: change fallback...
═════════════════════════════════════════════════════════════════

═════════════════════════════════════════════════════════════════
Repository: rdkcentral/test-and-diagnostic
─────────────────────────────────────────────────────────────────
Baseline Type:     Latest semver tag (first run)
Tag Selected:      1.5.2
Tag Created On:    2026-07-01
Tag Commit SHA:    def56789
New commits found: 0
Status: ✓ No new commits (repository up to date)
═════════════════════════════════════════════════════════════════
```

This format allows user to immediately see:
- ✓ Which tag was chosen for each repo
- ✓ Whether the tag selection was correct
- ✓ ALL commits found (not just unique Jira IDs)
- ✓ Repos that are up to date (0 new commits)

### MANDATORY SECTION 2: Jira Label Summary

After printing ALL repository details above, summarize Jira targets:
```
Jira Tickets to be labeled with '{label}':

Primary Jira IDs ({N} tickets):
  RDKB-12345 (from 2 commits)
  RDKB-12346 (from 1 commit)
  RDKB-12347 (from 3 commits)

Parent Jira IDs ({M} tickets):
  RDKB-12000 (parent of RDKB-12345)
  RDKB-12001 (parent of RDKB-12346)

Total targets: {T} tickets

Already labeled: {B} tickets
  RDKB-12348 (already has {label})

New labels to add: {A} tickets
```

**Production mode confirmation:**
- Ask user: `Review the commits and Jira tickets above. Proceed to add {label} label? (yes/no)`
- Wait for exact response `yes`
- On `no` or any other response: Skip labeling, print "Label operation cancelled by user"

**Dry-run mode:**
- Print: "DRY-RUN: Would add labels to {A} tickets (skipped)"
- Do NOT ask for confirmation

### Summary Report

**⚠️ IMPORTANT: This summary comes AFTER the detailed per-repository report above ⚠️**

Do NOT print ONLY this summary - the detailed report with baseline tags is mandatory and must be visible to the user.

**Before printing this summary, verify:**
- [ ] Did I print the detailed per-repository report as TEXT in my response?
- [ ] Can the user see baseline tag information for EVERY repository?
- [ ] Can the user see ALL individual commits (including NO-JIRA)?
- [ ] Or did I only show terminal output that might be collapsed?

**If you only ran terminal commands and didn't print the detailed report as text in your response, you MUST go back and print it now.**

Print:
```
═════════════════════════════════════════════════════════════════
/track-for-stable2 COMPLETE
═════════════════════════════════════════════════════════════════

Mode: {production|test-repo|dry-run}
Execution Time: {duration}

Repository Summary:
  Repos scanned: {R}
  Repos with new commits: {R_new}
  Repos up to date: {R_uptodate}

Commit Summary:
  Total new commits: {C}
  Commits with Jira: {C_jira}
  Commits without Jira (NO-JIRA): {N}

Jira Summary:
  Unique Jira IDs found: {J}
  Primary tickets: {J_primary}
  Parent tickets: {J_parent}
  Total targets: {T}

Label Operation:
  Labels added: {A} (or 0 if dry-run/cancelled)
  Already had label: {B}
  Failures: {F}

State:
  State file: track_for_stable2_state.yaml
  State updated: {yes|no (dry-run)}

═════════════════════════════════════════════════════════════════
```

If no new commits found:
```
═════════════════════════════════════════════════════════════════
/track-for-stable2 COMPLETE - No new commits
═════════════════════════════════════════════════════════════════
All repositories are up to date.
No new develop merges found after last processed markers.
═════════════════════════════════════════════════════════════════
```

---

### COMPLETE OUTPUT EXAMPLE (Step 8 - All Sections Combined)

**This is what the user should see when Step 8 is complete:**

```
[After running your scans and gathering data, print this:]

═══════════════════════════════════════════════════════════════════
DETAILED PER-REPOSITORY BREAKDOWN
═══════════════════════════════════════════════════════════════════

═════════════════════════════════════════════════════════════════
Repository: rdkcentral/lan-manager-lite
─────────────────────────────────────────────────────────────────
Baseline Type:     Latest semver tag (first run)
Tag Selected:      2.1.0
Tag Created On:    2026-06-15
Tag Commit SHA:    abc12345
New commits found: 3

Commits (listed individually, including NO-JIRA):
  PR #92  | RDKB-66031 | 48538a2 | RDKB-66031: change fallback...
  PR #91  | NO-JIRA    | 8262cc9 | Revert "fix: change..."
  PR #89  | NO-JIRA    | 6b8ff78 | fix: change fallback...
═════════════════════════════════════════════════════════════════

═════════════════════════════════════════════════════════════════
Repository: rdkcentral/test-and-diagnostic
─────────────────────────────────────────────────────────────────
Baseline Type:     Latest semver tag (first run)
Tag Selected:      1.5.2
Tag Created On:    2026-07-01
Tag Commit SHA:    def56789
New commits found: 0
Status: ✓ No new commits (repository up to date)
═════════════════════════════════════════════════════════════════

[... continue for ALL 39 repos ...]

═══════════════════════════════════════════════════════════════════
JIRA LABEL SUMMARY
═══════════════════════════════════════════════════════════════════

Jira Tickets to be labeled with 'track_for_stable2':

Primary Jira IDs (18 tickets):
  RDKB-66031 (from 1 commit in lan-manager-lite)
  RDKB-65709 (from 4 commits across utopia, sysint-broadband, etc.)
  [... list all ...]

Parent Jira IDs (1 ticket):
  RDKB-65586 (parent of RDKB-65709)

Total targets: 19 tickets
Already labeled: 0
New labels to add: 19 (DRY-RUN - no changes made)

═════════════════════════════════════════════════════════════════
/track-for-stable2 COMPLETE
═════════════════════════════════════════════════════════════════

Mode: dry-run
Repos scanned:          39
Repos with new commits: 20
Repos up to date:       19
Total new commits:      66
Commits with Jira:      19
Commits without Jira:   47

Jira Summary:
  Unique Jira IDs found: 18
  Primary tickets:       18
  Parent tickets:         1
  Total targets:         19

Label Operation:
  Labels added:          0 (DRY-RUN)
  Already had label:     0
  Failures:              0

State:
  State file:            track_for_stable2_state.yaml
  State updated:         no (dry-run)
═════════════════════════════════════════════════════════════════
```

**Notice the output has 3 distinct sections:**
1. **DETAILED PER-REPOSITORY BREAKDOWN** - Shows baseline tags and ALL commits for EVERY repo
2. **JIRA LABEL SUMMARY** - Shows which tickets will be labeled
3. **FINAL SUMMARY** - Overall statistics

**All three sections must be visible to the user as TEXT in the conversation.**

---

**EXECUTION ORDER CHECKLIST - Verify you completed ALL sections:**
- [ ] Step 8.1: Printed detailed per-repository report with baseline tags (MANDATORY)
- [ ] Step 8.2: Printed Jira label summary (if any Jira IDs found)
- [ ] Step 8.3: Asked for confirmation in production mode (if labeling)
- [ ] Step 8.4: Printed final summary report

If you printed ONLY the summary without the detailed per-repo section, you skipped a mandatory step.
Go back and print the detailed report with baseline tag information for EVERY repository.

**IMPORTANT: Stop here and return control to orchestrator/user.**
- Do NOT automatically proceed to next phase
- If called by orchestrator, let it handle confirmation
- If called directly, user must manually run next skill

## Out of Scope for this phase
