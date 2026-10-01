---
name: stable2-candidates
description: "Use when filtering Jira tickets that have been tracked for stable2 but not yet reviewed. Queries for tickets with a user-selected candidate label AND missing any *_considered label (e.g., stable2_considered, sync_week_33_considered). Outputs a candidate list for stable2 cherry-pick evaluation. Don't use for general Jira queries or tickets already marked as considered."
license: Comcast
argument-hint: "Required at runtime: provide '--candidate-label <label>' or enter label when prompted"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /stable2-candidates

> **⚠️ THERE IS NO DEDICATED SCRIPT FOR THIS SKILL** — do not look for or try
> to run `stable2_candidates.py`/`.sh` or anything similar; it does not exist.
> Every Jira read here goes through `scripts/jira_rest.py` below, called
> directly — nothing else.

## Purpose
Filter and display Jira tickets that:
1. Have the selected candidate label (user-provided)
2. Do NOT have ANY label ending in `_considered` (e.g., `stable2_considered`, `sync_week_33_considered`, etc.)

These are candidate tickets that need evaluation for cherry-picking to support/stable2 branch.

**Note:** This accounts for manual process labels like `sync_week_X_considered` that were used before automation.

## Critical Constraints — Read First
- Use `python3 scripts/jira_rest.py` (the shared Jira REST helper, credentials from
  `ccp_jira.env`) for all Jira operations. Never call Jira MCP tools or read
  `JIRA_TOKEN`/`JIRA_USER`/etc. directly — always go through this script so
  credential handling stays in one place.
- If `ccp_jira.env` is missing or `jira_rest.py` reports a credential error, STOP
  immediately and tell the user exactly that (see the script's own error message).
- This is a READ-ONLY skill — it does NOT add or remove labels.
- No dedicated script exists for this skill. The only script referenced anywhere in
  this file is `scripts/jira_rest.py` above — do not assume a separate one exists.
  To mark tickets as considered, use a separate skill (future: /stable2-mark-considered).
- Jira ticket keys are treated with a generic format (not RDK-only): `[A-Z0-9]+-\d+`
  (examples: `XB10-2860`, `CBR2-1234`, `RDKB-64184`).

## Before Starting
1. Read `/workspace/config/config.yaml` — all environment values come from here
2. Resolve candidate filter label:
   - If user passed `--candidate-label <label>`, use that exact label string
   - Else ask user to choose:
     1) Default label: `track_for_stable2`
     2) Custom test label: `test_release_agent`
     3) Own answer (enter any label)
   - If user chooses 1, set `{candidate_label} = track_for_stable2`
   - If user chooses 2, set `{candidate_label} = test_release_agent`
   - If user chooses 3, prompt: `Enter Jira candidate label:`
   - Empty input is NOT allowed; ask again until non-empty
   - Validate label format: only letters, digits, `_`, `-`, `.`
   - If invalid, ask again until valid
   - Save as `{candidate_label}` for this run
