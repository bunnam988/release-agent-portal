---
name: stable2-release-orchestrator
description: "Orchestrator for stable2 workflow without GitHub PR labeling. Chains: track-for-stable2 → candidates → status-evaluator → pr-lookup → stable2-meta-sync-orchestrator, where GitHub repo cherry-pick script and Gerrit operations are handled as separate intents. Includes confirmation gates. All arguments passed to orchestrator are forwarded to each skill."
mode: primary
license: Comcast
argument-hint: "Optional: '--dry-run' for preview, '--test-repo' for testing with forked repos, '--candidate-label <label>' to scope Jira candidates, '--reset-state' to delete all existing stable2 state/artifact files before starting a fresh cycle, or any other skill-specific arguments"
metadata:
  author: Suganya Sugumar
  source: local
---

# Agent: stable2-release-orchestrator

## Purpose
Automation of the stable2 release workflow using READY evaluation, PR collection, and meta sync. This orchestrator:
1. Tracks tickets merged to develop (adds track_for_stable2 label)
2. Filters candidates not yet considered
3. Evaluates RM Approved status (including parent tickets)
4. Collects PRs for approved tickets + dependencies
5. Runs stable2 meta sync orchestration with separated intents:
  - optional GitHub repo cherry-pick script
  - Gerrit/meta sync operations

## Critical Constraints — Read First
- **Confirmation gates:** User must approve before proceeding to each new phase
- **IMPORTANT:** This orchestrator manages confirmations BETWEEN phases. To use these confirmation gates, you MUST invoke the orchestrator (not individual skills in sequence)
- **If you run skills individually:** Each skill will complete and stop. You must manually run the next skill. No automatic phase-to-phase progression.
- **If you run via orchestrator:** After each phase completes, orchestrator will ask "Proceed to Phase N? [Y/n]" before continuing.
- **Dry-run propagation:** If `--dry-run` is provided, ALL invoked skills run in dry-run mode
- **Test-repo propagation:** If `--test-repo` is provided, ALL invoked skills use test repos
- **No automatic execution:** Each phase requires explicit user approval
- **Safety first:** All skills have their own confirmation gates for Jira/GitHub changes

## Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: TRACKING (Daily/On-Demand)                            │
│ track-for-stable2 → adds track_for_stable2 label               │
│ ✓ Confirmation: Before labeling Jira tickets                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: FILTERING                                              │
│ stable2-candidates → finds selected candidate label             │
│                      (default track_for_stable2) AND NOT        │
│                      *_considered                                │
│ ✓ Read-only, no confirmation needed                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: EVALUATION                                             │
│ stable2-status-evaluator → filters RM Approved, checks parents │
│ ✓ Read-only, no confirmation needed                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: PR COLLECTION                                          │
│ jira-pr-lookup → collects GitHub PRs for tickets + dependents  │
│ ✓ Read-only, no confirmation needed                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 5: META SYNC                                               │
│ stable2-meta-sync-orchestrator                                  │
│ -> ready-considered labeler                                     │
│ -> github stable2 tagging + PKGREV                              │
│ -> SHA resolution + SRCREV                                       │
│ -> optional GitHub repo cherry-pick script                      │
│ -> gerrit-cherrypick-squash with shared topic (Gerrit-only)    │
│ ✓ Confirmation: Before starting Phase 5 and inside each step   │
└─────────────────────────────────────────────────────────────────┘
```

## Before Starting

1. **Read config:** Load `config/config.yaml` (path is relative to the project root, not this agent file)
2. **Parse and store ALL arguments:**
   - Capture EXACTLY what user provides on command line (e.g., `--dry-run`, `--test-repo`, etc.)
   - Store as `{all_args}` to forward to each skill
   - **CRITICAL:** Do NOT add, modify, or inject any additional arguments
   - **CRITICAL:** Each skill receives EXACTLY what user passed to orchestrator, nothing more, nothing less
   - **Exception:** `--reset-state` (see Step 2a below) is consumed by this orchestrator only —
     no individual skill knows what it means, so strip it out of `{all_args}` before forwarding
     to any phase
   - Common user-provided arguments:
     - `--dry-run`: Preview mode (no actual changes)
     - `--test-repo`: Use test/forked repos only
     - `--candidate-label <label>`: Jira candidate filter label override for Phase 2
     - `--reset-state`: Delete existing stable2 state files before starting (see Step 2a)
     - Any other skill-specific arguments (only if user provides them)

2a. **If `--reset-state` is present**, before doing anything else:
   - Check which of these known state/artifact files exist in the project root (do NOT touch
     `config/config.yaml` or `config/tracked_repos.yaml` — those are static configuration, not
     run state):
     ```
     release_session.yaml
     last_run.txt
     track_for_stable2_state.yaml
     stable2_candidates.yaml
     stable2_status_analysis.yaml
     pr_list.yaml
     stable2_ready_tickets.txt
     stable2_release_tags.yaml
     stable2_srcrev_updates.yaml
     stable2_pr_labeling_results.yaml
     ```
   - Print exactly which of these exist and would be deleted:
     ```text
     ╔═════════════════════════════════════════════════════════════════╗
     ║  --reset-state: the following files will be permanently deleted ║
     ╚═════════════════════════════════════════════════════════════════╝
       - release_session.yaml
       - pr_list.yaml
       - stable2_candidates.yaml
       (only files that actually exist are listed; if none exist, say so and skip the prompt)

     This resets ALL cycle progress, including track-for-stable2's incremental markers --
     the next run will re-bootstrap from scratch. This cannot be undone.
     Delete these files and start a fresh cycle? [Y/n]
     ```
   - If the user answers anything other than `y`/`Y`, stop the entire orchestrator run here —
     do NOT proceed to Phase 1 with stale state left in place unless the user explicitly declines
     the reset and wants to continue with existing state (ask a follow-up: "Continue with existing
     state instead? [Y/n]" -- if that's also declined, abort).
   - If `--dry-run` is also present, do NOT delete anything — print the exact same list prefixed
     with `DRY-RUN: would delete` and continue to Phase 1 with existing state untouched (dry-run
     never mutates local files any more than it mutates Jira/GitHub/Gerrit).
   - Otherwise, delete each listed file and print a confirmation per file:
     ```text
     ✓ Deleted release_session.yaml
     ✓ Deleted pr_list.yaml
     - stable2_release_tags.yaml (did not exist, skipped)
     ```
3. **Print banner:**

```
╔═════════════════════════════════════════════════════════════════╗
║                                                                 ║
║         STABLE2 RELEASE ORCHESTRATOR                            ║
║                                                                 ║
║  End-to-end automation for stable2 release workflow            ║
║                                                                 ║
╚═════════════════════════════════════════════════════════════════╝

Mode: {DRY-RUN / TEST-REPO / PRODUCTION}

This orchestrator will execute 5 phases:
  1. Track develop merges (track-for-stable2)
  2. Filter candidates (stable2-candidates)
  3. Evaluate RM status (stable2-status-evaluator)
  4. Collect PRs (jira-pr-lookup)
  5. Meta sync + cherry-pick choice + Gerrit sync (stable2-meta-sync-orchestrator)

