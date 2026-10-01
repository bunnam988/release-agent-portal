---
name: stable2-pr-labeler
description: "Use when adding a GitHub label (default 'cherry-pick to support/stable2') to GitHub PRs collected from jira-pr-lookup. This triggers GitHub automation to create cherry-pick PRs to support/stable2 branch. Don't use for general PR operations or manual cherry-picking."
license: Comcast
argument-hint: "Required: '--file pr_list.yaml' from jira-pr-lookup. Optional: '--label <name>' to use a different GitHub label instead of the default 'cherry-pick to support/stable2', '--dry-run' for preview, '--test-repo' for testing with 6 forked repos"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /stable2-pr-labeler

> **⚠️ THERE IS NO DEDICATED SCRIPT FOR THIS SKILL** — do not look for or try
> to run `stable2_pr_labeler.py`/`.sh` or anything similar; it does not exist.
> This skill uses only the `gh` CLI/GitHub MCP tools directly — nothing else.

## Purpose
Add a GitHub label (resolved as `{label}`, default `cherry-pick to support/stable2`, override with `--label <name>`) to GitHub PRs collected by jira-pr-lookup. This label triggers GitHub automation that:
1. Creates cherry-pick commits on support/stable2 branch
2. Opens pull requests against support/stable2
3. Runs CI tests

## Resolve Label
- If the user passed `--label <name>`, use that exact string as `{label}`.
- Otherwise, default `{label}` to `cherry-pick to support/stable2`.
- Print the resolved label before doing anything else: `Using GitHub label: {label}`

## Critical Constraints — Read First
- Use GitHub CLI (`gh`) or GitHub MCP tools for all GitHub operations
- If `gh` CLI is not authenticated, STOP immediately and tell user:
  "GitHub authentication failed. Please run: gh auth login -h github.com -w -s repo
  then re-run /stable2-pr-labeler"
- This skill does NOT perform cherry-picks — it only adds labels to trigger automation
- Label name is exact as resolved in `{label}` above (spaces allowed, not restricted to underscores)
- Jira ticket keys in input artifacts use generic format (not RDK-only):
  `[A-Z0-9]+-\d+` (examples: `XB10-2860`, `CBR2-1234`, `RDKB-64184`).
- No dedicated script exists for this skill. It uses only the `gh` CLI/GitHub MCP
  tools named above — do not assume a separate script exists anywhere.

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
║  Real rdkcentral PRs will NOT be touched.                         ║
╚════════════════════════════════════════════════════════════════════╝
```

Only add labels to PRs from these 6 repos. Skip all rdkcentral/* PRs.

## Dry-Run Mode
If the user passed `--dry-run`:
- Execute Steps 1-3 fully (read file, parse PRs, validate)
- **Skip Step 4** (no label additions)
- Print detailed report of what WOULD be done:
  - List all PRs that would be labeled
  - Show which PRs would be skipped (closed, already labeled, etc.)
  - Estimate GitHub automation impact
- Make clear: "DRY-RUN MODE: No labels added. This is a preview only."

## Before Starting
1. Read `/workspace/config/config.yaml` — get `github_org` (default: rdkcentral)
2. Verify GitHub authentication:
   ```bash
   gh auth status
   ```
   If not authenticated: Stop with error message (see Critical Constraints)

## Input File Format

Expected from jira-pr-lookup output (saved manually or by skill):

**File: `pr_list.yaml`**
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
    state: "merged"
    branch: "develop"
    merge_commit_sha: "abc1234def5678abc1234def5678abc1234def"
  
  - ticket: "RDKB-64185"
    component: "ccsp-wifi"
    url: "https://github.com/rdkcentral/ccsp-wifi/pull/123"
    number: 123
    repo: "rdkcentral/ccsp-wifi"
    title: "Fix 6GHz channel scan"
    author: "jane.smith"
    state: "merged"
    branch: "develop"
    merge_commit_sha: "fedcba9876543210fedcba9876543210fedcba98"
```

