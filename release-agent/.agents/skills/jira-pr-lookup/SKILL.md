---
name: jira-pr-lookup
description: Use when finding all merged PRs to the develop branch for a Jira user story, including its subtasks and functional dependency tickets. Don't use for general Jira queries, PR reviews, or branch lookups unrelated to Jira ticket traversal.
argument-hint: "Required: ticket key (generic format: '[A-Z0-9]+-\\d+', e.g., XB10-2860, CBR2-1234, RDKB-64184). Optional: '--dry-run' for preview, '--test-repo' for testing with 6 forked repos"
metadata:
  author: sgujul412
---

## Quick start

Given a root Jira ticket key (generic format `[A-Z0-9]+-\d+`, e.g. `XB10-2860`, `CBR2-1234`, `RDKB-64184`), traverse its subtasks and functional dependencies via BFS, then collect all PRs with `status == MERGED` and `destination.branch == develop`.

Credentials are read from `ccp_jira.env` in the working directory. Never hardcode credentials.

## Test-Repo Mode
If the user passed `--test-repo`:

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

When scanning for PRs, only check PRs from these 6 repos.

## Dry-Run Mode
If the user passed `--dry-run`:
- Execute all Jira fetches and PR collection normally
- Print complete report with all PRs found
- Make clear: "DRY-RUN MODE: No actual operations performed. This is a preview only."

## Workflow

Copy this checklist and check off items as you complete them:

Task Progress:

- [ ] Step 1: Parse arguments (--dry-run, --test-repo)
- [ ] Step 2: Verify `ccp_jira.env` exists and contains required keys
- [ ] Step 3: Run `fetch_prs.py <TICKET-KEY>` to traverse the graph and collect PRs
- [ ] Step 4: Review the PR table output
- [ ] Step 5: Report results to the user

### Step 1 — Parse arguments

Check for optional flags:
- `--dry-run`: Preview mode (no changes, just report)
- `--test-repo`: Use 6 forked repos only (see allowlist above)

If `--test-repo`: Print banner and restrict PR collection to allowlisted repos only.

### Step 2 — Verify credentials

`ccp_jira.env` must contain:

```text
JIRA_BASE_URL=https://ccp.sys.comcast.net
JIRA_API_VERSION=2
JIRA_USER=svc-autotriage
JIRA_TOKEN=<token>
```

If the file is missing or any key is absent, ask the user to supply it. Do not proceed without credentials.

### Step 3 — Run the script

```bash
python3 .agents/skills/jira-pr-lookup/scripts/fetch_prs.py RDKB-64184
```

Output is a table of merged PRs: ticket, URL, title, author.

If `--test-repo`: only include PRs from the 6 allowlisted repos.

### Step 4 — Interpret results

- Each row is a unique PR (deduplicated by URL).
- The `ticket` column shows which Jira ticket the PR was linked to and how it was reached (root, subtask, dependency link type and direction).
- Every reached ticket is checked for its own PRs, whether it's a bug or a user story, and whether or not it has subtasks of its own -- a dependency ticket's own PRs and its subtasks' PRs are both collected.

### Step 5 — Report results

Print summary:
```
─────────────────────────────────────────────
Jira PR Lookup Results
Root ticket:    {RDKB-64184}
Tickets found:  {N} (including subtasks and dependencies)
PRs found:      {M} merged to develop
Mode:           {DRY-RUN / TEST-REPO / PRODUCTION}
─────────────────────────────────────────────
```

If `--dry-run`:
```
╔════════════════════════════════════════════════════════════════════╗
║  DRY-RUN MODE                                                     ║
║  No actual operations performed. This is a preview only.          ║
╚════════════════════════════════════════════════════════════════════╝
```

If `--test-repo`:
```
Note: Results limited to 6 approved test repositories only.
```

**Ask user to save results:**
```
Save PR list to pr_list.yaml? [Y/n]
(This file can be used by /stable2-pr-labeler to add GitHub labels)
```

