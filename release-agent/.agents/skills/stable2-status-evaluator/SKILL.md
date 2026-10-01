---
name: stable2-status-evaluator
description: "Use when evaluating Jira tickets for stable2 readiness. Fetches detailed status (RM Approved, parent/linked statuses, dependencies), analyzes readiness criteria, and groups tickets by feature/dependency chains. Takes input from /stable2-candidates or manual ticket list. Don't use for initial filtering or PR operations."
license: Comcast
argument-hint: "Optional: provide ticket keys directly (generic format '[A-Z0-9]+-\\d+', e.g., XB10-2860, CBR2-1234, RDKB-64184) or '--file stable2_candidates.yaml' to read from saved list"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /stable2-status-evaluator

> **⚠️ THERE IS NO DEDICATED SCRIPT FOR THIS SKILL** — do not look for or try
> to run `stable2_status_evaluator.py`/`.sh` or anything similar; it does not
> exist. This skill's readiness analysis (fetching status, grouping by
> feature/dependency chains) is your own reasoning over data fetched via
> `scripts/jira_rest.py` below — not a separate script.

## Purpose
Evaluate stable2 candidate tickets for cherry-pick readiness by:
1. Fetching detailed Jira status (current, parent, linked)
2. Determining RM Approved state using changelog analysis
3. Extracting dependency notes from comments
4. Grouping tickets by feature/epic/dependency chain
5. Filtering by readiness criteria (RM Approved = Yes)

Output: Organized ticket groups ready for cherry-picking decisions.

## Critical Constraints — Read First
- Use `python3 scripts/jira_rest.py` (the shared Jira REST helper, credentials from
  `ccp_jira.env`) for all Jira operations. Never call Jira MCP tools or read
  `JIRA_TOKEN`/`JIRA_USER`/etc. directly.