**Alternative: Simple text file (one PR URL per line):**
```
https://github.com/rdkcentral/utopia/pull/94
https://github.com/rdkcentral/ccsp-wifi/pull/123
https://github.com/rdkcentral/test-and-diagnostic/pull/56
```

## Workflow

Copy this checklist and track progress:

- [ ] Step 1: Parse arguments and load PR list
- [ ] Step 2: Validate and filter PRs
- [ ] Step 3: Print summary and get confirmation
- [ ] Step 4: Add labels (skip if --dry-run)
- [ ] Step 5: Report results and track automation

---

### Step 1 — Parse Arguments and Load PR List

**Parse flags:**
- `--file <path>`: Path to PR list file (YAML or text)
- `--label <name>`: GitHub label to use (see Resolve Label above)
- `--dry-run`: Preview mode (no label additions)
- `--test-repo`: Only label PRs in 6 forked repos

**Load PR list:**

If YAML file:
```python
import yaml
with open(pr_file) as f:
    data = yaml.safe_load(f)
    prs = data['prs']
```

If text file (one URL per line):
```python
with open(pr_file) as f:
    urls = [line.strip() for line in f if line.strip()]
    prs = [parse_pr_url(url) for url in urls]
```

**Parse GitHub URL to extract:**
```python
# Example: https://github.com/rdkcentral/utopia/pull/94
# Extract: org=rdkcentral, repo=utopia, number=94
import re
match = re.match(r'https://github\.com/([^/]+)/([^/]+)/pull/(\d+)', url)
org, repo, number = match.groups()
```

---

### Step 2 — Validate and Filter PRs

**For each PR:**

1. **Check if PR exists and is merged:**
   ```bash
   gh pr view <number> --repo <org>/<repo> --json state,baseRefName,labels
   ```
   
   Or use GitHub MCP to fetch PR details.

