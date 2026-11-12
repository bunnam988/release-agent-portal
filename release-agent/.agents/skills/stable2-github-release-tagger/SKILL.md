---
name: stable2-github-release-tagger
description: "Use after stable2 Phase 5 to create support/stable2 GitHub releases for eligible components only. Determines eligible repos from READY Jira tickets, finds the latest X.Y.Z base tag reachable from support/stable2, creates <base_tag>_stable2_YYYYMMDD release tags with generated notes, and updates generic-pkgrev.inc in a prepared meta-rdk-broadband stable2 workspace. Supports --dry-run and --test-repo with an explicit pre-change confirmation gate."
license: Comcast
argument-hint: "Optional: '--dry-run' for preview only, '--test-repo' to operate on approved fork repos only, '--test-repo <approved_fork_url>' to narrow to one repo, '--date YYYYMMDD' to override today's date in the stable2 tag name, '--support-branch <name>', '--meta-support-branch <name>', '--gerrit-host <host>', '--gerrit-repo <path>' to override the matching config.yaml values for this run only, '--mesh-support-branch <name>' and '--mesh-meta-support-branch <name>' for the GDCS (rdk-gdcs) tag-eligible repo's equivalents (skips that section's interactive ask if provided)"
metadata:
  author: Suganya Sugumar
  source: local
---

# Skill: /stable2-github-release-tagger

> **⚠️ THERE IS NO DEDICATED SCRIPT FOR THIS SKILL** — do not look for or try
> to run `stable2_github_release_tagger.py`/`.sh` or anything similar; it does
> not exist. This skill uses only the `gh` CLI/GitHub MCP tools and direct
> file edits — nothing else.

## Purpose
Create stable2 GitHub release tags only for components that are eligible for cherry-pick:
1. Read READY Jira tickets from `stable2_status_analysis.yaml`
2. Map those READY tickets to GitHub repos using `pr_list.yaml`
3. For each eligible repo, find the latest semver base tag reachable from `support/stable2`
4. Create a new GitHub release/tag named `{base_tag}_stable2_{YYYYMMDD}` on `support/stable2`
5. Update `generic-pkgrev.inc` in a prepared local `meta-rdk-broadband` workspace
6. Save all results to `stable2_release_tags.yaml`

Most GDCS (`rdk-gdcs`) stable2 components are SRCREV-only and never reach
this skill's actual tag/release/PKGREV steps — see **GDCS (rdk-gdcs)
Repos** below for the one exception (today: cujo-ni/cujo-fpingdq, both the
same repo, marked `tag_eligible: true` in
`config/tracked_repos_gdcs_stable2.yaml`).

## Critical Constraints — Read First
- Use ONLY the repos that correspond to READY tickets from `stable2_status_analysis.yaml`
- Do NOT infer eligibility from Phase 4 or Phase 5 counts alone
- Use GitHub CLI (`gh`) or GitHub MCP for GitHub operations
- Use Gerrit `meta-rdk-broadband` clone only for local file preparation in this skill; do NOT push Gerrit changes here
- `generic-pkgrev.inc` updates are per component, not one shared tag value
- Before creating any GitHub release, print the full per-repo plan and wait for explicit user confirmation
- If `--dry-run` is present, print the full plan and preview the local `generic-pkgrev.inc` updates but do NOT create releases and do NOT edit files
- No dedicated script exists for this skill. It uses only the `gh` CLI/GitHub MCP tools
  and direct file edits named above — do not assume a separate script exists anywhere.
- A repo from `config/tracked_repos_gdcs_stable2.yaml` is only eligible for THIS skill's
  tag/release/PKGREV steps if that file marks it `tag_eligible: true` — every other GDCS
  component is in scope for `stable2-srcrev-updater` only and must be skipped here entirely
  (no tag, no release, no PKGREV entry)
