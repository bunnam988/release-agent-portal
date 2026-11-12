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
- If `--test-repo` is present, keep scope limited to the approved fork repos found in the upstream artifacts (including `Suganya-Sugumar/*` allowlist entries and `bunnam988/provisioning-and-management`) — GDCS repos are never included in `--test-repo` mode
- If user passed `--test-repo <approved_fork_url>`, keep only that single allowlisted repo in scope
- Eligible repos split into two groups by GitHub org, each with its own meta-layer workspace, file, ref, and (for GDCS) GitHub identity — see **GDCS (rdk-gdcs) Repos** below. Everything above this applies to BOTH groups unless that section overrides it.

## GDCS (rdk-gdcs) Repos

Most GDCS stable2 components (`config/tracked_repos_gdcs_stable2.yaml`) are
SRCREV-only — this skill is the ONLY stable2-meta-sync phase that touches
them for most of the 6 (the 2 `tag_eligible: true` ones, today cujo-ni and
cujo-fpingdq sharing one repo, also go through `stable2-github-release-tagger`
first, same as core-nw).

- **Meta-layer**: use `{mesh_gerrit_repo}` / `{mesh_srcrev_file}` from
  `config/config.yaml` (today: `rdk/yocto_oe/layers/meta-rdk-comcast-broadband`,
  `conf/include/generic-srcrev.inc`) — a SEPARATE Gerrit project from
  `{gerrit_repo}`/`{srcrev_file}` used for core-nw.
- **Workspace**: `{meta_sync_workspace}/meta-rdk-comcast-broadband` —
  parallel to, never reusing, `{meta_sync_workspace}/meta-rdk-broadband`.
- **Meta branch**: ask the user separately (default from `config/config.yaml`'s
  `mesh_meta_support_branch`) — do NOT assume it matches `{meta_support_branch}`.
- **GitHub identity**: `rdk-gdcs` requires `$GH_MESH_USER`, not the default
  identity. Before running `resolve_srcrev.py` against any GDCS repo (it
  shells out to `git ls-remote` over HTTPS, which uses `gh`'s credential
  helper for the currently active account), switch identity exactly like
  `on-demand-cherry-pick.md`'s "GitHub Identity — Per-Repo Org Switch":
  record `{default_gh_user}` once, switch to `$GH_MESH_USER` (STOP if
  unset) and verify with `gh auth status` before the GDCS batch, then
  switch back to `{default_gh_user}` immediately after that batch
  completes (core-nw repos must never run under the Mesh identity).
- **SSO gotcha**: a missing-ref/404 result for a GDCS repo even with
  `$GH_MESH_USER` active means the token needs SSO authorization for
  `comcast-global`, not that the branch/repo is missing — tell the user to
  authorize it rather than concluding the ref doesn't exist.
- **Run `resolve_srcrev.py` as a separate invocation** from the core-nw
  batch (different `--repo` scope, potentially different `--ref`) — do not
  mix the two org's repos into one command.