You will be asked to confirm before proceeding to each phase.
```

If `--dry-run`:
```
╔═════════════════════════════════════════════════════════════════╗
║  DRY-RUN MODE ACTIVE                                            ║
║  All skills will run in preview mode.                           ║
║  No changes will be made to Jira, GitHub, or Gerrit.            ║
║                                                                 ║
║  NOTE: Will scan ALL production rdkcentral repos.               ║
║  To preview with test repos only, use:                          ║
║    --dry-run --test-repo                                        ║
╚═════════════════════════════════════════════════════════════════╝
```

If `--test-repo`:
```
╔═════════════════════════════════════════════════════════════════╗
║  TEST-REPO MODE ACTIVE                                          ║
║  All skills will use test/forked repositories only.             ║
║  Real rdkcentral repos will NOT be touched.                     ║
║                                                                 ║
║  NOTE: This filters GitHub/Gerrit REPOSITORIES only.            ║
║  Jira tickets are filtered by candidate label in Phase 2.       ║
║  Default label: track_for_stable2                               ║
║  To isolate test Jira tickets, pass:                            ║
║    --candidate-label <your_test_label>                          ║
║                                                                 ║
║  Test repos: Suganya-Sugumar/* +                               ║
║              bunnam988/provisioning-and-management (6 total)   ║
╚═════════════════════════════════════════════════════════════════╝
```

---

## PHASE 1: Track Develop Merges

**Skill:** `track-for-stable2`

**Purpose:** Scan develop branch for new commits since last run, add `track_for_stable2` label to Jira tickets.

### Step 1.1 — Ask to proceed

```
─────────────────────────────────────────────────────────────────
PHASE 1: TRACK DEVELOP MERGES
─────────────────────────────────────────────────────────────────

This phase will:
  ✓ Scan all RDK component repos for commits merged to develop
  ✓ Extract Jira IDs from PRs
  ✓ Add 'track_for_stable2' label to discovered Jira tickets
  
Proceed with Phase 1? [Y/n]
```

**If user enters 'n':**
- Exit orchestrator with message: "Orchestration stopped by user."

**If user enters 'Y':**
- Proceed to Step 1.2

### Step 1.2 — Invoke track-for-stable2

**Build command:**
```bash
/track-for-stable2 {all_args}
```

Where:
- `{all_args}` = ALL arguments passed to orchestrator (e.g., `--dry-run --test-repo`)
- track-for-stable2 will use supported arguments and ignore others

**Invoke skill** and wait for completion.

**Note:** track-for-stable2 has its own confirmation gate before labeling Jira tickets.

### Step 1.3 — Review results

After track-for-stable2 completes, print:
```
─────────────────────────────────────────────────────────────────
PHASE 1 COMPLETE
─────────────────────────────────────────────────────────────────

Results from track-for-stable2:
  Components scanned: {N}
  Commits found:      {M}
  Jira tickets found: {K}
  Labels added:       {K} (or 0 if --dry-run)

Proceed to Phase 2? [Y/n]
```

**If user enters 'n':** Exit orchestrator
**If user enters 'Y':** Continue to Phase 2

---

## PHASE 2: Filter Candidates

**Skill:** `stable2-candidates`

**Purpose:** Find tickets with selected candidate label (user-provided) but NOT any `*_considered` label.

### Step 2.1 — Ask to proceed

```
─────────────────────────────────────────────────────────────────
PHASE 2: FILTER CANDIDATES
─────────────────────────────────────────────────────────────────

This phase will:
  ✓ Query Jira for tickets with selected candidate label
  ✓ Exclude tickets with any *_considered label
  ✓ Save candidate list to stable2_candidates.yaml
  
Proceed with Phase 2? [Y/n]
```

**If user enters 'n':** Exit orchestrator
**If user enters 'Y':** Proceed to Step 2.2

### Step 2.2 — Ask Candidate Label (MANDATORY)

Before invoking `stable2-candidates`, ALWAYS ask user which Jira label to use for candidate filtering.

Prompt rules:
- If `--candidate-label <label>` is already present in `{all_args}`:
  - Do NOT ask again.
  - Use that label as `{candidate_label}`.
- Else:
  - Ask user to choose:
    1) Default label: `track_for_stable2`
    2) Custom test label: `test_release_agent`
    3) Own answer (enter any label)
  - If user chooses 1, set `{candidate_label} = track_for_stable2`
  - If user chooses 2, set `{candidate_label} = test_release_agent`
  - If user chooses 3, prompt: `Enter Jira candidate label:`
  - Empty input is NOT allowed.
  - Keep prompting until user provides a non-empty valid label.

Validation:
- Label must contain only letters, digits, `_`, `-`, `.`
- If invalid, ask again until valid input is provided.

Store final choice as `{candidate_label}` and print:
`Using Jira candidate label: {candidate_label}`

### Step 2.3 — Invoke stable2-candidates

**Build command:**
```bash
/stable2-candidates --candidate-label {candidate_label} {all_args_without_candidate_label}
```

Where:
- `{all_args}` = ALL arguments passed to orchestrator
- `{all_args_without_candidate_label}` = ALL orchestrator arguments except `--candidate-label` (to avoid duplicate values)
- stable2-candidates will use supported arguments and ignore others
- The explicit `--candidate-label {candidate_label}` from Step 2.2 is the source of truth for this phase
- Note: This is read-only, --dry-run has no effect but can be passed through

**Invoke skill** and wait for completion.

### Step 2.4 — Review results

After stable2-candidates completes:
```
─────────────────────────────────────────────────────────────────
PHASE 2 COMPLETE
─────────────────────────────────────────────────────────────────

Results from stable2-candidates:
  Candidates found: {N}
  Saved to:         stable2_candidates.yaml
```

**If N candidates = 0:**
```
REPORT: No candidates found.
REASON: All tickets with {candidate_label} have already been considered.

Phases 3-5 will have nothing to process.

Proceed to Phase 3 anyway? [Y/n]
```

**Otherwise:**
```
Proceed to Phase 3? [Y/n]
```

**If user enters 'n':** Exit orchestrator
**If user enters 'Y':** Continue to Phase 3

---

## PHASE 3: Evaluate RM Approved Status

**Skill:** `stable2-status-evaluator`

**Purpose:** Fetch detailed status, filter for RM Approved tickets, check parent ticket status.

### Step 3.1 — Ask to proceed

```
─────────────────────────────────────────────────────────────────
PHASE 3: EVALUATE RM APPROVED STATUS
─────────────────────────────────────────────────────────────────

This phase will:
  ✓ Fetch detailed Jira status for all candidates
  ✓ Check RM Approved state (including changelog)
  ✓ Verify parent ticket status
  ✓ Identify dependencies and blockers
  ✓ Filter for READY tickets only (RM Approved = Yes)
  
Proceed with Phase 3? [Y/n]
```

**If user enters 'n':** Exit orchestrator
**If user enters 'Y':** Proceed to Step 3.2

### Step 3.2 — Invoke stable2-status-evaluator

**Build command:**
```bash
/stable2-status-evaluator --file stable2_candidates.yaml {all_args}
```

Where:
- `{all_args}` = ALL arguments passed to orchestrator
- stable2-status-evaluator will use supported arguments and ignore others
- Note: This is read-only, --dry-run has no effect but can be passed through

**Invoke skill** and wait for completion.

### Step 3.3 — Extract READY tickets

From stable2-status-evaluator output, extract tickets where `readiness = "READY"`.

**Filter criteria (as per skill logic):**
- RM Approved = Yes
- No active blockers
- Parent ticket done (if exists)

Save READY ticket list to: `stable2_ready_tickets.txt` (comma-separated keys)

### Step 3.4 — Review results

```
─────────────────────────────────────────────────────────────────
PHASE 3 COMPLETE
─────────────────────────────────────────────────────────────────

Results from stable2-status-evaluator:
  Total analyzed:     {N}
  RM Approved (READY): {K}
  Needs Approval:     {M}
  Blocked:            {B}
```

**If K READY tickets = 0:**
```
REPORT: No tickets are ready for stable2 cherry-pick.
REASON: All tickets need RM approval or have blockers.

Phases 4-5 will have nothing to process.
Review the status report and try again later.

Proceed to Phase 4 anyway? [Y/n]
```

**Otherwise:**
```
READY tickets (will proceed to PR collection):
  {list of K ticket keys}

Saved to: stable2_ready_tickets.txt

Proceed to Phase 4? [Y/n]
```

**If user enters 'n':** Exit orchestrator
**If user enters 'Y':** Continue to Phase 4

---

## PHASE 4: Collect GitHub PRs

**Skill:** `jira-pr-lookup`

**Purpose:** For each READY ticket, collect all merged GitHub PRs (including dependencies).

### Step 4.1 — Ask to proceed

```
─────────────────────────────────────────────────────────────────
PHASE 4: COLLECT GITHUB PRS
─────────────────────────────────────────────────────────────────

This phase will:
  ✓ For each READY ticket, traverse dependencies/linked tickets
  ✓ Collect all merged GitHub PRs to develop branch
  ✓ Deduplicate PR list
  ✓ Save to pr_list.yaml
  
Processing {K} READY tickets...

Proceed with Phase 4? [Y/n]
```

**If user enters 'n':** Exit orchestrator
**If user enters 'Y':** Proceed to Step 4.2

### Step 4.2 — Invoke jira-pr-lookup for each READY ticket

**For each ticket in READY list:**

Build command:
```bash
/jira-pr-lookup {TICKET-KEY} {all_args}
```

Where:
- `{all_args}` = ALL arguments passed to orchestrator
- jira-pr-lookup will use supported arguments (`--dry-run`, `--test-repo`) and ignore others

**Note:** Run sequentially (not in parallel) to avoid overwhelming Jira API.

**Progress indicator:**
```
Processing ticket 1/{K}: RDKB-64184... Done (12 PRs found)
Processing ticket 2/{K}: RDKB-62906... Done (5 PRs found)
...
```

### Step 4.3 — Merge and deduplicate PR lists

Collect all PRs from all ticket runs, deduplicate by URL.

Save to: `pr_list.yaml` (consolidated list)

### Step 4.4 — Review results

```
─────────────────────────────────────────────────────────────────
PHASE 4 COMPLETE
─────────────────────────────────────────────────────────────────

Results from jira-pr-lookup:
  Tickets processed:  {K}
  Total PRs found:    {M}
  Unique PRs:         {N} (after deduplication)
```

**If N unique PRs = 0:**
```
REPORT: No GitHub PRs found for the READY tickets.
REASON: All changes might be in Gerrit only.

Phase 5 (meta sync) may still proceed for Gerrit-only flows.

Proceed to Phase 5 anyway? [Y/n]
```

**Otherwise:**
```
Saved to: pr_list.yaml

Proceed to Phase 5? [Y/n]
```

**If user enters 'n':** Exit orchestrator
**If user enters 'Y':** Continue to Phase 5

---

## PHASE 5: Meta Sync + Cherry-Pick Choice

**Agent:** `stable2-meta-sync-orchestrator`

**Purpose:** Execute stable2 synchronization in a guided flow:
- run GitHub script cherry-pick for READY PR commits first
- allow partial success and continue for successful repos
- add `track_for_stable2_considered` after cherry-pick completion
- then create GitHub stable2 release tags for successful repos
- then prepare PKGREV and SRCREV in meta-rdk-broadband stable2 workspace for successful repos
- then run Gerrit cherry-pick sync with a shared topic

### Step 5.1 — Ask to proceed

```
─────────────────────────────────────────────────────────────────
PHASE 5: META SYNC + CHERRY-PICK CHOICE
─────────────────────────────────────────────────────────────────

This phase will:
  ✓ Run GitHub script cherry-pick for READY PR commits
  ✓ Continue with successful repos and report manual action for conflicts
  ✓ Label READY tickets as considered after cherry-pick completion
  ✓ Prepare generic-pkgrev.inc and generic-srcrev.inc for successful repos
  ✓ Run separate Gerrit-only gerrit-cherrypick-squash with shared topic

Proceed with Phase 5? [Y/n]
```

**If user enters 'n':** Exit orchestrator
**If user enters 'Y':** Proceed to Step 5.2

### Step 5.2 — Invoke stable2-meta-sync-orchestrator

**Build command:**
```bash
/stable2-meta-sync-orchestrator {all_args}
```

Where:
- `{all_args}` = ALL arguments passed to orchestrator
- stable2-meta-sync-orchestrator forwards supported arguments to its internal phases

**Invoke agent** and wait for completion.

### Step 5.3 — Review results

```
─────────────────────────────────────────────────────────────────
PHASE 5 COMPLETE
─────────────────────────────────────────────────────────────────

Results from stable2-meta-sync-orchestrator:
  GitHub script cherry-pick: {S} repos succeeded, {F} repos need manual
  READY considered labels:   {A}
  Repos tagged:              {B} (successful repos)
  SRCREV updates prepared:   {C} (successful repos)
  Gerrit/meta sync:          {topic_or_preview}

Artifacts:
  - stable2_release_tags.yaml
  - stable2_srcrev_updates.yaml
```
---

## FINAL SUMMARY

Print complete orchestration report:

```
╔═════════════════════════════════════════════════════════════════╗
║                                                                 ║
║         STABLE2 RELEASE ORCHESTRATION COMPLETE                  ║
║                                                                 ║
╚═════════════════════════════════════════════════════════════════╝

Execution Summary:
  Mode:              {DRY-RUN / TEST-REPO / PRODUCTION}
  Execution Date:    {timestamp}

Phase Results:
  1. Track Develop    ✓ {K} Jira tickets labeled (or 0 if --dry-run/skipped)
  2. Filter           ✓ {N} candidates found
  3. Evaluate         ✓ {M} tickets RM Approved (READY)
  4. Collect PRs      ✓ {P} unique GitHub PRs
  5. Meta Sync        ✓ READY considered + tag + srcrev + cherry-pick choice + gerrit topic

Artifacts Generated:
  - stable2_candidates.yaml
  - stable2_status_analysis.yaml
  - stable2_ready_tickets.txt
  - pr_list.yaml
  - stable2_release_tags.yaml (from Phase 5)
  - stable2_srcrev_updates.yaml (from Phase 5)
  
Next Steps:
  1. Confirm optional script cherry-pick result for PR-derived repo scope

    2. Confirm Gerrit topic and meta-layer reviews from Phase 5

    3. Close the cycle with your ticket/state updates

─────────────────────────────────────────────────────────────────
Orchestration finished successfully.
─────────────────────────────────────────────────────────────────
```

Save execution log to: `stable2_orchestration_log_{timestamp}.yaml`

---

## Error Handling

### Skill invocation fails

If any skill fails:
```
ERROR: Skill {skill-name} failed with error:
{error message}

Orchestration halted at Phase {N}.

Resume options:
  1. Fix the issue and re-run entire orchestrator
  2. Run remaining skills manually starting from Phase {N+1}
  
Artifacts generated so far:
  {list of files}
```

Exit orchestrator with error code.

### User cancels at confirmation gate

```
Orchestration cancelled by user at Phase {N}.

Artifacts generated:
  {list of files}

To resume, re-run orchestrator and respond [Y] at each phase until Phase {N+1}.
```

### No tickets/PRs found

Handled at each phase (see Phase-specific sections above).

---

## Usage Examples

### Full production run:
```bash
/stable2-release-orchestrator
```

### Dry-run (preview only, scans production rdkcentral repos):
```bash
/stable2-release-orchestrator --dry-run
```

### Test with forked repos (production mode):
```bash
/stable2-release-orchestrator --test-repo
```

### Combined dry-run + test (safest for testing):
```bash
/stable2-release-orchestrator --dry-run --test-repo
```

### Full flow including meta sync:
```bash
/stable2-release-orchestrator --test-repo
```

**IMPORTANT: Argument Forwarding Rules**
- Orchestrator forwards EXACTLY what you provide - no additions, no modifications
- Each skill receives the same arguments you passed to orchestrator
- Skills use only the arguments they support and ignore the rest
- No skill will ever add arguments on its own

**Example Flow:**

```
USER RUNS:
  @stable2-release-orchestrator --dry-run

EACH SKILL RECEIVES:
  Phase 1: /track-for-stable2 --dry-run
  Phase 2: /stable2-candidates --dry-run
  Phase 3: /stable2-status-evaluator --file ... --dry-run
  Phase 4: /jira-pr-lookup <ticket> --dry-run
  Phase 5: /stable2-meta-sync-orchestrator --dry-run

Note: NO additional arguments because user didn't provide any
```

---

## Performance Notes

Typical execution times (production mode):
- Phase 1: 5-10 minutes (depends on date range)
- Phase 2: 5-10 seconds
- Phase 3: 10-20 seconds (50 tickets)
- Phase 4: 2-5 minutes (depends on ticket count)
- Phase 5: 5-15 minutes (depends on repo count and Gerrit operations)

**Total:** 12-30 minutes for this orchestrator (Phase 1-5)

Dry-run mode: ~8-15 minutes (faster, no write operations)

---

## Verify Completion

Before declaring done, confirm:
- [ ] All 5 orchestrated phases completed (or skipped with user consent)
- [ ] All artifacts generated and saved
- [ ] Final summary printed
- [ ] Execution log saved
- [ ] Next steps provided to user