If user confirms, save to `pr_list.yaml`:
```yaml
query_date: "2026-08-04"
root_ticket: "RDKB-64184"
tickets_processed: 8
prs_found: 12
prs:
  - ticket: "RDKB-64184"
    component: "utopia"
    url: "https://github.com/rdkcentral/utopia/pull/94"
    number: 94
    repo: "rdkcentral/utopia"
    title: "Add WiFi 6E support"
    author: "john.doe"
    branch: "develop"
    merge_commit_sha: "abc1234def5678abc1234def5678abc1234def"
  - ticket: "RDKB-64185"
    component: "ccsp-wifi"
    url: "https://github.com/rdkcentral/ccsp-wifi/pull/123"
    number: 123
    repo: "rdkcentral/ccsp-wifi"
    title: "Fix 6GHz channel scan"
    author: "jane.smith"
    branch: "develop"
    merge_commit_sha: "fedcba9876543210fedcba9876543210fedcba98"
```

Before saving `pr_list.yaml`, enrich each PR with GitHub metadata. This is the
only step in this skill that calls GitHub directly (everything else is pure
Jira REST via `scripts/fetch_prs.py`), and the discovered PRs can belong to
either the `rdkcentral` org or the `rdk-gdcs` (GDCS) org — switch identity
per repo before each call, same rule `on-demand-cherry-pick.md`'s "GitHub
Identity — Per-Repo Org Switch" section and `track-for-stable2`'s own copy
of it already use:
- Mesh org name = `github_org` from `config/tracked_repos_gdcs_stable2.yaml`
  (today: `rdk-gdcs`).
- Record `{default_gh_user}` once before the first enrichment call (`gh auth
  status --hostname github.com`, the account with `Active account: true`).
- Before each `gh pr view` call: if that PR's repo owner is the Mesh org,
  switch to `$GH_MESH_USER` (STOP if unset) and verify with `gh auth
  status`; otherwise ensure `{default_gh_user}` is active.
- SSO gotcha: a 404 on an `rdk-gdcs` repo even with `GH_MESH_USER` active
  means the token needs SSO authorization for `comcast-global`, not that
  the repo/PR is missing.
- Restore `{default_gh_user}` before this skill finishes, regardless of
  outcome.

```bash
gh pr view <number> --repo <org>/<repo> \
  --json mergeCommit,baseRefName \
  --jq '{merge_commit_sha: .mergeCommit.oid, branch: .baseRefName}'
```

Rules:
- `component` = repo basename (for `rdkcentral/utopia`, component = `utopia`)
- `branch` must be `develop`
- `merge_commit_sha` must be the full 40-character merge commit SHA when available
- If `mergeCommit` is null, leave `merge_commit_sha: null` and report it

**After saving, print the complete file contents:**
```
═════════════════════════════════════════════════════════════════
SAVED FILE: pr_list.yaml
═════════════════════════════════════════════════════════════════
{print entire contents of pr_list.yaml}
═════════════════════════════════════════════════════════════════

PR Summary by Repository:
{for each unique repo, show count of PRs}
  rdkcentral/utopia: 3 PRs
  rdkcentral/ccsp-wifi: 5 PRs
  rdkcentral/halif-wifi: 2 PRs
  Total: 10 PRs across 3 repositories
═════════════════════════════════════════════════════════════════
```

Print next steps:
```
Next Steps:
1. Review the PR list above
2. Run /stable2-pr-labeler --file pr_list.yaml to add GitHub labels
3. Wait for GitHub automation to create support/stable2 PRs
```

## Traversal rules

### Link types followed (functional deps only)

`Dependency`, `Block`, `Depends on`, `blocks`, `is blocked by`, `requires`, `is required by`

### Always excluded

- `CATR-*` project tickets
- Link types: `Cloner`, `Clonee`, `Testing`, `Release Container`, `Gantt`, `Gantt: start-finish`, `Key Requirements`

Every other ticket reached by the traversal (the root ticket, its subtasks,
and every linked dependency ticket, recursively) is checked for its own
PRs regardless of issue type (bug or user story) or whether it itself has
subtasks -- there is no type-based skip. This keeps GitHub PR discovery in
parity with `gerrit-cherrypick-squash`'s Gerrit-side traversal, which
likewise checks every non-excluded ticket it reaches.

### PR filter

- `status == MERGED`
- `destination.branch == develop`
- All other branches (`main`, `topic/*`, `support/*`) are excluded

## Algorithm summary

BFS, max depth 5, `ThreadPoolExecutor(max_workers=10)` for parallel Jira API calls.

APIs used:

```text
GET /rest/api/2/issue/{KEY}?fields=id,key,summary,subtasks,issuelinks
GET /rest/dev-status/1.0/issue/detail?applicationType=githube&dataType=pullrequest&issueId={ID}
```

See [scripts/fetch_prs.py](scripts/fetch_prs.py) for the full implementation.