- If `--test-repo` is present, only allow the 6 approved fork repos:
  - `https://github.com/Suganya-Sugumar/data-model-cli`
  - `https://github.com/Suganya-Sugumar/test-and-diagnostic`
  - `https://github.com/Suganya-Sugumar/xconf-client`
  - `https://github.com/Suganya-Sugumar/moca-agent`
  - `https://github.com/Suganya-Sugumar/utopia`
  - `https://github.com/bunnam988/provisioning-and-management`
- If user passed `--test-repo <approved_fork_url>`, keep only that single allowlisted repo in scope
- GDCS repos are never included in `--test-repo` mode

## GDCS (rdk-gdcs) Repos

Only repos marked `tag_eligible: true` in `config/tracked_repos_gdcs_stable2.yaml`
(today: the single repo shared by `cujo-ni`/`cujo-fpingdq`) go through this
skill's tag/release/PKGREV steps — every other GDCS component is
SRCREV-only and is never touched here.

For that eligible GDCS scope:
- **GitHub identity**: switch to `$GH_MESH_USER` before any `gh`/`git`
  command targeting it (record `{default_gh_user}` first, STOP if
  `$GH_MESH_USER` is unset, verify the switch with `gh auth status`), same
  rule as `on-demand-cherry-pick.md`'s "GitHub Identity — Per-Repo Org
  Switch". Switch back to `{default_gh_user}` once this scope's steps are
  done, before this skill finishes.