- If `ccp_jira.env` is missing or `jira_rest.py` reports a credential error, STOP
  immediately and tell the user exactly that (see the script's own error message).
- This is a READ-ONLY analysis skill — does NOT modify tickets or add labels.
- No dedicated script exists for this skill's readiness analysis. The only script
  referenced anywhere in this file is `scripts/jira_rest.py` above — the actual
  evaluation logic (readiness criteria, grouping by feature/dependency chains) is your
  own reasoning over that data, not a separate script. Do not assume one exists.

## Before Starting
1. Read `/workspace/config/config.yaml` — all environment values come from here
2. Determine ticket source:
   - If `--file <path>` provided: load tickets from YAML file (e.g., stable2_candidates.yaml)
   - If ticket keys provided as comma-separated argument: use those
   - If neither: prompt user for input
3. Jira key format rule (generic, not RDK-only): `[A-Z0-9]+-\d+`
  - Examples that MUST be accepted: `XB10-2860`, `CBR2-1234`, `RDKB-64184`
4. **IMPORTANT:** This skill analyzes ALL tickets provided (from file or arguments)
   - It does NOT filter by --test-repo flag (that's for GitHub repos only)
   - Test tickets (like RDKB-66213) will be analyzed if included in input
   - To exclude test tickets, remove them from the input file or don't include them in the argument list

## Input Sources

### Option 1: From saved candidate list
```bash
/stable2-status-evaluator --file stable2_candidates.yaml
```

Load tickets from:
```yaml
tickets:
  - key: "RDKB-64184"
  - key: "RDKB-62906"
```

### Option 2: Manual ticket list
```bash
/stable2-status-evaluator RDKB-64184,RDKB-62906,RDKV-5421
```

Parse comma-separated keys, trim whitespace.

### Option 3: Interactive
If no input provided, ask:
```
Enter Jira ticket keys (comma-separated):
Example: RDKB-64184, RDKB-62906, RDKV-5421
```

## Step 1 — Fetch Detailed Jira Data

For each ticket, fetch with the shared REST helper, requesting the changelog too
(needed for the RM-Approved history check in step 4 below):

```bash
python3 scripts/jira_rest.py get-issue <KEY> \
  --fields key,summary,status,issuetype,parent,issuelinks,labels,updated,created \
  --expand changelog
```

### Fields retrieved (same set as before):
- **key**: Jira ticket ID
- **summary**: Ticket title
- **status**: Current Jira status
- **issuetype**: Bug, Story, Task, Epic, Sub-task
- **parent**: Parent ticket key (if subtask)
- **issuelinks**: All linked issues with link types
- **labels**: All labels
- **updated**: Last update timestamp
- **created**: Creation timestamp

### For each ticket:
1. Get current ticket details (call above)
2. Get parent ticket status (one level up only) — same `get-issue` call on the parent key
3. Get each linked ticket's status (one level only — do not recurse) — same call per linked key
4. Check RM Approved status using this logic, reading `.changelog.histories` from the
   response above (each history entry has `.items[]` with `field: "status"` transitions):
   - Current status is one of: `RM Approved`, `Ready for Release Test`,
     `Ready for Patch Test`, `Verified in Patch Stable`, `Verified in Release`
     → rm_approved = `Yes`
   - Current status has moved back FROM one of the above (check changelog)
     → rm_approved = `Moved Back` — NOT treated as approved
   - Never reached RM Approved in changelog
     → rm_approved = `No`
5. Fetch comments and look for dependency language:
   ```bash
   python3 scripts/jira_rest.py get-comments <KEY>
   ```
   Scan `.comments[].body` for:
   `goes with`, `depends on`, `needs`, `blocked by`, `cherry-pick`,
   `companion`, `paired with`, `part of`
   Extract the relevant sentence and any other Jira IDs mentioned.
   If nothing relevant found: notes = `NA`

### Error handling:
- 401 (Unauthorized): Stop immediately with auth error message (see stable2-candidates for template)
- 404 (Not Found): Mark ticket as `NOT_FOUND`, continue with others
- ID migration (API returns different ticket than requested): Use returned data, note original ID

If any `jira_rest.py` call reports an HTTP 401: Stop all Jira fetches immediately.
Tell the user: "Jira authentication failed. Check the credentials in ccp_jira.env
and try again."

## Step 2 — Analyze Readiness

For each ticket, determine readiness score:

### Criteria:
- **RM Approved = Yes**: ✅ READY
- **RM Approved = No**: ⚠️ NOT READY (needs approval)
- **RM Approved = Moved Back**: 🚫 BLOCKED (regression)
- **Parent exists and Parent Status != Done/Closed**: ⚠️ WAITING (parent incomplete)
- **Linked blockers exist**: Check each linked ticket:
  - If link type is `blocks`/`is blocked by` and linked status != Done/Closed: 🚫 BLOCKED

### Readiness Categories:
1. **READY**: RM Approved = Yes, no blockers, parent done (if exists)
2. **NEEDS_APPROVAL**: RM Approved = No, otherwise ready
3. **BLOCKED**: Has active blockers or moved back from approval
4. **WAITING_PARENT**: Parent ticket not complete

## Step 3 — Group by Feature/Epic

Build dependency graph:

### Grouping logic:
1. **By Epic/Parent**:
   - If ticket has parent: group under parent key
   - If ticket is Epic: create group for all its children

2. **By Dependency Chain**:
   - Find all tickets with `depends on`/`blocked by` relationships
   - Group into chains: ticket → depends on → depends on...

3. **Independent Tickets**:
   - Tickets with no parent, no epic, no dependencies

### Group Output Format:
```yaml
groups:
  - name: "RDKB-60000 - WiFi 6E Support"
    type: "epic"
    tickets:
      - RDKB-64184
      - RDKB-64185
    readiness: "READY" # all tickets in group ready
    
  - name: "RDKB-62900 → RDKB-62906 dependency chain"
    type: "dependency_chain"
    tickets:
      - RDKB-62900 # depends on next
      - RDKB-62906
    readiness: "BLOCKED" # at least one ticket blocked
    
  - name: "Independent tickets"
    type: "independent"
    tickets:
      - RDKV-5421
      - RDKM-1234
    readiness: "MIXED" # some ready, some not
```

## Step 4 — Output Detailed Report

### 4.1 Summary Header
```
─────────────────────────────────────────────────────────────────────
/stable2-status-evaluator
Date:           {current_date}
Tickets:        {N} analyzed
Ready:          {X} ready for cherry-pick
Needs Approval: {Y} awaiting RM approval
Blocked:        {Z} blocked or moved back
─────────────────────────────────────────────────────────────────────
```

### 4.2 Detailed Table
```
Ticket       | Type    | Status        | RM Appr | Parent    | P.Status | Linked      | Notes
────────────────────────────────────────────────────────────────────────────────────────────────
✅ READY
────────────────────────────────────────────────────────────────────────────────────────────────
RDKB-64184   | Story   | RM Approved   | Yes     | -         | -        | RDKB-64185  | Part of WiFi 6E
RDKB-62906   | Bug     | Done          | Yes     | RDKB-62900| Done     | -           | NA

⚠️ NEEDS APPROVAL
────────────────────────────────────────────────────────────────────────────────────────────────
RDKV-5421    | Task    | In Progress   | No      | -         | -        | RDKV-5420   | Depends on 5420

🚫 BLOCKED
────────────────────────────────────────────────────────────────────────────────────────────────
RDKB-60123   | Bug     | Code Dev      | Moved Back | -      | -        | -           | Regression found
RDKM-1234    | Story   | RM Approved   | Yes     | RDKM-1200| In Prog  | -           | Parent incomplete
```

Column widths:
- Ticket: 12
- Type: 8
- Status: 15
- RM Appr: 8
- Parent: 12
- P.Status: 10
- Linked: 12
- Notes: 30 (truncate with `...`)

### 4.3 Grouped View
```
─────────────────────────────────────────────────────────────────────
FEATURE GROUPS
─────────────────────────────────────────────────────────────────────

📦 Group: RDKB-60000 - WiFi 6E Support (Epic)
   Status: ✅ READY (all 3 tickets approved)
   Tickets:
   - RDKB-64184 (Story) - Add WiFi 6E config
   - RDKB-64185 (Bug) - Fix 6GHz channel scan
   - RDKB-64186 (Task) - Update documentation

📦 Group: RDKB-62900 → RDKB-62906 dependency chain
   Status: ✅ READY (all tickets in chain approved)
   Tickets:
   - RDKB-62900 (Story) - Memory leak fix (depends on 62906)
   - RDKB-62906 (Bug) - WiFi agent refactor

📦 Group: Independent tickets (no parent/dependencies)
   Status: ⚠️ MIXED (2 ready, 1 blocked)
   Tickets:
   - RDKV-5421 (Task) - ⚠️ NEEDS_APPROVAL
   - RDKM-1234 (Story) - 🚫 BLOCKED (parent incomplete)
   - RDKC-789 (Bug) - ✅ READY
```

## Step 5 — Save Analysis Results

Save to `/workspace/stable2_status_analysis.yaml`:

```yaml
analysis_date: "2026-08-03"
tickets_analyzed: 15
summary:
  ready: 8
  needs_approval: 4
  blocked: 2
  waiting_parent: 1

tickets:
  - key: "RDKB-64184"
    summary: "Add support for WiFi 6E configuration"
    status: "RM Approved"
    rm_approved: "Yes"
    readiness: "READY"
    parent: null
    parent_status: null
    linked_tickets:
      - key: "RDKB-64185"
        status: "Done"
        link_type: "relates to"
    notes: "Part of WiFi 6E feature set"
  
  - key: "RDKB-62906"
    summary: "Fix memory leak in ccsp-wifi-agent"
    status: "Done"
    rm_approved: "Yes"
    readiness: "READY"
    parent: "RDKB-62900"
    parent_status: "Done"
    linked_tickets: []
    notes: "NA"

groups:
  - name: "RDKB-60000 - WiFi 6E Support"
    type: "epic"
    readiness: "READY"
    tickets: ["RDKB-64184", "RDKB-64185", "RDKB-64186"]
  
  - name: "RDKB-62900 → RDKB-62906 dependency chain"
    type: "dependency_chain"
    readiness: "READY"
    tickets: ["RDKB-62900", "RDKB-62906"]
  
  - name: "Independent tickets"
    type: "independent"
    readiness: "MIXED"
    tickets: ["RDKV-5421", "RDKM-1234", "RDKC-789"]
```

Ask user before saving:
```
Save detailed analysis to stable2_status_analysis.yaml? [Y/n]
(This file can be used by /stable2-pr-labeler to process ready tickets)
```

## Step 6 — Next Steps Guidance

Print recommendations based on results:

```
─────────────────────────────────────────────────────────────────────
RECOMMENDATIONS
─────────────────────────────────────────────────────────────────────

✅ {X} tickets are READY for cherry-pick:
   → Run /stable2-pr-labeler with these ticket keys to trigger GitHub automation

⚠️ {Y} tickets need RM approval:
   → Follow up with RM team for approval, then re-run this analysis

🚫 {Z} tickets are BLOCKED:
   → Resolve blockers or parent dependencies before proceeding

Priority order for cherry-picking:
1. Process complete feature groups first (all tickets ready)
2. Then process dependency chains in order
3. Finally process independent ready tickets
─────────────────────────────────────────────────────────────────────
```

**If analysis was saved to file (`stable2_status_analysis.yaml`), print the file contents:**

```
═════════════════════════════════════════════════════════════════
SAVED FILE: stable2_status_analysis.yaml
═════════════════════════════════════════════════════════════════
{print entire contents of stable2_status_analysis.yaml}
═════════════════════════════════════════════════════════════════

This detailed analysis can be used for:
- Manual review and validation
- Tracking decision history
- Input to PR collection phase
═════════════════════════════════════════════════════════════════
```

## Edge Cases

### No tickets provided
```
Error: No tickets specified.
Usage: /stable2-status-evaluator RDKB-64184,RDKB-62906
   or: /stable2-status-evaluator --file stable2_candidates.yaml
```

### All tickets blocked
```
⚠️ WARNING: All {N} tickets are blocked or awaiting approval.
No tickets are ready for cherry-pick at this time.
Review the detailed status above and resolve blockers.
```

### Circular dependencies detected
```
⚠️ WARNING: Circular dependency detected:
RDKB-64184 → RDKB-64185 → RDKB-64186 → RDKB-64184
These tickets cannot be automatically grouped. Manual review required.
```

## Performance Notes
- Parallel Jira fetches: run multiple `jira_rest.py get-issue` calls concurrently
  (e.g. via subagents/background shells) rather than one at a time
- Typical analysis: 10-50 tickets in 5-10 seconds
- Complex dependency graphs: may need recursive traversal (limit depth to 3 levels)

## Verify Completion
Before declaring done, confirm:
- [ ] All tickets fetched successfully via jira_rest.py
- [ ] RM Approved status calculated correctly (including changelog check)
- [ ] Parent and linked ticket statuses retrieved
- [ ] Dependency notes extracted from comments
- [ ] Readiness categories assigned correctly
- [ ] Tickets grouped by feature/dependency
- [ ] Detailed report printed with all tables
- [ ] Analysis saved to YAML if user confirmed
- [ ] Next steps recommendations provided