3. **IMPORTANT:** This skill queries ALL Jira tickets with the selected `{candidate_label}`
   - It does NOT filter by --test-repo flag (that's for GitHub repos only)
   - It queries ALL projects (RDKB, RDKV, RDKC, etc.)
   - In test-repo mode, use a dedicated test label via `--candidate-label` (example: `track_for_stable2_test`)

## Step 1 — Build JQL Query

Construct this JQL query:

```jql
labels = "{candidate_label}" ORDER BY updated DESC
```

**Note:** JQL does not support wildcard patterns in label matching (cannot do `labels != *_considered`).
Therefore, we query for ALL tickets with `{candidate_label}` and filter programmatically in Step 4.

This queries ALL projects. Order by most recently updated first.

## Step 2 — Execute Jira Search

Run the JQL query above through the shared REST helper — it auto-paginates past
Jira's own per-request cap on its own, so this always returns every matching
ticket, not just the first page:

```bash
python3 scripts/jira_rest.py search '<jql-from-step-1>' --fields key,summary,status,issuetype,updated,labels
```

Parse the `issues` array from the printed JSON.

If the script exits non-zero with an HTTP 401 in its error: Stop immediately.
Tell the user: "Jira authentication failed. Check the credentials in ccp_jira.env
and try again."

## Step 3 — Extract Key Fields

For each ticket returned, extract:
- **Key**: Jira ticket ID (e.g., `RDKB-64184`)
- **Summary**: Ticket title
- **Status**: Current Jira status
- **Issue Type**: Bug, Story, Task, etc.
- **Updated**: Last update timestamp
- **Labels**: All labels (to confirm `{candidate_label}` is present)

**Do NOT fetch** parent/linked/subtask details in this skill.
That logic belongs in the status evaluator skill.

## Step 4 — Filter and Validate

**CRITICAL:** Filter out tickets with ANY `*_considered` label.

For each ticket returned from JQL:
1. Verify `{candidate_label}` is in labels
2. Check ALL labels on the ticket
3. Exclude ticket if ANY label ends with `_considered` (e.g., `stable2_considered`, `sync_week_33_considered`, `sync_week_34_considered`, etc.)
4. Keep ticket only if it has NO labels ending in `_considered`

**Implementation:**
```python
# Pseudocode for filtering
for ticket in jql_results:
    labels = ticket.get('labels', [])
    
  # Check if candidate label exists
  if candidate_label not in labels:
        skip_ticket  # Shouldn't happen with correct JQL
    
    # Check if ANY label ends with '_considered'
    has_considered_label = any(label.endswith('_considered') for label in labels)
    
    if has_considered_label:
        skip_ticket  # Already been considered (manually or automatically)
    else:
        include_in_results  # This is a candidate
```

**Why this is needed:**
Before automation, manual process used labels like:
- `sync_week_33_considered`
- `sync_week_34_considered`
- etc.

We need to exclude ALL of these, not just `stable2_considered`.

## Step 5 — Output Results

Print a summary header:
```
─────────────────────────────────────────────
/stable2-candidates
Date:      {current_date}
Query:     labels = "{candidate_label}"
Fetched:   {M} tickets from Jira
Filtered:  {F} tickets with *_considered labels
Found:     {N} candidate tickets
─────────────────────────────────────────────
```

Where:
- M = total tickets returned from JQL
- F = tickets excluded because they have a label ending in `_considered`
- N = final candidates (M - F)

If F > 0, show which labels were found:
```
Filtered out tickets had these *_considered labels:
  - stable2_considered ({X} tickets)
  - sync_week_33_considered ({Y} tickets)
  - sync_week_34_considered ({Z} tickets)
```

Print table:
```
Ticket       | Type    | Status        | Summary                                | Updated
──────────────────────────────────────────────────────────────────────────────────────────
RDKB-64184   | Story   | RM Approved   | Add support for WiFi 6E configuration  | 2026-08-01
RDKB-62906   | Bug     | Done          | Fix memory leak in ccsp-wifi-agent     | 2026-07-30
RDKV-5421    | Task    | Closed        | Update mesh controller dependencies    | 2026-07-28
```

Column widths:
- Ticket: 14
- Type: 8
- Status: 15
- Summary: 40 (truncate with `...` if longer)
- Updated: 12 (format as `YYYY-MM-DD`)

Sort by: Updated DESC (already sorted by JQL)

## Step 6 — Save to Session State (Optional)

If user wants to proceed with status evaluation, save the ticket list to:
`/workspace/stable2_candidates.yaml`

Format:
```yaml
query_date: "2026-08-03"
jql_query: "labels = \"{candidate_label}\""
candidate_label: "{candidate_label}"
filter_applied: "Excluded tickets with any label ending in '_considered'"
tickets_fetched: 20
tickets_filtered: 5
ticket_count: 15
tickets:
  - key: "RDKB-64184"
    type: "Story"
    status: "RM Approved"
    summary: "Add support for WiFi 6E configuration"
    updated: "2026-08-01"
  - key: "RDKB-62906"
    type: "Bug"
    status: "Done"
    summary: "Fix memory leak in ccsp-wifi-agent"
    updated: "2026-07-30"
```

Ask user before saving:
```
Save candidate list to stable2_candidates.yaml? [Y/n]
(This file can be used by /stable2-status-evaluator for detailed analysis)
```

**If user confirms save:**
- Write `stable2_candidates.yaml`
- Print: "Saved to stable2_candidates.yaml"

## Step 7 — Print Saved File Contents (if saved)

If file was saved to `stable2_candidates.yaml`, print the complete file contents:

```
═════════════════════════════════════════════════════════════════
SAVED FILE: stable2_candidates.yaml
═════════════════════════════════════════════════════════════════
{print entire contents of stable2_candidates.yaml}
═════════════════════════════════════════════════════════════════
```

This allows user to review exactly what was saved before proceeding to next phase.

## Step 8 — Next Steps Guidance

Print:
```
─────────────────────────────────────────────
Next Steps:
1. Review the candidate list above
2. Run /stable2-status-evaluator to fetch detailed status,
   dependencies, and RM approval state for these tickets
3. After review, run /stable2-pr-labeler to trigger cherry-picks
─────────────────────────────────────────────
```

## Edge Cases

### No candidates found (all filtered out)
```
No candidates found.

Fetched:  {M} tickets with {candidate_label} label
Filtered: {M} tickets (all had *_considered labels)
Result:   0 candidates

All tickets with {candidate_label} have already been considered.

Common *_considered labels found:
  - stable2_considered
  - sync_week_33_considered
  - sync_week_34_considered
```

### All candidates (none filtered)
```
Fetched:  {N} tickets with {candidate_label} label
Filtered: 0 tickets
Result:   {N} candidates (none have been considered yet)
```

### jira_rest.py errors
- 401: Authentication error (see Step 2)
- 400: Invalid JQL syntax — report error message and stop
- Other errors: log and continue with remaining tickets

## Performance Notes
- Typical query returns 10-50 tickets (depends on release cycle frequency)
- `jira_rest.py search` paginates past Jira's per-request cap automatically — no
  manual pagination needed
- Filtering is done programmatically (JQL doesn't support wildcard label matching)

## Verify Completion
Before declaring done, confirm:
- [ ] JQL query executed successfully via jira_rest.py
- [ ] All fetched tickets have selected `{candidate_label}` label
- [ ] Tickets with ANY `*_considered` label were filtered out
- [ ] Final candidates do NOT have any `*_considered` label
- [ ] Table printed with correct formatting
- [ ] Filtering summary shown (fetched vs filtered vs final count)
- [ ] Optional: session state saved if user confirmed