- **GitHub branch**: ask separately from `{support_branch}` —
  `GitHub target branch for GDCS (rdk-gdcs) tagging:` (no silent default;
  if this skill is invoked from `stable2-meta-sync-orchestrator`, that
  branch was already resolved there and is simply forwarded — see that
  agent's "Before Starting" step 4). Store as `{mesh_support_branch}`.
  If `--mesh-support-branch <name>` was passed on the command line, use it
  directly and skip the interactive ask entirely.
- **Meta-layer**: use `{mesh_gerrit_repo}` from config.yaml (today
  `meta-rdk-comcast-broadband`), workspace
  `{meta_sync_workspace}/meta-rdk-comcast-broadband` (separate from the
  core-nw workspace below), PKGREV file `{mesh_pkgrev_file}` from
  config.yaml. Meta branch (`{mesh_meta_support_branch}`, default from
  config.yaml's key of the same name) can likewise be overridden with
  `--mesh-meta-support-branch <name>`.
- **SSO gotcha**: a GDCS repo/tag lookup that 404s even with
  `$GH_MESH_USER` active means the token needs SSO authorization for
  `comcast-global`, not that the repo/tag is missing.
- Run this scope's Steps 2–6 as their own pass, after the core-nw pass
  completes and identity is switched — do not interleave core-nw and GDCS
  repos in one loop since they need different active `gh` identities.

## Inputs
Required files in project root:
- `stable2_status_analysis.yaml`
- `pr_list.yaml`

Read config from `/workspace/config/config.yaml` as the **default** for each of
these values, then apply any matching CLI override before using it anywhere
below:
- `support_branch` — override with `--support-branch <name>`
- `meta_support_branch` — override with `--meta-support-branch <name>`
- `support_tag_format`
- `gerrit_host` — override with `--gerrit-host <host>`
- `gerrit_repo` — override with `--gerrit-repo <path>`
- `pkgrev_file`
- `meta_sync_workspace`
- `mesh_gerrit_repo`, `mesh_pkgrev_file`, `mesh_meta_support_branch` — GDCS-scope
  equivalents, used only for the `tag_eligible: true` GDCS repo(s) above

Print the resolved values before proceeding: `Using support_branch={support_branch}, meta_support_branch={meta_support_branch}, gerrit_host={gerrit_host}, gerrit_repo={gerrit_repo}` (note if any were overridden vs. taken from config.yaml).

## Step 1 — Build Eligible Repo Scope

1. Load `stable2_status_analysis.yaml`
2. Keep only tickets where `readiness == "READY"`
3. Load `pr_list.yaml`
4. Keep only PR entries whose `ticket` is in the READY ticket set
5. Build unique eligible repo list from those PRs
6. For each eligible repo, keep:
   - `repo`
   - `component`
   - all READY Jira tickets mapped to that repo
   - all PR numbers/URLs mapped to that repo
   - `merge_commit_sha` values if present

7. **Split into two scopes**:
   - **core-nw scope**: every repo whose owner is NOT the GDCS org
   - **GDCS tag-eligible scope**: repos whose owner IS the GDCS org
     (`github_org` in `config/tracked_repos_gdcs_stable2.yaml`, today
     `rdk-gdcs`) AND whose component is listed under a `tag_eligible: true`
     entry in that same file
   - Any GDCS repo that is NOT `tag_eligible: true` is dropped from this
     skill's scope entirely (it's `stable2-srcrev-updater`-only) — print it
     as `SRCREV-only, skipped here` rather than silently discarding it

If no eligible repos remain in EITHER scope, stop with:
`No eligible GitHub repos found from READY Jira tickets.`
If one scope is empty but the other isn't, continue with just the non-empty one.

If `--test-repo` is present:
- Filter core-nw scope to the 6 approved fork repos only; GDCS scope is forced empty
- If a specific allowlisted URL was passed with `--test-repo`, filter eligible repos to that single repo only
- If any non-allowlisted repo appears, print it and exclude it from this run
- Print the final filtered repo list before proceeding

## Step 2 — Prepare Local Meta-Layer Workspace(s)

**Core-nw** — use shared local workspace from config:
- `{meta_sync_workspace}/meta-rdk-broadband`

Meta-layer workspace branch must be `{meta_support_branch}` from config.

If workspace does not exist:
```bash
git clone --branch {meta_support_branch} https://{gerrit_host}/{gerrit_repo} {meta_sync_workspace}/meta-rdk-broadband
```

Notes:
- Gerrit clone must use HTTPS and existing `~/.netrc` credentials
- Do NOT prompt for password interactively

If workspace exists:
```bash
cd {meta_sync_workspace}/meta-rdk-broadband
git fetch origin
git checkout {meta_support_branch}
git pull --ff-only origin {meta_support_branch}
```

Do NOT commit or push in this skill.

This local meta-rdk-broadband workspace MUST track `{meta_support_branch}` from config, not the sprint branch and not the GitHub component support branch.

**GDCS** (only if the GDCS tag-eligible scope is non-empty) — use a SEPARATE
workspace, never the one above:
- `{meta_sync_workspace}/meta-rdk-comcast-broadband`, tracking
  `{mesh_meta_support_branch}` (resolved per "GDCS (rdk-gdcs) Repos" above)

If it does not exist:
```bash
git clone --branch {mesh_meta_support_branch} https://{gerrit_host}/{mesh_gerrit_repo} {meta_sync_workspace}/meta-rdk-comcast-broadband
```
Otherwise `fetch`/`checkout`/`pull --ff-only` it the same way as the core-nw
workspace above. Do NOT commit or push here either.

## Step 3 — Determine Base Tag on support/stable2

For each eligible repo (core-nw under `{default_gh_user}`, GDCS under
`$GH_MESH_USER` — see identity rule above):

1. Clone the GitHub repo shallowly for tag inspection:
```bash
git clone --branch {support_branch} --depth 50 --no-single-branch https://github.com/{owner_repo}.git /tmp/stable2-tagging/{repo_name}
cd /tmp/stable2-tagging/{repo_name}
git fetch --tags --force
```

2. Find the latest semver tag reachable from `origin/{support_branch}`:
For each eligible repo, resolve `{repo_branch}` first: `{support_branch}`
for core-nw repos, `{mesh_support_branch}` for the GDCS tag-eligible repo.

```bash
git tag --merged origin/{repo_branch} --sort=-creatordate
```

3. Filter tags using exact semver rule:
```regex
^[0-9]+\.[0-9]+\.[0-9]+$
```

4. Select the first matching tag as `{base_tag}`

This must represent the latest `X.Y.Z` base tag for the support branch.

If no semver base tag is found for a repo, stop and report that repo.

## Step 4 — Build New Stable2 Tag and Release Plan

For each eligible repo:
- `new_tag = {base_tag}_stable2_{YYYYMMDD}`
- target branch = `{repo_branch}` (resolved in Step 3: `{support_branch}` for
  core-nw, `{mesh_support_branch}` for the GDCS tag-eligible repo)
- release title = `{new_tag}`
- previous tag for notes = `{base_tag}`

Print the full plan for ALL repos:

```text
─────────────────────────────────────────────────────────────────────
Stable2 GitHub Release Plan
─────────────────────────────────────────────────────────────────────
Repo:           rdkcentral/advanced-security
Component:      advanced-security
READY Jira(s):  RDKB-66162
PRs:            #98
Base tag:       2.6.1
New tag:        2.6.1_stable2_20260805
Target branch:  support/stable2
Release notes:  2.6.1...2.6.1_stable2_20260805

Repo:           rdkcentral/utopia
Component:      utopia
READY Jira(s):  XB10-2860, RDKB-65709
PRs:            #101, #102
Base tag:       2.2.1
New tag:        2.2.1_stable2_20260805
Target branch:  support/stable2
Release notes:  2.2.1...2.2.1_stable2_20260805

Repo:           rdk-gdcs/cujo-qoo-module
Component:      cujo-ni, cujo-fpingdq
READY Jira(s):  RDKB-67890
PRs:            #12
Base tag:       1.4.0
New tag:        1.4.0_stable2_20260805
Target branch:  {mesh_support_branch}
Release notes:  1.4.0...1.4.0_stable2_20260805
─────────────────────────────────────────────────────────────────────
Create GitHub stable2 releases for all repos above? [Y/n]
```

If user answers anything other than `Y` or `y`, stop without changes.

If `--dry-run` is present, replace the final prompt line with:
`Preview the GitHub stable2 release actions above? [Y/n]`

This report is mandatory and must include for every repo:
- repo/component
- READY Jira ticket list
- PR number list
- base tag
- new stable2 tag
- target branch
- release notes range

**Stop here and wait for the actual reply** (see AGENTS.md's "Confirmation
gates and questions") — this is a real stop even when this skill was
invoked from inside an orchestrator phase that was itself already
confirmed; do not answer this prompt automatically and proceed straight
to Step 5.

## Step 5 — Create GitHub Release Exactly Like Manual Flow

For each approved repo, create the release/tag on GitHub:

```bash
gh release create {new_tag} \
  --repo {owner_repo} \
  --target {support_branch} \
  --title {new_tag} \
  --generate-notes \
  --notes-start-tag {base_tag}
```

This must produce the manual-style output:
- tag on `{repo_branch}` (`{support_branch}` for core-nw, `{mesh_support_branch}`
  for the GDCS tag-eligible repo)
- release title equal to the stable2 tag
- generated notes/changelog from `{base_tag}` to `{new_tag}`

If `--dry-run` is present:
- Do NOT run `gh release create`
- Instead print the exact `gh release create` command that would be used for each repo

Record for each repo:
- `base_tag`
- `new_tag`
- release URL
- target branch
- Jira tickets
- PR numbers/URLs
- merge commit SHAs

## Step 6 — Update generic-pkgrev.inc Locally

**Core-nw** — in prepared workspace file `{pkgrev_file}`
(`{meta_sync_workspace}/meta-rdk-broadband`):
1. Update only the entries for eligible core-nw components
2. Set each selected component's package revision/tag value to its new stable2 tag
3. Do NOT modify unrelated components
4. Keep changes uncommitted; later Gerrit skill will squash them into one meta-layer change

**GDCS** (only if the GDCS tag-eligible scope is non-empty) — in prepared
workspace file `{mesh_pkgrev_file}`
(`{meta_sync_workspace}/meta-rdk-comcast-broadband`), same rules 1-4 applied
to `cujo-ni`/`cujo-fpingdq` only — never touch any other GDCS component's
entries here (they don't get PKGREV entries at all).

If a component cannot be mapped to an entry in the matching `generic-pkgrev.inc`,
print a warning and continue, but record it in the output YAML.

Before editing either file, print a PKGREV update report per meta-layer:
```text
PKGREV Update Plan (core-nw)
  component -> new stable2 tag

PKGREV Update Plan (GDCS) -- only if non-empty
  component -> new stable2 tag
```

Ask:
`Apply the generic-pkgrev.inc updates above? [Y/n]`

If `--dry-run` is present:
- Do NOT edit either `generic-pkgrev.inc`
- Print the exact component/tag substitutions that would be applied as a
  fenced ```diff block (one `-`/`+` line pair per changed entry — see
  AGENTS.md), not prose — one block per meta-layer

## Step 7 — Save Results

Write `stable2_release_tags.yaml`:

```yaml
execution_date: "2026-08-05T10:30:00Z"
support_branch: "support/stable2"
meta_workspace: ".stable2-meta-sync/meta-rdk-broadband"
pkgrev_file: "conf/include/generic-pkgrev.inc"
repos_tagged: 3
repos:
  - repo: "rdkcentral/advanced-security"
    component: "advanced-security"
    ready_tickets: ["RDKB-66162"]
    prs:
      - number: 98
        url: "https://github.com/rdkcentral/advanced-security/pull/98"
        merge_commit_sha: "d4fc653a36e4529e7d16460dbe40da06e4edf113"
    base_tag: "2.6.1"
    new_tag: "2.6.1_stable2_20260805"
    release_url: "https://github.com/rdkcentral/advanced-security/releases/tag/2.6.1_stable2_20260805"
    pkgrev_updated: true
    meta: "core-nw"
  - repo: "rdkcentral/utopia"
    component: "utopia"
    ready_tickets: ["XB10-2860", "RDKB-65709"]
    prs:
      - number: 101
        url: "https://github.com/rdkcentral/utopia/pull/101"
        merge_commit_sha: "abc1234def5678abc1234def5678abc1234def"
    base_tag: "2.2.1"
    new_tag: "2.2.1_stable2_20260805"
    release_url: "https://github.com/rdkcentral/utopia/releases/tag/2.2.1_stable2_20260805"
    pkgrev_updated: true
    meta: "core-nw"
  - repo: "rdk-gdcs/cujo-qoo-module"
    component: "cujo-ni, cujo-fpingdq"
    ready_tickets: ["RDKB-67890"]
    prs:
      - number: 12
        url: "https://github.com/rdk-gdcs/cujo-qoo-module/pull/12"
        merge_commit_sha: "18750ded9458c3d24837968f96ea8c75e86098ab"
    base_tag: "1.4.0"
    new_tag: "1.4.0_stable2_20260805"
    release_url: "https://github.com/rdk-gdcs/cujo-qoo-module/releases/tag/1.4.0_stable2_20260805"
    pkgrev_updated: true
    meta: "gdcs"
warnings: []
```

Include these extra fields:
- `mode: "production|test-repo|dry-run"`
- `pkgrev_updates_previewed: true|false`

Print the complete saved file contents.

## Verify Completion
Before declaring done, confirm:
- [ ] Eligible repo list came from READY tickets only
- [ ] GDCS repos not marked `tag_eligible: true` were excluded entirely (no tag, no release, no PKGREV)
- [ ] Base semver tag was identified per repo on its own resolved branch (`{support_branch}` or `{mesh_support_branch}`)
- [ ] Full per-repo plan was printed before release creation
- [ ] User explicitly approved release creation
- [ ] GitHub stable2 tag/release was created for each eligible repo
- [ ] `generic-pkgrev.inc` was updated locally for eligible core-nw components, and `mesh_pkgrev_file` for the GDCS tag-eligible one(s), only
- [ ] If GDCS scope was non-empty: GitHub identity was switched to `$GH_MESH_USER` for its steps and restored afterward
- [ ] `stable2_release_tags.yaml` was saved and printed, with `meta` set per repo