2. **Validate:**
   - State must be `MERGED` (not open, not closed without merge)
   - Base branch must be `develop` (where PR was merged to)
   - If `--test-repo`: org/repo must be in allowlist (Suganya-Sugumar/*)

3. **Check if already labeled:**
   - If PR already has the `{label}` label: skip (log as "already labeled")

4. **Categorize PRs:**
   - ✅ **Ready to label:** Merged, to develop, not already labeled
   - ⏭️ **Skip - already labeled:** Has the label
   - ⚠️ **Skip - not merged:** PR is open or closed without merge
   - ⚠️ **Skip - wrong branch:** Merged to non-develop branch
   - ⚠️ **Skip - wrong org:** Not in test allowlist (if --test-repo)
   - ❌ **Error:** PR not found or API error

---

### Step 3 — Print Summary and Get Confirmation

**Print categorized summary:**
```
─────────────────────────────────────────────────────────────────────
PR Labeling Plan
Date:          2026-08-04
Source:        pr_list.yaml (from jira-pr-lookup)
Mode:          {DRY-RUN / TEST-REPO / PRODUCTION}
─────────────────────────────────────────────────────────────────────

✅ Ready to Label (12 PRs):
  #94   rdkcentral/utopia              | Add WiFi 6E support
  #123  rdkcentral/ccsp-wifi           | Fix 6GHz channel scan
  #56   rdkcentral/test-and-diagnostic | Update diagnostic tools
  ...

⏭️ Already Labeled (2 PRs):
  #88   rdkcentral/ccsp-common         | Fix memory leak

⚠️ Skipped (3 PRs):
  #99   rdkcentral/utopia              | Not merged (state: open)
  #45   rdkcentral/ccsp-wifi           | Wrong branch (main)

─────────────────────────────────────────────────────────────────────
Summary:
  Ready to label:   12 PRs
  Already labeled:  2 PRs
  Skipped:          3 PRs
  Total:            17 PRs
─────────────────────────────────────────────────────────────────────
```

**If dry-run mode:**
```
╔════════════════════════════════════════════════════════════════════╗
║  DRY-RUN MODE - PREVIEW ONLY                                      ║
║  No labels will be added.                                         ║
╚════════════════════════════════════════════════════════════════════╝

Expected GitHub Automation Workflow:
  For each labeled PR, GitHub will:
  1. Create cherry-pick commit on support/stable2 branch
  2. Open new PR against support/stable2
  3. Run CI tests
  4. Notify reviewers

Estimated: 12 new PRs will be created in support/stable2
```

Exit skill here if `--dry-run`.

**If production mode, ask for confirmation:**
```
Add '{label}' label to 12 PRs? [Y/n]
```

If user enters anything other than Y or y: Stop and exit.

---

### Step 4 — Add Labels (skip if --dry-run)

**For each PR in "Ready to label" list:**

Using gh CLI:
```bash
gh pr edit <number> --repo <org>/<repo> --add-label "{label}"
```

Or using GitHub MCP:
```
Add label "{label}" to PR <org>/<repo>#<number>
```

**Handle errors:**
- 404 (PR not found): Log and skip
- 403 (Permission denied): Stop with error message:
  ```
  Permission denied. Ensure 'gh' is authenticated with repo write permissions.
  Run: gh auth refresh -h github.com -s repo
  ```
- 422 (Label doesn't exist in repo): Log warning, continue
  ```
  Warning: Label '{label}' does not exist in <org>/<repo>.
  Create the label first or skip this PR.
  ```
- Other errors: Log and continue with next PR

**Track results:**
```python
labeled_successfully = []
label_errors = []

for pr in ready_to_label:
    try:
        add_label(pr)
        labeled_successfully.append(pr)
    except Exception as e:
        label_errors.append((pr, str(e)))
```

---

### Step 5 — Report Results and Track Automation

**Print final report:**
```
─────────────────────────────────────────────────────────────────────
Label Addition Complete
Date:          2026-08-04
Mode:          {PRODUCTION / TEST-REPO}
─────────────────────────────────────────────────────────────────────

✅ Successfully Labeled (12 PRs):
  #94   rdkcentral/utopia              | https://github.com/rdkcentral/utopia/pull/94
  #123  rdkcentral/ccsp-wifi           | https://github.com/rdkcentral/ccsp-wifi/pull/123
  ...

❌ Labeling Failed (0 PRs):
  (none)

─────────────────────────────────────────────────────────────────────
Summary:
  Successfully labeled: 12 PRs
  Failed:               0 PRs
  Already labeled:      2 PRs
  Skipped:              3 PRs
  Total processed:      17 PRs
─────────────────────────────────────────────────────────────────────

GitHub Automation Status:
  GitHub workflows should now trigger for the 12 labeled PRs.
  
  Track automation progress:
  - Check Actions tab in each repository
  - New PRs to support/stable2 will appear within 5-10 minutes
  - CI tests will run automatically
  
  Monitor at:
  - https://github.com/rdkcentral/utopia/pulls?q=is:pr+base:support/stable2
  - https://github.com/rdkcentral/ccsp-wifi/pulls?q=is:pr+base:support/stable2
─────────────────────────────────────────────────────────────────────

Next Steps:
1. Wait for GitHub automation to create support/stable2 PRs
2. Review and approve the generated PRs
3. Once merged, run /gerrit-cherrypick-squash for Gerrit repos
4. Create sync Jira ticket to track all changes
─────────────────────────────────────────────────────────────────────
```

**Save results to file:**

Write `stable2_pr_labeling_results.yaml`:
```yaml
execution_date: "2026-08-04T10:30:00Z"
mode: "production"
input_file: "pr_list.yaml"
summary:
  ready_to_label: 12
  successfully_labeled: 12
  already_labeled: 2
  skipped: 3
  failed: 0
  total: 17

components:
  - repo: "rdkcentral/utopia"
    component: "utopia"
    tickets: ["RDKB-64184"]
    prs: [94]
    merge_commits:
      - "abc1234def5678abc1234def5678abc1234def"

labeled_prs:
  - ticket: "RDKB-64184"
    component: "utopia"
    repo: "rdkcentral/utopia"
    number: 94
    url: "https://github.com/rdkcentral/utopia/pull/94"
    title: "Add WiFi 6E support"
    branch: "develop"
    merge_commit_sha: "abc1234def5678abc1234def5678abc1234def"
    label_added: true
    timestamp: "2026-08-04T10:30:15Z"
  
  - ticket: "RDKB-64185"
    component: "ccsp-wifi"
    repo: "rdkcentral/ccsp-wifi"
    number: 123
    url: "https://github.com/rdkcentral/ccsp-wifi/pull/123"
    title: "Fix 6GHz channel scan"
    branch: "develop"
    merge_commit_sha: "fedcba9876543210fedcba9876543210fedcba98"
    label_added: true
    timestamp: "2026-08-04T10:30:16Z"

errors: []
```

Requirements for saved Phase 5 artifact:
- It MUST list component/repo, Jira ticket, PR number, PR URL, PR title, base branch, and merge commit SHA for every PR entry
- It MUST include a `components` summary grouped by repo/component
- This file is used as a downstream reference artifact, so do NOT save only counts

Ask user:
```
Save results to stable2_pr_labeling_results.yaml? [Y/n]
```

**If user confirms save, print the complete file contents:**

```
═════════════════════════════════════════════════════════════════
SAVED FILE: stable2_pr_labeling_results.yaml
═════════════════════════════════════════════════════════════════
{print entire contents of stable2_pr_labeling_results.yaml}
═════════════════════════════════════════════════════════════════

Summary:
  ✅ Successfully labeled: {N} PRs
  ℹ️  Already labeled: {M} PRs
  ⚠️  Skipped: {K} PRs
  ❌ Failed: {F} PRs

Component Summary:
  {repo/component} | {ticket_count} Jira(s) | {pr_count} PR(s) | {merge_commit_count} commit(s)

GitHub Actions triggered for {N} repositories.
Check automation PRs at:
  https://github.com/rdkcentral/*/pulls?q=base:support/stable2+label:"{label, URL-encoded}"
═════════════════════════════════════════════════════════════════
```

---

## Edge Cases

### No PRs ready to label
```
No PRs are ready for labeling.
All PRs are either already labeled, not merged, or on wrong branch.
Review the PR list and try again.
```

### Label doesn't exist in repository
GitHub automation requires the label to exist in each repository.

If label missing:
```
Warning: Label '{label}' not found in rdkcentral/utopia.

Create the label:
  gh label create "{label}" \
    --repo rdkcentral/utopia \
    --description "Cherry-pick this PR to support/stable2 branch" \
    --color "0E8A16"

Or create via GitHub UI:
  https://github.com/rdkcentral/utopia/labels
```

### GitHub automation not configured
If repositories don't have automation workflows:
```
Warning: Some repositories may not have GitHub Actions configured for automatic cherry-picking.
Labeled PRs in those repos will require manual cherry-picks.

Check .github/workflows/ in each repository for cherry-pick automation.
```

### Rate limiting
```
GitHub API rate limit exceeded.
Wait 60 minutes and retry, or authenticate with a token that has higher limits.

Current limit: gh api rate_limit
```

## Performance Notes
- Typical execution: 12 PRs in 10-15 seconds
- Large batches (50+ PRs): 30-60 seconds
- Each label addition is a separate API call
- Can parallelize, but GitHub has rate limits

## Verify Completion
Before declaring done, confirm:
- [ ] All eligible PRs have label added
- [ ] Results saved to YAML file (if user confirmed)
- [ ] Next steps printed
- [ ] No API errors or permission issues