- **GDCS SRCREV-only repos never appear in `stable2_release_tags.yaml`**
  (that file only lists repos `stable2-github-release-tagger` actually
  tagged, which excludes every GDCS repo that isn't `tag_eligible: true`) —
  see Step 1's explicit "always add" rule below. Do not rely on
  `stable2_release_tags.yaml` alone to build GDCS scope, or 4 of the 6
  GDCS components (today: everything except cujo-ni/cujo-fpingdq) will be
  silently skipped.

## Inputs
Read in this priority order:
1. `stable2_release_tags.yaml` for final eligible core-nw repo scope (plus
   any GDCS `tag_eligible: true` repos it also contains)
2. If absent, derive core-nw scope from:
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

Build the **core-nw** repo scope from `stable2_release_tags.yaml` if present
(filter to non-GDCS repos — see the split rule below).
Each repo entry must include:
- `repo`
- `component`
- READY Jira tickets
- PRs

If `stable2_release_tags.yaml` is absent, rebuild core-nw scope from READY
tickets and `pr_list.yaml`.

Build the **GDCS** repo scope SEPARATELY and ALWAYS this way (never solely
from `stable2_release_tags.yaml` — see "GDCS SRCREV-only repos never
appear..." above): take every repo in `pr_list.yaml` mapped to a READY
ticket whose owner matches `github_org` in
`config/tracked_repos_gdcs_stable2.yaml` (today `rdk-gdcs`). This picks up
all 6 GDCS components, not just the `tag_eligible: true` one(s) that
`stable2-release-tags.yaml` happens to also list.

If both scopes are empty, stop.

**Confirm the org split**: any repo whose owner matches `github_org` in
`config/tracked_repos_gdcs_stable2.yaml` (today: `rdk-gdcs`) is GDCS scope;
everything else is core-nw scope. Print both counts:
`{N} core-nw repos, {M} GDCS repos`.

If `--test-repo` is present, print the final repo scope before proceeding
(core-nw only — GDCS repos are excluded entirely in this mode).
If a specific allowlisted URL was passed, the final repo scope must contain only that repo.

## Step 2 — Ask User Which Ref to Resolve

Ask once for core-nw scope (if non-empty), with exactly these options:

```text
Choose ref to resolve for eligible core-nw (rdkcentral) repos:
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

**If GDCS scope is non-empty**, ask a SEPARATE prompt — do not assume it's
the same ref as core-nw (GDCS components may be on a different branch):

```text
Choose ref to resolve for eligible GDCS (rdk-gdcs) repos:
1. Same as core-nw: {selected_ref}
2. Custom branch
3. Own answer
```

Store result as `{mesh_selected_ref}`.

## Step 3 — Resolve SHAs Using git ls-remote

Run the helper script with ONLY the eligible repos, core-nw and GDCS as
separate invocations (GDCS needs the identity switch from "GDCS (rdk-gdcs)
Repos" above active around its call):

```bash
python3 .agents/skills/stable2-srcrev-updater/scripts/resolve_srcrev.py \
  --ref {selected_ref} \
  --repo {owner_repo_1} \
  --repo {owner_repo_2}
```

```bash
# GDCS batch — run only after switching to $GH_MESH_USER
python3 .agents/skills/stable2-srcrev-updater/scripts/resolve_srcrev.py \
  --ref {mesh_selected_ref} \
  --repo {gdcs_owner_repo_1} \
  --repo {gdcs_owner_repo_2}
# then switch back to {default_gh_user} immediately
```

The script must:
- match repo scope against its built-in repo mapping (now includes both
  the core-nw `DEFAULT_REPOS` and GDCS `GDCS_REPOS` dicts — see the
  script's own source)
- use `git ls-remote` for exact ref resolution
- output resolved SHA lines and a structured summary

Before any file edits, print a SHA Resolution Report containing for every repo:
- repo/component
- READY Jira tickets
- PR numbers
- selected ref
- resolved SHA
- github org (rdkcentral / rdk-gdcs)

Then ask:
`Apply the generic-srcrev.inc updates above? [Y/n]`

If a repo does not contain the selected ref:
- print the repo and missing ref clearly
- ask user:
  - `Choose another ref for this repo? [Y/n]`
- if user says yes, prompt for replacement ref and retry only that repo
- if user says no, stop the skill

## Step 4 — Update generic-srcrev.inc Locally

**Core-nw batch** — use prepared workspace:
- `{meta_sync_workspace}/meta-rdk-broadband`

This workspace is expected to already be on `{meta_support_branch}`.

In `{srcrev_file}`:
1. Update only the SHA lines for selected components
2. Preserve all unrelated components unchanged
3. If a matching `SRCREV_scope_branch_pn-<component>` line exists, update it to `{selected_ref}`
4. Do not alter formatting outside the targeted lines
5. Leave changes uncommitted

**GDCS batch** (only if GDCS scope is non-empty) — use a SEPARATE prepared
workspace:
- `{meta_sync_workspace}/meta-rdk-comcast-broadband`

If this workspace does not exist yet:
```bash
git clone --branch {mesh_meta_branch} https://{gerrit_host}/{mesh_gerrit_repo} {meta_sync_workspace}/meta-rdk-comcast-broadband
```
Otherwise `fetch`/`checkout`/`pull --ff-only` the same way core-nw's workspace
is refreshed. `{mesh_meta_branch}` is asked for once (default
`mesh_meta_support_branch` from config.yaml), shared with
`stable2-github-release-tagger` if that phase already resolved it this run.

In `{mesh_srcrev_file}` (from config.yaml, today same relative path
`conf/include/generic-srcrev.inc` but inside the GDCS workspace above):
1. Update only the SHA lines for the GDCS components resolved in Step 3
2. Preserve all unrelated components unchanged
3. Update `SRCREV_scope_branch_*` to `{mesh_selected_ref}` where present
4. Leave changes uncommitted — this is a separate Gerrit change from the
   core-nw one (see `gerrit-cherrypick-squash` Step 5h)

If `--dry-run` is present:
- Do NOT edit either `generic-srcrev.inc`
- Print the exact variable/value updates and any scope-branch updates that
  would be applied as a fenced ```diff block (one `-`/`+` line pair per
  changed `SRCREV_pn-<component>` / `SRCREV_scope_branch_pn-<component>`
  line — see AGENTS.md), not prose — one block per meta-layer

## Step 5 — Save Results

Write `stable2_srcrev_updates.yaml`. Each update entry carries a `meta` field
(`"core-nw"` or `"gdcs"`) so `gerrit-cherrypick-squash` Step 5h knows which
meta-layer workspace/change it belongs to:

```yaml
execution_date: "2026-08-05T10:45:00Z"
selected_ref: "support/stable2"
mesh_selected_ref: "support/stable2"
support_branch: "support/stable2"
meta_support_branch: "stable2"
mesh_meta_support_branch: "stable2"
meta_workspace: ".stable2-meta-sync/meta-rdk-broadband"
mesh_meta_workspace: ".stable2-meta-sync/meta-rdk-comcast-broadband"
srcrev_file: "conf/include/generic-srcrev.inc"
mesh_srcrev_file: "conf/include/generic-srcrev.inc"
repos_resolved: 3
updates:
  - repo: "rdkcentral/advanced-security"
    component: "advanced-security"
    srcrev_var: "SRCREV_ccsp_adv_security"
    sha: "3224178baa2d1cf3260ca5685eaca17a45a27e93"
    scope_branch_updated: true
    tickets: ["RDKB-66162"]
    prs: [98]
    meta: "core-nw"
  - repo: "rdkcentral/utopia"
    component: "utopia"
    srcrev_var: "SRCREV_utopia"
    sha: "abc1234def5678abc1234def5678abc1234def"
    scope_branch_updated: true
    tickets: ["XB10-2860", "RDKB-65709"]
    prs: [101]
    meta: "core-nw"
  - repo: "rdk-gdcs/apparmor-profiles"
    component: "apparmor_profiles"
    srcrev_var: "SRCREV_apparmor_profiles"
    sha: "fedcba9876543210fedcba9876543210fedcba98"
    scope_branch_updated: true
    tickets: ["SHARMAN-4337"]
    prs: [325]
    meta: "gdcs"
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
- generic-pkgrev.inc (core-nw, if any tag-eligible repos were in scope)
- generic-srcrev.inc (core-nw)

Prepared local meta-rdk-comcast-broadband updates (if GDCS scope was non-empty):
- generic-pkgrev.inc (GDCS tag-eligible repos only, today cujo-ni/cujo-fpingdq)
- generic-srcrev.inc (all GDCS repos in scope)

Next step:
Run gerrit-cherrypick-squash using the same topic so the prepared meta-layer
changes are included as one extra Gerrit change per meta-layer (core-nw and,
if present, GDCS — see that skill's Step 5h).
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
- [ ] If GDCS scope was non-empty: ref was asked separately, GitHub identity
      was switched to `$GH_MESH_USER` for its `resolve_srcrev.py` batch and
      restored afterward, and its updates went to the separate
      `meta-rdk-comcast-broadband` workspace, never `meta-rdk-broadband`
