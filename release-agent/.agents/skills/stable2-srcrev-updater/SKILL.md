---
name: stable2-srcrev-updater
description: "Use after stable2 GitHub stable2 tagging to resolve GitHub SHAs for eligible repos only using git ls-remote, mirroring update_srcrev.py behavior, then update generic-srcrev.inc and scope-branch entries in the prepared meta-rdk-broadband workspace. Supports --dry-run and --test-repo with an explicit pre-change confirmation gate."
license: Comcast
argument-hint: "Optional: '--dry-run' for preview only, '--test-repo' to keep scope on approved forks, '--test-repo <approved_fork_url>' to narrow to one repo. The skill will ask which ref to use: 1) support_branch from config.yaml, 2) custom branch, 3) own answer"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /stable2-srcrev-updater

## Purpose
Resolve and apply GitHub SHAs for the eligible stable2 components only:
1. Read eligible repo scope from previous artifacts
2. Ask user which Git ref to resolve
3. Use `git ls-remote` exactly like `update_srcrev.py` to resolve SHAs without cloning the component repos
4. Update `generic-srcrev.inc` in the prepared `meta-rdk-broadband` workspace
5. Update `SRCREV_scope_branch_*` entries for the selected components to the chosen ref when present
6. Save results to `stable2_srcrev_updates.yaml`

## Critical Constraints — Read First
- Use the same SHA-resolution approach as `update_srcrev.py`: `git ls-remote`
- Do NOT silently fall back to `main` or `master`
- If the chosen ref is missing for a repo, tell the user and ask whether to provide another ref or stop
- Update only the components that are in the eligible stable2 repo scope
- Do NOT commit or push Gerrit changes in this skill
- If `--dry-run` is present, resolve SHAs and print the planned `generic-srcrev.inc` changes but do NOT edit files
- If `--test-repo` is present, keep scope limited to the approved fork repos found in the upstream artifacts (including `Suganya-Sugumar/*` allowlist entries and `bunnam988/provisioning-and-management`)
- If user passed `--test-repo <approved_fork_url>`, keep only that single allowlisted repo in scope

## Inputs
Read in this priority order:
1. `stable2_release_tags.yaml` for final eligible repo scope
2. If absent, derive scope from:
   - `stable2_status_analysis.yaml` READY tickets
   - `pr_list.yaml` READY-ticket PR mappings

Read config from `/workspace/config/config.yaml`:
- `srcrev_file`
- `meta_sync_workspace`
- `support_branch`
- `meta_support_branch`

Use helper script:
- `scripts/resolve_srcrev.py`

## Step 1 — Determine Repo Scope

Build the repo scope from `stable2_release_tags.yaml` if present.
Each repo entry must include:
- `repo`
- `component`
- READY Jira tickets
- PRs

If `stable2_release_tags.yaml` is absent, rebuild scope from READY tickets and `pr_list.yaml`.

If repo scope is empty, stop.

If `--test-repo` is present, print the final repo scope before proceeding.
If a specific allowlisted URL was passed, the final repo scope must contain only that repo.

## Step 2 — Ask User Which Ref to Resolve

Prompt the user with exactly these options:

```text
Choose ref to resolve for eligible GitHub repos:
1. Default: {support_branch} (from config.yaml)
2. Custom branch
3. Own answer
```

Behavior:
- If user chooses `1`, use `{support_branch}` from config.yaml
- If user chooses `2`, ask: `Enter custom branch name:`
- If user chooses `3`, ask: `Enter ref (branch or tag):`
- Empty input is not allowed

Store result as `{selected_ref}`.

## Step 3 — Resolve SHAs Using git ls-remote

Run the helper script with ONLY the eligible repos:

```bash
python3 .agents/skills/stable2-srcrev-updater/scripts/resolve_srcrev.py \
  --ref {selected_ref} \
  --repo {owner_repo_1} \
  --repo {owner_repo_2}
```

The script must:
- match repo scope against its built-in repo mapping
- use `git ls-remote` for exact ref resolution
- output resolved SHA lines and a structured summary

Before any file edits, print a SHA Resolution Report containing for every repo:
- repo/component
- READY Jira tickets
- PR numbers
- selected ref
- resolved SHA

Then ask:
`Apply the generic-srcrev.inc updates above? [Y/n]`

If a repo does not contain the selected ref:
- print the repo and missing ref clearly
- ask user:
  - `Choose another ref for this repo? [Y/n]`
- if user says yes, prompt for replacement ref and retry only that repo
- if user says no, stop the skill

## Step 4 — Update generic-srcrev.inc Locally

Use prepared workspace:
- `{meta_sync_workspace}/meta-rdk-broadband`

This workspace is expected to already be on `{meta_support_branch}`.

In `{srcrev_file}`:
1. Update only the SHA lines for selected components
2. Preserve all unrelated components unchanged
3. If a matching `SRCREV_scope_branch_pn-<component>` line exists, update it to `{selected_ref}`
4. Do not alter formatting outside the targeted lines
5. Leave changes uncommitted

If `--dry-run` is present:
- Do NOT edit `generic-srcrev.inc`
- Print the exact variable/value updates and any scope-branch updates that would be applied

## Step 5 — Save Results

Write `stable2_srcrev_updates.yaml`:

```yaml
execution_date: "2026-08-05T10:45:00Z"
selected_ref: "support/stable2"
support_branch: "support/stable2"
meta_support_branch: "stable2"
meta_workspace: ".stable2-meta-sync/meta-rdk-broadband"
srcrev_file: "conf/include/generic-srcrev.inc"
repos_resolved: 2
updates:
  - repo: "rdkcentral/advanced-security"
    component: "advanced-security"
    srcrev_var: "SRCREV_ccsp_adv_security"
    sha: "3224178baa2d1cf3260ca5685eaca17a45a27e93"
    scope_branch_updated: true
    tickets: ["RDKB-66162"]
    prs: [98]
  - repo: "rdkcentral/utopia"
    component: "utopia"
    srcrev_var: "SRCREV_utopia"
    sha: "abc1234def5678abc1234def5678abc1234def"
    scope_branch_updated: true
    tickets: ["XB10-2860", "RDKB-65709"]
    prs: [101]
warnings: []
errors: []
```

Include these extra fields:
- `mode: "production|test-repo|dry-run"`
- `srcrev_updates_previewed: true|false`

Print the complete saved file contents.

## Step 6 — Ready for Gerrit Meta Sync

Print:
```text
Prepared local meta-rdk-broadband updates:
- generic-pkgrev.inc
- generic-srcrev.inc

Next step:
Run gerrit-cherrypick-squash using the same topic so the prepared meta-layer
changes are included as one extra Gerrit change.
```

## Verify Completion
Before declaring done, confirm:
- [ ] Repo scope came from eligible stable2 repos only
- [ ] User explicitly selected the ref
- [ ] SHAs were resolved with `git ls-remote`
- [ ] Missing refs required user intervention (no silent fallback)
- [ ] `generic-srcrev.inc` was updated locally for selected components only
- [ ] `SRCREV_scope_branch_*` lines were updated when present
- [ ] `stable2_srcrev_updates.yaml` was saved and printed
