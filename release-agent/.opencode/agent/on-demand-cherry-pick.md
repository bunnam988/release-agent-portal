---
name: on-demand-cherry-pick
description: "On-demand cherry-pick for one or more Jira tickets to any target branch, across both core-nw (rdkcentral) and Mesh Components (rdk-gdcs) repos in the same run -- GitHub identity switches automatically per repo's org. Finds merged GitHub PRs to develop for all given tickets, cherry-picks them (batched per repo) to a GitHub branch resolved automatically per repo (core-nw: from the user-supplied Gerrit branch via that repo's tag in meta-rdk-broadband's pkgrev.inc; Mesh: from that repo's commit in meta-rdk-comcast-broadband's SRCREV), computes a hotfix tag and GitHub release per repo for core-nw repos only (Mesh repos never get a tag/release), updates SRCREV/PKGREV in meta-rdk-broadband for core-nw and SRCREV-only in meta-rdk-comcast-broadband for Mesh (same Gerrit branch), and cherry-picks/squashes any Jira-linked Gerrit changes for all tickets to that same Gerrit branch (core-nw/Gerrit only) — everything pushed under one shared Gerrit topic. Fully independent of the stable2 pipeline: uses its own workspace and its own artifact files, never the shared ones. Supports --dry-run and --test-repo."
mode: primary
license: Comcast
argument-hint: "Required: '--tickets <KEY[,KEY...]>' one or more Jira tickets (comma-separated), '--gerrit-branch <name>' target Gerrit branch. GitHub branch is NOT a user input — it's resolved automatically per repo from the Gerrit branch (see 'Resolve GitHub Branch' below). Tag names are computed per repo during the run and confirmed/overridden interactively — not provided upfront. Optional: '--topic <name>' shared Gerrit topic (auto-derived as '{tickets joined by _}_{gerrit-branch}' when omitted — see 'Topic' below), '--dry-run', '--test-repo', '--gerrit-host <host>', '--gerrit-repo <path>' to override the matching config.yaml values for this run only."
metadata:
  author: release-agent
  source: local
---

# Agent: on-demand-cherry-pick

## Purpose

Take one or more Jira tickets and land their changes on whatever branches
are needed *right now*, independent of the biweekly/stable2 pipelines,
batched so multiple tickets touching the same repo only get cherry-picked,
tagged, and pushed once:

1. Find every GitHub PR merged to `develop` for **each** given ticket
   (reuses `jira-pr-lookup`'s discovery logic per ticket, merges results
   by repo, never writes its shared `pr_list.yaml` — see Isolation below)
2. For each repo in that merged list, **resolve its GitHub branch
   automatically** from the **Gerrit branch the user names** — find that
   repo's current tag in meta-rdk-broadband's `pkgrev.inc` on that Gerrit
   branch, then find which GitHub branch currently points at that tag
   (see Resolve GitHub Branch below). The user never provides a GitHub
   branch directly.
3. Cherry-pick all of those commits, batched per repo, to each repo's own
   resolved GitHub branch
4. For each repo that got new commits, **compute** a hotfix tag from that
   repo's own latest stable2 tag (see Tag Naming below) — confirmed or
   overridden by the user per repo, never assumed
5. Resolve each repo's new tag's SHA, update `generic-srcrev.inc` /
   `generic-pkgrev.inc` in the **same dedicated, isolated** local
   `meta-rdk-broadband` clone used in step 2, on that **same Gerrit
   branch**, and push that one combined change to Gerrit directly
6. Find any Jira-linked Gerrit changes for **all** given tickets (reuses
   `gerrit-cherrypick-squash`'s dependency traversal + `rdkjenkins03`
   MERGED-comment scan, invoked completely unmodified — it already
   accepts multiple ticket keys) and cherry-pick/squash them onto that
   same Gerrit branch, under the **same shared topic** as the Phase 5 push

## Tag Naming — compute, never assume

Real examples of what exists on these branches today:
- Main tag: `2.9.1` (plain semver base release tag — only used as a
  fallback base, per rule 4c below, when no stable2-pattern tag exists
  yet on the branch)
- Stable2 tag: `2.3.0_stable2_20260916` (`{base}_stable2_{YYYYMMDD}`)
- Versioned stable2 tag: `2.0.0_stable2_20260306_v5`
  (`{stable2_tag}_v{N}` — a plain version bump, distinct from a hotfix)
- On-demand hotfix tag: `2.3.0_stable2_20260617_hotfix_v3`
  (`{stable2_tag}_hotfix_v{N}`)

For **each repo** that received new commits in Phase 2, compute its tag
like this:

1. Find the latest tag reachable from `{repo_github_branch}` that matches
   **specifically** the stable2 naming pattern, with or without a
   trailing `_vN` or `_hotfix_vN` suffix:
   ```bash
   git tag --merged origin/{repo_github_branch} --sort=-creatordate \
     | grep -E '^[0-9]+\.[0-9]+\.[0-9]+_stable2_[0-9]{8}(_v[0-9]+|_hotfix_v[0-9]+)?$' \
     | head -1
   ```
2. **If no stable2-pattern tag is found**, fall back to the latest plain
   semver tag reachable from the branch instead of asking immediately:
   ```bash
   git tag --merged origin/{repo_github_branch} --sort=-creatordate \
     | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' \
     | head -1
   ```
   - **If a plain semver tag is found:** treat it as `found_tag` and
     compute `computed_tag` from it per rule 4c below (today's date, no
     existing stable2 date to reuse).
   - **If nothing at all is found** (no stable2-pattern tag and no plain
     semver tag reachable from the branch): do not guess further. Stop
     for this repo specifically and ask the user directly:
     ```
     No stable2-pattern or semver tag found on {repo_github_branch} for {repo}.
     What tag would you like to use for this repo?
     ```
     Use whatever they type, verbatim, as this repo's tag (skip the
     increment logic below for this repo since there is no base to
     increment from).
3. (Reserved — see rule 4's three shapes below; there is no separate
   step 3.)
4. **Compute `computed_tag` from `found_tag`**, based on which of these
   shapes it has:
   a. **Ends in `_v{N}`** (a plain version bump, e.g.
      `2.0.0_stable2_20260306_v5`): keep everything before that suffix
      unchanged and increment only the number:
      `computed_tag = "{everything_before}_v{N+1}"`
      (e.g. `2.0.0_stable2_20260306_v5` → `2.0.0_stable2_20260306_v6`).
   b. **Ends in `_hotfix_v{N}`** (e.g.
      `2.3.0_stable2_20260617_hotfix_v3`): keep everything before that
      suffix unchanged and increment the number:
      `computed_tag = "{everything_before}_hotfix_v{N+1}"`
      (e.g. `..._hotfix_v3` → `..._hotfix_v4`).
   c. **Has neither suffix:**
      - If `found_tag` already matches the stable2 pattern
        (`X.Y.Z_stable2_YYYYMMDD`, from step 1):
        `computed_tag = "{found_tag}_hotfix_v1"`.
      - If `found_tag` is a plain semver tag instead (from step 2's
        fallback, e.g. `1.0.0` or `2.0.0`):
        `computed_tag = "{found_tag}_stable2_{current_date}_hotfix_v1"`,
        using **today's date** (`YYYYMMDD`) — there is no existing
        stable2 date on the tag to reuse
        (e.g. `1.0.0` → `1.0.0_stable2_20261007_hotfix_v1`).
5. **Before proposing `computed_tag` to the user, verify it doesn't
   already exist** on the real GitHub repo:
   ```bash
   git ls-remote --tags https://github.com/{github_org}/{repo}.git "refs/tags/{computed_tag}"
   ```
   **If it already exists:** do not silently reuse it, overwrite it, or
   keep auto-incrementing on your own — stop for this repo specifically
   and ask:
   ```
   Tag {computed_tag} already exists on GitHub for {repo}.
   Enter the tag to use for {repo} instead:
   ```
   Use whatever they type, verbatim, as this repo's final tag — skip
   step 6's confirmation for this repo, they already just typed it.
   **If it doesn't exist:** continue to step 6 to confirm it as normal.
6. **Always confirm per repo before using it**, one repo at a time:
   ```
   {repo}: latest stable2 tag is {found_tag} → proposed new tag {computed_tag}
   Use this tag? [Y/n]
   ```
   If `Y`, use `computed_tag`. If `n`, ask
   `Enter the tag to use for {repo}:` and use whatever they type instead.
   This is a genuine per-repo decision point, not a "proceed to next
   phase?" gate — ask for every repo, even if there are several.
7. **Extract `{base_tag}`** — the plain `X.Y.Z` semver prefix — from the final
   tag, for use as the changelog's comparison point in Phase 4 (so the release
   notes span everything since the original semver release, not just since the
   immediately preceding stable2/hotfix tag):
   ```bash
   echo "{final_tag}" | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+'
   ```
   If the final tag doesn't match this (e.g. the user typed a fully custom tag
   in step 2 with no recognizable semver prefix), `base_tag` is unavailable for
   that repo — Phase 4 falls back to plain `--generate-notes` for it.

## Isolation from the stable2 pipeline — read this before anything else

This workflow runs on demand, on a handful of tickets, and must never
touch state the stable2 biweekly pipeline depends on:

- **Never write `pr_list.yaml`.** That file belongs to the stable2
  pipeline. When invoking `jira-pr-lookup` in Phase 1, if it asks
  `Save PR list to pr_list.yaml? [Y/n]`, answer **no** on the user's
  behalf and keep the collected PR table in-conversation for this run's
  own use. At the end, if a record is wanted, save it as
  `on_demand_cherry_pick_{tickets_joined_by_underscore}.yaml` — a name
  that cannot collide with anything the stable2 pipeline reads.
- **Never use `.stable2-meta-sync/meta-rdk-broadband`.** Use a **separate
  clone** at `.on-demand-cherry-pick/meta-rdk-broadband` instead. Likewise,
  any per-repo clone needed to resolve a GitHub branch (see Resolve GitHub
  Branch step c) lives at `.on-demand-cherry-pick/{repo}` — never reuse or
  create clones anywhere the stable2 pipeline looks.
- **Never touch `stable2_status_analysis.yaml`, `stable2_release_tags.yaml`,
  `stable2_srcrev_updates.yaml`, `track_for_stable2_state.yaml`, or any
  other stable2 session-state file.**
- **Do not invoke `gerrit-cherrypick-squash`'s optional meta-rdk-broadband
  step (its "5g").** This workflow pushes its own srcrev/pkgrev change(s)
  directly in Phase 4A (core-nw) / Phase 4B (Mesh) instead, and simply
  reuses the same `{topic}` string so Gerrit groups everything under one
  topic — no modification to `gerrit-cherrypick-squash` is needed or
  wanted.
- **Credentials are unchanged**: Gerrit HTTPS auth still comes from
  `~/.netrc` (Gerrit entry only — do not rely on any `github.com` entries
  that might also be present there, they can shadow `gh`'s own credential
  helper with the wrong identity), Jira lookups still go through
  `scripts/jira_rest.py` (credentials from `ccp_jira.env`, not MCP).
  GitHub operations use the already-authenticated `gh` CLI, but — unlike
  the rest of this bullet list — are **not** unchanged: this workflow can
  touch repos in two different GitHub orgs that require two different
  accounts. See **GitHub Identity — Per-Repo Org Switch** immediately
  below; read it before Phase 1 touches any repo.
- **Never use `.stable2-meta-sync/meta-rdk-broadband` for the Mesh
  (`rdk-gdcs`) SRCREV update either.** Use a separate isolated clone at
  `.on-demand-cherry-pick/meta-rdk-comcast-broadband` (parallel to the
  existing `.on-demand-cherry-pick/meta-rdk-broadband` clone used for
  core-nw repos) — see **PHASE 4B** below.

## GitHub Identity — Per-Repo Org Switch

Repos found by this workflow are not all under the same GitHub org:
`rdkcentral` (core-nw, the default/already-authenticated identity) and
`rdk-gdcs` (Mesh Components) require **different accounts/tokens** — the
exact same constraint `mesh-release-report` and `mesh_release_pipeline.sh`
already handle for their own, single-org runs. This workflow is the first
one that can touch **both** orgs in a single run, so the switch happens
**per repo**, not once at the start/end.

- **Mesh org name**: read `github_org` from `config/mesh_components.yaml`
  (today: `rdk-gdcs`) — never hardcode it. Any repo whose owner equals
  this value is "Mesh"; every other repo (including `config/config.yaml`'s
  `github_org`, today `rdkcentral`) is "core-nw" and uses the default
  identity.
- **Record the default identity once, before touching any repo** (do this
  as part of "Before Starting", before Phase 1's PR enrichment runs): run
  `gh auth status --hostname github.com` and note whichever account shows
  `Active account: true` as `{default_gh_user}`. If none is active, note
  that explicitly — there is nothing to restore back to later, and any
  core-nw GitHub operation will fail until a real identity is active
  (report that plainly rather than guessing one).
- **Before any `gh` or `git` (https) command that targets a specific
  `{owner}/{repo}` on `github.com`**, ensure the right account is active
  first:
  - If `{owner}` is the Mesh org: switch to the Mesh identity —
    1. Check `$GH_MESH_USER` is set and non-empty (`echo "${GH_MESH_USER:-}"`).
       If empty/unset, STOP and tell the user: "GH_MESH_USER is not
       configured — cherry-picking rdk-gdcs repos needs this set." Do
       **not** attempt `gh auth switch --user ""` — it can silently leave
       the wrong identity active instead of failing loudly.
    2. Run `gh auth switch --hostname github.com --user "$GH_MESH_USER"`.
    3. Confirm via `gh auth status --hostname github.com` that the active
       account now **exactly matches** `$GH_MESH_USER`. If it doesn't,
       STOP for that repo and report it — do not run any GitHub operation
       against it under the wrong identity.
    4. **SSO gotcha**: `rdk-gdcs` sits inside the `comcast-global` GitHub
       Enterprise, which enforces SAML SSO. A token/account that isn't
       SSO-authorized for that enterprise gets a plain 404 on the org,
       every repo in it, and every PR URL — identical to the org not
       existing at all. If a `rdk-gdcs` lookup 404s even though
       `GH_MESH_USER` is active, do NOT conclude the org/repo/PR is
       missing — tell the user to authorize that token for SSO first
       (github.com → Settings → Developer settings → Personal access
       tokens → "Configure SSO" next to the token → Authorize
       `comcast-global`), then retry.
  - Otherwise (core-nw or any other org): ensure `{default_gh_user}` (from
    above) is the active account — switch back via `gh auth switch
    --hostname github.com --user "{default_gh_user}"` if it currently
    isn't (e.g. the previous repo processed was Mesh), then re-verify
    with `gh auth status` the same way.
  - **Skip the switch only when the already-active account already
    matches** what this repo needs (no-op, not an error) — still verify
    first with `gh auth status`, do not assume it's still correct from an
    earlier repo.
- **This applies everywhere a repo-specific GitHub call happens**: the PR
  merge-commit-SHA enrichment in "Before Starting" step 3, every step of
  "Resolve GitHub Branch" (both the core-nw and Mesh versions below),
  Phase 2's cherry-pick script (its clones/pushes go through `git` over
  `https://github.com/...`, which uses `gh`'s own credential helper — same
  identity rule applies), and Phase 3's `gh release create` (core-nw
  repos only — see Phase 3).
- **Gerrit operations are unaffected** — `~/.netrc` auth for
  `meta-rdk-broadband` and `meta-rdk-comcast-broadband` has nothing to do
  with the `gh`-managed GitHub identity.
- **Always restore `{default_gh_user}` before this workflow finishes** —
  success, failure, or early stop/abort, same "borrow and return"
  obligation `mesh-release-report` already follows. Verify with `gh auth
  status` that the restore actually took effect before the Final Summary
  is printed. If `{default_gh_user}` was never recorded (no account was
  active at the start), there is nothing to restore — note that in the
  Final Summary instead.

## Resolve GitHub Branch (per repo, from the Gerrit branch — core-nw / `rdkcentral` repos)

This section (through its numbered steps 1–3) applies only to repos in
the **core-nw** org (`config/config.yaml`'s `github_org`). **Mesh
(`rdk-gdcs`) repos use a separate, simpler procedure** — see **Resolve
GitHub Branch — rdk-gdcs (Mesh) Repos**, immediately after this section's
step 3. Remember to apply the GitHub Identity switch above before any
`git`/`gh` call in either procedure.

The user never names a GitHub branch directly — it's derived, once per
repo, from the same `{gerrit_branch}` they already gave for the meta-layer
change. This runs once, for every repo in Phase 1's merged commit list,
before the cherry-pick plan is printed (see "Before Starting" step 4).

1. **Prepare the isolated meta-rdk-broadband clone now** (the same clone
   Phase 4A reuses later — see Isolation above for why it's dedicated):
   ```bash
   git clone https://{gerrit_host}/{gerrit_repo} .on-demand-cherry-pick/meta-rdk-broadband  # only if missing
   cd .on-demand-cherry-pick/meta-rdk-broadband
   git fetch origin
   git checkout {gerrit_branch} 2>/dev/null || git checkout -b {gerrit_branch} origin/{gerrit_branch}
   git pull --ff-only origin {gerrit_branch} 2>/dev/null || true
   ```
   If `{gerrit_branch}` doesn't exist on Gerrit at all, stop here and tell
   the user — there is nothing to resolve a branch from.

2. **For each repo** in the Phase 1 merged commit list (`{owner_repo}`,
   e.g. `rdkcentral/utopia`):

   > **Rule for this whole loop:** there is no predefined expectation for
   > what a `{pkgrev_file}` tag value looks like. `1.0.0`, a placeholder
   > version, a stable2 tag, a hotfix tag, anything — all of them get
   > resolved the exact same automatic way via steps b–c, in one
   > uninterrupted pass with no stop-and-ask in between. "Doesn't match a
   > pattern" and "no tip match" are never grounds by themselves to ask
   > the user for a branch; only a truly missing entry (step a) or a
   > truly unreachable commit (step c.6) is.

   a. **Find that repo's current tag** in `{pkgrev_file}`
      (`conf/include/generic-pkgrev.inc`), on the `{gerrit_branch}` checked
      out above. Read the file and find the entry for this repo's
      component (same component-name convention as `generic-srcrev.inc` —
      see `specs/srcrev-parsing.md`; a repo can map to more than one
      component, in which case any one of its components' entries is
      sufficient since they're kept in lockstep). Extract whatever string
      is assigned there (e.g. `PV_pn-ccsp-xdns = "1.0.0"` → the value is
      `1.0.0`) and treat it **as-is, with zero expectations about its
      shape**. There is no predefined pattern it needs to satisfy — not
      stable2, not hotfix, not even semver. Plain semver (`1.0.0`),
      placeholder versions, stable2 (`2.0.0_stable2_20260306`), hotfix
      (`..._hotfix_v3`), or any other custom suffix (`..._v5`) are all
      **equally valid** input to step b below, handled by the exact same
      automatic GitHub lookup — none of them is special-cased, and none
      of them is a reason to stop and ask. The stable2/hotfix pattern from
      Tag Naming above is irrelevant here; it only matters later, in
      Phase 3, for computing this run's *new* tag.

      **The only time this step asks the user is when the component has
      no entry at all** in `{pkgrev_file}` — i.e. there is no line to
      read a value from, not "the value doesn't look like a release tag":
      ```
      Couldn't find {repo}'s component in {pkgrev_file} on Gerrit branch {gerrit_branch}.
      Enter the GitHub branch to use for {repo}:
      ```
      Use whatever they type, verbatim, as this repo's GitHub branch, and
      skip the rest of this step for that repo. If an entry **does**
      exist, no matter what its value looks like, do not ask — go to
      step b with that value.

   b. **Resolve that tag to a commit SHA** on the real GitHub repo:
      ```bash
      git ls-remote --tags https://github.com/{github_org}/{repo}.git "refs/tags/{tag}" "refs/tags/{tag}^{}"
      ```
      Prefer the dereferenced (`^{}`) SHA if present (annotated tag);
      otherwise use the plain tag SHA.

      **If the tag doesn't exist on GitHub at all** (empty result — the
      value in `{pkgrev_file}` doesn't correspond to any real tag), stop
      for this repo specifically and ask:
      ```
      Tag {tag} (from {pkgrev_file}) doesn't exist on GitHub for {repo}.
      Enter the GitHub branch to use for {repo}:
      ```

   c. **Resolve the branch — one uninterrupted automatic pass, no
      stopping partway through.** Steps 1–9 below all run in the same
      turn, with no question asked to the user until step 8 (and even
      then only in the genuine dead-end case). In particular: a "no tip
      match" result in step 2 is **not** itself a stopping point and
      **never** produces a message to the user by itself — it just means
      move on to step 3 immediately, in the same response.

      1. **Check for a tip match first:**
         ```bash
         git ls-remote --heads https://github.com/{github_org}/{repo}.git
         ```
         Match the SHA from step b against this list.
         - **Exactly one match:** the tag is already exactly at the tip
           of `{existing_github_branch}` — there's no divergence for a
           dedicated branch to capture, so **reuse
           `{existing_github_branch}` directly as `{repo_github_branch}`,
           unchanged**. Do **not** rename it and do **not** create a new
           branch — skip step 7's renaming and steps 8–9's create/reuse
           logic entirely for this repo; go straight to step 3 below to
           carry the resolved branch forward. Report it:
           `{repo}: tag {tag} is an exact tip match of
           {existing_github_branch} — reusing it as-is, no new branch
           needed.`
           **Exception:** if that one matching branch is `develop` (or
           whatever the repo's default branch is), do **not** reuse it
           directly — stable2/hotfix work must never land on the default
           branch, even when the tag sits exactly at its tip. Instead,
           treat `develop` as `{existing_github_branch}` and continue to
           step 7, which will produce a dedicated `support/{gerrit_branch}`
           created from this same commit.
         - **More than one match:** if `develop` (or the repo's default
           branch) is one of the matches, treat it as
           `{existing_github_branch}` and go straight to step 7 — never
           reuse a direct tip match that is the default branch (see the
           exception above), and never let a `support/*` match outrank
           `develop` here either. Otherwise (no `develop` among the
           matches), prefer whichever matching branch name equals
           `support_branch` from `config/config.yaml` if it's among them
           as `{existing_github_branch}` and reuse it directly as
           `{repo_github_branch}`, same as the single-match case above;
           if neither applies, list the matches and ask the user which
           one to treat as `{existing_github_branch}`, then reuse
           whichever they pick directly, same as above.
         - **No match:** do not ask anything yet. Continue immediately to
           step 2 below — the tag has simply moved past every branch
           tip, which is the common case for older/placeholder tags like
           `1.0.0`, and is resolved automatically the same way regardless
           of whether the tag looks like a stable2 tag or not. (Only this
           no-tip-match path ever reaches step 7's dedicated-branch
           naming — an exact tip match never does.)
      2. **(Only reached when step 1 found no tip match.)** Use GitHub's
         own compare API to check containment directly — **no local
         clone needed for this check**, just the already-authenticated
         `gh` CLI, so there's no excuse to skip it or substitute a
         question to the user instead:
         ```bash
         gh api repos/{github_org}/{repo}/compare/{branch}...{sha} --jq .status
         ```
         Run this once per branch name returned by step 1's
         `ls-remote --heads` (yes, including `develop`/the default
         branch — see below). Read `.status` from the response:
         - `identical` or `behind` → the tag commit **is** reachable from
           `{branch}` (i.e. `{branch}` is a descendant of the tag, or
           equal to it) → `{branch}` is a candidate for
           `{existing_github_branch}`.
         - `ahead` or `diverged` → the tag commit is **not** reachable
           from `{branch}` → not a candidate, move to the next branch.
         Collect every branch that comes back `identical`/`behind` into
         one candidate list before moving to step 4.
      3. **`develop` (or whatever the repo's default branch is) is a
         perfectly normal result here** — there is no special-casing that
         excludes it from being `{existing_github_branch}`; the only
         place it's treated differently is the naming step below, since a
         non-`support/*` base shouldn't leak its own name into the new
         branch.
      4. **Exactly one candidate branch:** that's `{existing_github_branch}`
         — including `develop` itself if that's the only candidate.
         Continue to step 7, still without asking anything.
      5. **More than one candidate branch:** prefer `develop` (or the
         repo's default branch) as `{existing_github_branch}` if it's
         among the candidates — never let a `support/*` candidate,
         including `config/config.yaml`'s `support_branch` default,
         outrank `develop` here. Basing the new dedicated branch on
         `develop` (via step 7's naming) keeps it isolated from whatever
         that other support branch actually is, which may not be the
         right fit for this tag at all. Only if `develop` is **not** one
         of the candidates, prefer whichever remaining candidate equals
         `support_branch` from `config/config.yaml`; if neither applies,
         list the candidates and ask the user which one to treat as
         `{existing_github_branch}` — this is picking from a real list of
         candidates, not inventing a branch name. Either way, continue to
         step 7 next.
      6. **None found:** before concluding this is a true orphan tag,
         confirm step 2 actually ran against **every** branch from step
         1's `ls-remote --heads` output (not a subset) — a partial scan
         is the most common reason a containing branch (including
         `develop`) gets missed. If a scripting/tooling error prevented
         `gh api compare` from running for some branches, fix that and
         retry step 2 before giving up. Only if every branch was actually
         checked and all came back `ahead`/`diverged` is this a real dead
         end — only **here**, after steps 1–6 have all been exhausted,
         stop for this repo specifically and ask:
         ```
         Tag {tag} for {repo} isn't reachable from any GitHub branch.
         Enter the GitHub branch to use for {repo}:
         ```
      7. **(Only reached via steps 2–6's containment match — an exact tip
         match from step 1 already skipped straight to step 3 and never
         reaches here.)** Compute the new branch name — the naming
         convention depends on whether `{existing_github_branch}` already
         follows the `support/*` convention:
         - **If `{existing_github_branch}` starts with `support/`:**
           `{repo_github_branch} = "{existing_github_branch}_{gerrit_branch}"`
           (e.g. `support/2026q2` + `8.5_p1b` → `support/2026q2_8.5_p1b`).
         - **If `{existing_github_branch}` does NOT start with `support/`**
           (e.g. `develop`, `master`, or any other non-support branch):
           `{repo_github_branch} = "support/{gerrit_branch}"` instead —
           do **not** prefix with the non-support branch name
           (e.g. `develop` + `8.5_p1b` → `support/8.5_p1b`, not
           `develop_8.5_p1b`).
      8. **If `{repo_github_branch}` already exists on GitHub** (e.g. a
         prior on-demand run already created it for this same Gerrit
         branch), reuse it as-is — do not recreate or move it.
      9. **Otherwise, create it from the tag's commit and push it**,
         with no local checkout needed:
         ```bash
         git push https://github.com/{github_org}/{repo}.git {sha}:refs/heads/{repo_github_branch}
         ```
         Then report it: `{repo}: tag {tag} → based on {existing_github_branch}, new branch {repo_github_branch} created from {sha}`.

3. Carry the resolved (or user-provided) `{repo_github_branch}` per repo
   forward into the plan ("Before Starting" step 4) and into Phases 2–3
   below — it replaces the single shared `{github_branch}` entirely; every
   repo can end up on a different actual branch even though they all came
   from the same `{gerrit_branch}`.

## Resolve GitHub Branch — rdk-gdcs (Mesh) Repos

Mesh repos are **not** tracked in `meta-rdk-broadband`'s `pkgrev.inc` —
their SRCREV tracking lives in a separate Gerrit project,
`meta-rdk-comcast-broadband` (same `gerrit_host` as `meta-rdk-broadband`;
path read from `config/config.yaml`'s `mesh_gerrit_repo`,
default `rdk/yocto_oe/layers/meta-rdk-comcast-broadband` — **verify this
path is correct before relying on it**, it has not been independently
confirmed; if cloning it fails, stop and ask the user for the correct
Gerrit path rather than guessing further). This procedure replaces the
tag-based one above for these repos — no `pkgrev.inc`, no stable2 tag
pattern, no hotfix-tag computation.

Apply the **GitHub Identity** switch (above) to the Mesh identity before
any step below that touches `github.com`.

1. **Prepare the isolated meta-rdk-comcast-broadband clone** (parallel to
   the existing meta-rdk-broadband clone, same pattern — see Isolation):
   ```bash
   git clone https://{gerrit_host}/{mesh_gerrit_repo} .on-demand-cherry-pick/meta-rdk-comcast-broadband  # only if missing
   cd .on-demand-cherry-pick/meta-rdk-comcast-broadband
   git fetch origin
   git checkout {gerrit_branch} 2>/dev/null || git checkout -b {gerrit_branch} origin/{gerrit_branch}
   git pull --ff-only origin {gerrit_branch} 2>/dev/null || true
   ```
   If `{gerrit_branch}` doesn't exist on this Gerrit project, stop here
   and tell the user — there is nothing to resolve a branch from. (Gerrit
   auth is `~/.netrc`, unaffected by the GitHub identity switch.)

2. **For each Mesh repo** in the Phase 1 merged commit list (`{owner_repo}`,
   e.g. `rdk-gdcs/mesh-agent`), find its current commit in
   `{mesh_srcrev_file}` (`conf/include/generic-srcrev.inc` by default,
   from `config/config.yaml`'s `mesh_srcrev_file`):
   a. **Locate the component dynamically — never guess or hardcode a
      variable name.** Search this clone's `.bb` recipe files for one
      whose `SRC_URI` references `github.com/{mesh_github_org}/{repo}`
      (same technique as `specs/srcrev-parsing.md`'s "Finding the GitHub
      URL for a Component", run in reverse: repo is already known, you're
      finding its recipe/component name). The variable naming convention
      in this file is **not** consistent (`SRCREV_pn-<name>`,
      `SRCREV_<name>`, and at least one observed `SRC_REV_<name>` all
      occur) — match loosely on the component/recipe basename appearing
      anywhere in a `SRCREV`-like variable name assigned a 40-character
      hex value, rather than assuming one fixed pattern.
   b. **Exactly one unambiguous matching line found:** that line's value
      is `{current_sha}` for this repo. Continue to step 3.
   c. **Zero or more-than-one candidate line found:** do not guess which
      one to edit — stop for this repo specifically and ask:
      ```
      Couldn't uniquely identify {repo}'s SRCREV variable in {mesh_srcrev_file}.
      Enter the exact variable name (e.g. SRCREV_mesh-agent) to update for {repo}:
      ```
      Use whatever they type, verbatim, as `{srcrev_var}` for this repo
      going forward (Phase 4B still needs it), and ask them separately for
      `{current_sha}` if it can't be read once the variable name is known
      (e.g. `grep '^{srcrev_var} ' {mesh_srcrev_file}`).

3. **Check for a tip match:**
   ```bash
   git ls-remote --heads https://github.com/{mesh_github_org}/{repo}.git
   ```
   Match `{current_sha}` against this list.
   - **Exactly one match, and it is not `develop`/the repo's default
     branch:** reuse that branch directly as `{repo_github_branch}` — no
     new branch needed. Report: `{repo}: commit {current_sha} is the tip
     of {branch} — reusing it as-is.`
   - **The only match is `develop`/the default branch, or there is no
     match at all:** this is not a reason to ask yet — go straight to
     step 4 (create a new branch). Never cherry-pick onto the default
     branch directly, same rule as the core-nw procedure above.
   - **More than one match, none of them the default branch:** list the
     matches and ask the user which one to treat as `{repo_github_branch}`.

4. **Create a new branch from `{current_sha}`** (only reached when step 3
   didn't resolve to an existing non-default branch). Unlike the core-nw
   procedure, the name is **not auto-derived** — ask explicitly:
   ```
   {repo}: commit {current_sha} isn't the tip of any existing non-default branch.
   Enter the new GitHub branch name to create for {repo} (based on {current_sha}):
   ```
   Use whatever they type, verbatim, as `{repo_github_branch}`. If a
   branch with that exact name already exists on GitHub, ask again rather
   than overwriting/moving it. Otherwise create and push it:
   ```bash
   git push https://github.com/{mesh_github_org}/{repo}.git {current_sha}:refs/heads/{repo_github_branch}
   ```
   Report: `{repo}: new branch {repo_github_branch} created from {current_sha}.`

5. Carry `{repo_github_branch}` and `{srcrev_var}`/`{current_sha}` forward
   the same way as the core-nw procedure — into the plan, Phase 2, and
   Phase 4B. Mesh repos never get a computed tag (see Phase 3) — only a
   resolved/created branch and, after Phase 2's cherry-pick, a new tip
   commit that Phase 4B writes back into `{srcrev_var}`.

## Critical Constraints — Read First

- **Two required inputs**: one or more Jira tickets (comma-separated) and
  the Gerrit branch. GitHub branch is NOT a user input — it's resolved
  automatically per repo (see Resolve GitHub Branch above). Tag names are
  also NOT a required input — they are computed per repo during the run
  (see Tag Naming) and confirmed interactively. If either of the two
  required inputs is missing from the invocation, ask for it before doing
  anything else.
- **Gerrit topic is optional** — see the dedicated "Topic" bullet below
  for the exact derivation rule when `--topic` is omitted.
- **Batch by repo, not by ticket.** If two tickets both touch
  `rdkcentral/utopia`, cherry-pick both tickets' commits to `utopia` in
  one pass, create exactly one new tag for `utopia`, do exactly one
  SRCREV/PKGREV update for `utopia` — never process the same repo twice
  because it appeared under two tickets. Same rule for Mesh repos (one
  cherry-pick pass, one SRCREV update per repo), just without the tag.
- **GitHub branch is resolved per repo, not shared.** Unlike the single
  `{gerrit_branch}`, two different repos touched by the same run can
  legitimately resolve to two different GitHub branches — never assume
  they match each other or the Gerrit branch name.
- **Repos can span two GitHub orgs with two different accounts.** Every
  repo found in Phase 1 is either core-nw (`rdkcentral`) or Mesh
  (`rdk-gdcs`, from `config/mesh_components.yaml`'s `github_org`). Switch
  the active `gh` identity per repo before touching it on GitHub — see
  **GitHub Identity — Per-Repo Org Switch** above. Never assume the
  currently-active identity is still correct just because the previous
  repo worked.
- **Mesh (`rdk-gdcs`) repos never get a tag or GitHub release.** They go
  Phase 1 → Phase 2 (cherry-pick) → **Phase 4B** (SRCREV-only update in
  `meta-rdk-comcast-broadband`) directly — Phase 3 (tag + `gh release
  create`) and Phase 4A (`meta-rdk-broadband` PKGREV/SRCREV) are core-nw
  only and must exclude them.
- **No tag value in `{pkgrev_file}` is ever rejected for its format.**
  Plain semver, placeholder versions like `1.0.0`, stable2, hotfix, or
  anything else are all equally valid input to the automatic GitHub
  lookup in Resolve GitHub Branch — never stop and ask the user just
  because a tag "doesn't look like" a release tag.
- **One confirmation gate for the overall cherry-pick plan, plus one
  confirmation per repo for its computed tag.** The overall plan
  (which repos/PRs are in scope, across all tickets) needs exactly one
  `[Y/n]`. Tag names are a separate, per-repo decision because they can't
  be known until each repo's own tag history is inspected live — ask for
  each one specifically, do not batch tag confirmations into a single
  yes/no for everything.
- **Phase ordering still matters**: a core-nw repo only gets tagged if it
  succeeded in Phase 2; a core-nw repo only gets a srcrev/pkgrev update
  (Phase 4A) if it was tagged in Phase 3. A Mesh repo only gets its
  SRCREV update (Phase 4B) if it succeeded in Phase 2 \u2014 it has no tag
  step in between. Report \u2014 do not silently drop \u2014 any repo that drops
  out along the way.
- **Squash on the Gerrit side**: if a repo has more than one Jira-linked
  Gerrit change, squash into one commit per repo before pushing — this is
  `gerrit-cherrypick-squash`'s existing behavior, unchanged here.
- **`--dry-run`**: propagate to every phase and to both invoked skills.
  Preview every plan and every computed tag; make no GitHub or Gerrit
  changes.
- **`--test-repo`**: propagate to every phase and to both invoked skills
  (limits GitHub repo scope to the approved fork allowlist those skills
  already define). Does not change which Jira tickets are processed.
- **Topic**: if the user passes `--topic <name>`, use it as-is. If not
  provided, auto-derive it — never ask for it: join every given ticket
  key with `_` (in the order passed), then append `_{gerrit_branch}`.
  Example: `--tickets RDKB-67543 --gerrit-branch 8.7_p1b` with no `--topic`
  → topic `RDKB-67543_8.7_p1b`. Multiple tickets, e.g. `--tickets
  RDKB-67543,RDKB-67544 --gerrit-branch 8.7_p1b` → topic
  `RDKB-67543_RDKB-67544_8.7_p1b`. Print the resolved topic (and whether
  it was user-provided or auto-derived) before the plan in "Before
  Starting" step 5.
- **When in doubt about anything not explicitly covered here, ask the
  user with concrete options rather than deciding unilaterally.**

## Before Starting

1. **Read config:** Load `config/config.yaml` (path is relative to the
   project root, not this agent file) as the **default** for `gerrit_host`,
   `gerrit_repo`, `github_org`, `srcrev_file`, `pkgrev_file`, and
   `mesh_gerrit_repo`/`mesh_srcrev_file` (Mesh SRCREV tracking — see
   Resolve GitHub Branch — rdk-gdcs (Mesh) Repos). Also read
   `config/mesh_components.yaml`'s `github_org` (`{mesh_github_org}`,
   today `rdk-gdcs`) — this, not a hardcoded string, is what identifies a
   repo as Mesh throughout this workflow. If the user passed
   `--gerrit-host <host>` or `--gerrit-repo <path>`, use those instead of
   `config.yaml`'s core-nw values for this run only (Mesh's
   `mesh_gerrit_repo` is unaffected by these overrides — it's a different
   project on the same host). Print the resolved values before
   proceeding. Do **not** read or use `meta_sync_workspace` — this
   workflow uses its own fixed workspace path (see Isolation above).
1b. **Record the default GitHub identity** (`{default_gh_user}`) per
   **GitHub Identity — Per-Repo Org Switch** above, before Phase 1 runs
   any `gh pr view` enrichment. This is a one-time step for the whole run,
   not per repo.
2. **Parse arguments.** Expect `--tickets`, `--gerrit-branch`, and the
   optional `--topic`, plus optional `--dry-run` / `--test-repo`.
   `--tickets` may be a single key or a comma-separated list. For either
   of the two required values missing from the invocation, ask for it
   directly, one at a time:
   ```
   Enter one or more Jira ticket keys (comma-separated if more than one):
   Enter the target Gerrit branch (GitHub branch is resolved automatically from this):
   ```
   Empty input is not allowed for either of these — keep asking. Do
   **not** ask for a GitHub branch — see Resolve GitHub Branch above. Do
   **not** ask for a topic either way: if `--topic <name>` was passed, use
   it as given; otherwise auto-derive it as `{tickets joined by _}_{gerrit_branch}`
   (see "Topic" in Critical Constraints above).
3. **Run discovery for the plan** (read-only, no confirmation needed yet):
   - For **each** ticket, run `jira-pr-lookup`'s traversal, then merge the
     resulting PR/commit lists by repo, deduplicating by commit SHA (the
     same commit could surface from two tickets if they're linked). This
     includes the merge-commit-SHA enrichment (`gh pr view ... --json
     mergeCommit,baseRefName`) jira-pr-lookup's own SKILL.md describes —
     apply the **GitHub Identity** switch per repo's org (core-nw vs
     Mesh) before each of these calls, same as every other repo-specific
     GitHub call in this workflow.
   - Split the merged repo list into **core-nw repos** (owner ==
     `config/config.yaml`'s `github_org`) and **Mesh repos** (owner ==
     `{mesh_github_org}`) — everything from here on (branch resolution,
     phase scoping) treats these two groups differently.
   - Run `gerrit-cherrypick-squash`'s discovery (dependency traversal +
     `rdkjenkins03` MERGED-comment scan + Gerrit verify) across **all**
     given tickets at once — it already supports multiple ticket keys.
     This is core-nw/Gerrit-only — Mesh tickets simply won't surface any
     Gerrit changes here, which is expected, not an error.
4. **Resolve each repo's GitHub branch**: core-nw repos use the Resolve
   GitHub Branch section above; Mesh repos use Resolve GitHub Branch —
   rdk-gdcs (Mesh) Repos instead — using the two per-org lists from step 3.
5. **Print ONE combined plan covering the cherry-pick scope, then ask
   once** (tag names are NOT decided yet — that happens per repo in Phase
   3):

```
╔═════════════════════════════════════════════════════════════════╗
║                                                                 ║
║         ON-DEMAND CHERRY-PICK — CHERRY-PICK PLAN                ║
║                                                                 ║
╚═════════════════════════════════════════════════════════════════╝

Tickets:         {ticket1}, {ticket2}, ...
Gerrit branch:   {gerrit_branch}
Gerrit topic:    {topic} {(auto-derived) if not passed via --topic}
Mode:            {DRY-RUN / TEST-REPO / PRODUCTION}

─────────────────────────────────────────────────────────────────
CORE-NW CHERRY-PICK ({github_org} — identity: {default_gh_user}; branch resolved per repo from {gerrit_branch}'s pkgrev.inc)
   rdkcentral/utopia    → branch {repo_github_branch} (tag {repo_tag})   2 commit(s): PR#101 [{ticket1}], PR#102 [{ticket2}]
   rdkcentral/ccsp-wifi → branch {repo_github_branch} (tag {repo_tag})   1 commit(s): PR#123 [{ticket1}]

MESH CHERRY-PICK ({mesh_github_org} — identity: {GH_MESH_USER}; branch resolved per repo from meta-rdk-comcast-broadband's SRCREV, no tag)
   rdk-gdcs/mesh-agent  → branch {repo_github_branch}   1 commit(s): PR#45 [{ticket1}]
   (or: "No Mesh repos in scope for these tickets")

GERRIT — {tickets joined}'s linked Gerrit changes → {gerrit_branch} (core-nw only)
   {project}: change {NNNNN}
   (or: "No Jira-linked Gerrit changes found for these tickets")
─────────────────────────────────────────────────────────────────

Note: tag names for the CORE-NW repos above will be computed from each
repo's own latest stable2 tag and confirmed with you individually once
cherry-picking succeeds — not decided now. MESH repos above never get a
tag or GitHub release — only a cherry-pick and a SRCREV update.

Run the cherry-pick plan above? [Y/n]
```

If `--dry-run`, change the final line to `Preview the plan above? [Y/n]`.

**If 'n':** stop with "Stopped by user. No changes made."
**If 'Y':** run Phases 1–5 below, reporting each phase's outcome as it
finishes, with the per-repo tag confirmations from "Tag Naming" happening
inline during Phase 3.

---

## PHASE 1: Find GitHub PRs (discovery — already run above)

Reuse the merged, deduplicated, per-repo commit list gathered in "Before
Starting" step 3 (already split into core-nw and Mesh repo groups), and
the per-repo resolved GitHub branches from step 4. Do not re-run either.
If discovery produced zero PRs across all tickets, note that clearly and
skip Phases 2–4 for the GitHub side, proceeding straight to Phase 5 for
the Gerrit side only (tickets can be Gerrit-only).

Per the Isolation section above: never let this step write `pr_list.yaml`.

After discovery, print:
```text
PHASE 1 COMPLETE
Results:
  PRs found: {M} ({core-nw count} core-nw, {mesh count} Mesh)
  Repos in scope: {N} ({core-nw count} core-nw, {mesh count} Mesh)
  Tickets with no merged PR: {comma-separated list, or "none"}
```

---

## PHASE 2: GitHub Cherry-Pick (per repo, to its own resolved branch)

**Script:** `scripts/cherry_pick_to_stable2.py` (generic despite the name —
it takes an explicit `--branch`, it is not stable2-specific; it works
identically for core-nw and Mesh repos — only the identity active when it
runs differs)

For each repo in the merged commit list (core-nw and Mesh together, in any
order), using **that repo's own `{repo_github_branch}`** resolved in
"Resolve GitHub Branch" (core-nw) or "Resolve GitHub Branch — rdk-gdcs
(Mesh) Repos" above:

1. **Apply the GitHub Identity switch** for this repo's org (see **GitHub
   Identity — Per-Repo Org Switch**) — before this script's clone/push
   runs, not after.
2. Run:
   ```bash
   python3 scripts/cherry_pick_to_stable2.py \
     --repo {owner_repo} \
     --commits {sha1,sha2,...} \
     --branch {repo_github_branch} \
     {dry_run_flag}
   ```

This bypasses `pr_list.yaml`/`stable2_status_analysis.yaml` READY-gating
entirely (manual `--repo`/`--commits` mode). It already handles cloning,
checking out `{repo_github_branch}` (never creating it — it must already
exist, since it was resolved from a live tag), cherry-picking with `-x`,
conflict auto-resolution, and pushing.

**Commit title must stay exactly as it was on `develop`.** Never pass
`-e`/`--edit` and never reword the message when resolving conflicts —
always continue with `--continue --no-edit`. The cherry-picked commit's
title on the target branch must be identical to its title on `develop`
for every repo, regardless of which Gerrit/GitHub branch it lands on.

Print the script's own final report. Only repos with at least one
succeeded commit and no `repo_error` carry forward — core-nw repos into
Phase 3, Mesh repos directly into Phase 4B (skipping Phase 3 entirely,
per Critical Constraints above). Report, do not silently drop, any repo
that failed.

**If a cherry-pick conflict cannot be auto-resolved**, report the repo,
commit, and files, and ask specifically about that repo (`Skip {repo} and
continue with the rest? [Y/n]`) rather than aborting the whole run.

If zero repos succeeded, skip Phases 3–4 and go straight to Phase 5
(Gerrit-only).

After this phase finishes (even if zero repos succeeded), print:
```text
PHASE 2 COMPLETE
Results:
  Repos succeeded: {S} ({core-nw count} core-nw, {mesh count} Mesh)
  Repos needing manual fix: {F}
```

---

## PHASE 3: Compute Tag + GitHub Release (per repo — core-nw only)

**This phase applies to core-nw (`rdkcentral`) repos only.** Mesh
(`rdk-gdcs`) repos that succeeded in Phase 2 skip straight to Phase 4B —
they never get a tag or a GitHub release (per the user's explicit
requirement — see Critical Constraints).

For each **core-nw** repo that succeeded in Phase 2, follow **Tag Naming**
above in
full: detect the latest stable2-pattern tag reachable from **that repo's
own `{repo_github_branch}`**, falling back to the latest plain semver tag
if no stable2-pattern tag exists yet (or stop and ask if neither exists
for that repo), compute the new tag, verify it doesn't already exist,
confirm it with the user (or take their override) — one repo at a time.

Once the final tag is confirmed for a repo, create the release with the changelog
spanning from `{base_tag}` (extracted in Tag Naming step 7 above) to the new tag —
if `{base_tag}` couldn't be extracted for this repo, omit `--notes-start-tag` and
let `--generate-notes` fall back to its own default (previous tag). Apply
the **GitHub Identity** switch for this repo before running it (should
already be the core-nw/default identity from Phase 2, but re-verify — do
not assume it's still correct):
```bash
gh release create {final_tag} \
  --repo {owner_repo} \
  --target {repo_github_branch} \
  --title {final_tag} \
  --generate-notes \
  --notes-start-tag {base_tag}
```

If `--dry-run`, print the command instead of running it (still run the
tag computation and confirmation — the user should see and approve the
tag even in dry-run, just no actual `gh release create`). Record the
release URL per repo. If creation fails for a repo (e.g. tag already
exists), report it and exclude that repo from Phase 4A — do not stop the
whole run.

After every core-nw repo in scope has been tagged (or excluded), print:
```text
PHASE 3 COMPLETE
Results:
  Releases created: {N}
  Repos excluded: {F}
  (Mesh repos: N/A — no tag/release phase for rdk-gdcs, see Phase 4B)
```

---

## PHASE 4A: Update SRCREV / PKGREV and Push to Gerrit (core-nw, `meta-rdk-broadband`)

**This subphase applies to core-nw (`rdkcentral`) repos only** — see
Phase 4B immediately after it for Mesh repos.

**Script:** `.agents/skills/stable2-srcrev-updater/scripts/resolve_srcrev.py`
(generic: takes `--ref` and repeatable `--repo`; here, call it once per
repo since each repo now has its own distinct tag/ref, not once for all
repos)

### 4A.1 — Resolve each repo's own new tag's SHA

For every core-nw repo tagged in Phase 3, using **that repo's own `final_tag`**:
```bash
python3 .agents/skills/stable2-srcrev-updater/scripts/resolve_srcrev.py \
  --ref {that_repo_final_tag} \
  --repo {that_owner_repo}
```

If a repo is not in the script's built-in SRCREV mapping (see
`specs/srcrev-parsing.md`), report it and skip that repo's `.inc` update.

### 4A.2 — Reuse the ISOLATED meta-rdk-broadband workspace

Already cloned and checked out to `{gerrit_branch}` during "Resolve GitHub
Branch" above, at `.on-demand-cherry-pick/meta-rdk-broadband` (**not**
`.stable2-meta-sync/meta-rdk-broadband`). Just refresh it before editing:
```bash
cd .on-demand-cherry-pick/meta-rdk-broadband
git fetch origin
git checkout {gerrit_branch}
git pull --ff-only origin {gerrit_branch}
```

Gerrit HTTPS credentials come from `~/.netrc` — do not prompt
interactively. This clone is dedicated to this workflow; it is fine for it
to persist between runs.

### 4A.3 — Apply the updates

In `{srcrev_file}` (`conf/include/generic-srcrev.inc`): update only the
`SRCREV_pn-<component>` lines for resolved components to each one's own
new SHA. If a matching `SRCREV_scope_branch_pn-<component>` line exists,
update it to that component's own `final_tag`. Leave every other line
untouched.

In `{pkgrev_file}` (`conf/include/generic-pkgrev.inc`): update only the
entries for resolved components, each to its own `final_tag`.

If `--dry-run`, print the exact line-level changes per component as a
fenced ```diff block (one `-`/`+` line pair per changed `SRCREV_pn-<component>`
/ PKGREV entry — see AGENTS.md) and stop here — do not edit files, commit,
or push.

### 4A.4 — Commit and push directly under the shared topic

This workflow pushes these files itself — `gerrit-cherrypick-squash` is
invoked unmodified in Phase 5 and knows nothing about this change; they
are linked only by sharing `{topic}`:

```bash
git add conf/include/generic-srcrev.inc conf/include/generic-pkgrev.inc
git commit -m "on-demand cherry-pick {tickets joined by comma}: update SRCREV/PKGREV

Reason for change: on-demand cherry-pick of {tickets joined by comma} to {gerrit_branch}
Test Procedure:
1) Build meta-rdk-broadband with the updated SRCREV/PKGREV and confirm each new tag is picked up
Risks: Low
Priority: P2
Signed-off-by: <email>"
GIT_TERMINAL_PROMPT=0 git push https://{gerrit_host}/{gerrit_repo} \
  HEAD:refs/for/{gerrit_branch}%topic={topic}
```

Generate a `Change-Id` the same way `gerrit-cherrypick-squash` does
(Python SHA1 of tree+author+time) if the commit hook doesn't add one.

If nothing actually changed (all resolved SHAs already match what's in
the files), skip the commit/push and report "no srcrev/pkgrev changes
needed" — do not push an empty change.

After this subphase finishes (including the "nothing changed" case), print:
```text
PHASE 4A COMPLETE (core-nw)
Results:
  Components updated: {N}
  Gerrit topic: {topic}
```

---

## PHASE 4B: Update SRCREV and Push to Gerrit (Mesh, `meta-rdk-comcast-broadband`)

**This subphase applies to Mesh (`rdk-gdcs`) repos only.** No tag, no
PKGREV, no `gh release` — only `{mesh_srcrev_file}`'s SRCREV variable, and
only for repos that succeeded in Phase 2 (there is no Phase 3 gate for
Mesh repos to pass through first).

### 4B.1 — Reuse the ISOLATED meta-rdk-comcast-broadband workspace

Already cloned and checked out to `{gerrit_branch}` during "Resolve GitHub
Branch — rdk-gdcs (Mesh) Repos" above, at
`.on-demand-cherry-pick/meta-rdk-comcast-broadband`. Just refresh it
before editing:
```bash
cd .on-demand-cherry-pick/meta-rdk-comcast-broadband
git fetch origin
git checkout {gerrit_branch}
git pull --ff-only origin {gerrit_branch}
```
Gerrit HTTPS credentials come from `~/.netrc` (same host as
meta-rdk-broadband) — unaffected by the GitHub identity switch.

### 4B.2 — Resolve each Mesh repo's new tip SHA

For every Mesh repo that succeeded in Phase 2, the new value is simply
`{repo_github_branch}`'s current tip on GitHub after the cherry-pick —
no separate resolve script needed, since there's no tag to look up:
```bash
git ls-remote https://github.com/{mesh_github_org}/{repo}.git refs/heads/{repo_github_branch}
```
(Apply the GitHub Identity switch to the Mesh identity first if it isn't
already active.) Call this `{new_sha}` for the repo.

### 4B.3 — Apply the update

For each Mesh repo, update only its own `{srcrev_var}` line (identified in
"Resolve GitHub Branch — rdk-gdcs (Mesh) Repos" step 2) in
`{mesh_srcrev_file}` to `{new_sha}`. Leave every other line untouched —
there is no PKGREV-equivalent file to update for Mesh.

If `--dry-run`, print the exact line-level change per component as a
fenced ```diff block and stop here — do not edit files, commit, or push.

### 4B.4 — Commit and push under the same shared topic

```bash
git add {mesh_srcrev_file}
git commit -m "on-demand cherry-pick {tickets joined by comma}: update Mesh SRCREV

Reason for change: on-demand cherry-pick of {tickets joined by comma} to {gerrit_branch}
Test Procedure:
1) Build meta-rdk-comcast-broadband with the updated SRCREV and confirm each new commit is picked up
Risks: Low
Priority: P2
Signed-off-by: <email>"
GIT_TERMINAL_PROMPT=0 git push https://{gerrit_host}/{mesh_gerrit_repo} \
  HEAD:refs/for/{gerrit_branch}%topic={topic}
```

Same `Change-Id` generation rule as 4A.4 if the commit hook doesn't add
one. If nothing changed, skip the commit/push and report "no Mesh SRCREV
changes needed".

After this subphase finishes, print:
```text
PHASE 4B COMPLETE (Mesh)
Results:
  Components updated: {N}
  Gerrit topic: {topic}
```

---

## PHASE 5: Gerrit Cherry-Pick (ticket-linked changes only, all tickets)

**Skill:** `gerrit-cherrypick-squash`, invoked exactly as documented there
— it already accepts multiple ticket keys in one invocation. **Do not**
rely on or trigger its optional "5g" meta-rdk-broadband step; Phase 4A
above already pushed that change directly under the same topic. This
phase is core-nw/Gerrit-only — it has no Mesh equivalent (see Critical
Constraints).

```bash
/gerrit-cherrypick-squash {ticket1} {ticket2} ... --branch {gerrit_branch} --topic {topic} {test_repo_flag} {dry_run_flag}
```

This unmodified skill handles: Jira dependency/subtask traversal across
all given tickets, `rdkjenkins03` MERGED-comment scanning restricted to
`*_sprint`/`develop`, Gerrit-API verification, per-repo cherry-pick with
squash if multiple changes land in the same repo, commit-message field
enforcement, push to `refs/for/{gerrit_branch}`, and setting the topic.
Because Phases 4A/4B already pushed their meta-layer changes under
`{topic}`, Gerrit will group all of them under the same topic without
`gerrit-cherrypick-squash` needing to know about it.

If none of the tickets have any Jira-linked Gerrit changes, that's fine —
the topic then covers just the Phase 4A/4B meta-layer changes (if any). If
Phases 4A/4B also had nothing to push, report that the run produced no
Gerrit changes at all rather than treating it as an error.

After this phase finishes, print:
```text
PHASE 5 COMPLETE
Results:
  Gerrit changes pushed: {N}
  Topic: {topic}
```

---

## FINAL SUMMARY

```
╔═════════════════════════════════════════════════════════════════╗
║                                                                 ║
║         ON-DEMAND CHERRY-PICK COMPLETE                          ║
║                                                                 ║
╚═════════════════════════════════════════════════════════════════╝

Tickets:         {ticket1}, {ticket2}, ...
Mode:            {DRY-RUN / TEST-REPO / PRODUCTION}

Phase Results:
  1. Find PRs            ✓ {M} PRs across {N} repos ({core-nw count} core-nw, {mesh count} Mesh), batched by repo (no shared files written)
  2. GitHub cherry-pick  ✓ {S} repos succeeded, {F} need manual fix (branch resolved per repo from {gerrit_branch} for core-nw, from meta-rdk-comcast-broadband's SRCREV for Mesh)
  3. Tag + release       ✓ {N} releases created (core-nw only — one tag per repo, listed below; N/A for Mesh)
  4A. SRCREV/PKGREV (core-nw) ✓ {N} components updated + pushed to Gerrit under topic {topic}
  4B. SRCREV (Mesh)      ✓ {N} components updated + pushed to Gerrit under topic {topic}
  5. Gerrit cherry-pick   ✓ {N} Gerrit change(s) pushed under topic {topic} (core-nw only)

GitHub releases (core-nw only):
  {repo} → {final_tag} → {release URL}
  ...

Mesh repos cherry-picked (no tag/release):
  {repo} → branch {repo_github_branch} → new tip {new_sha}
  ...

Gerrit changes:
  {list of repo → change number, including the Phase 4A/4B meta-layer changes}

Gerrit topic: https://{gerrit_host}/q/topic:{topic}

GitHub identity: restored to {default_gh_user} (verified via gh auth status)

Workspaces used: .on-demand-cherry-pick/meta-rdk-broadband and
.on-demand-cherry-pick/meta-rdk-comcast-broadband (independent of the
stable2 pipeline's own .stable2-meta-sync workspace — nothing there was
touched)
```

## Verify Completion

Before declaring done, confirm:
- [ ] Both required inputs (tickets, Gerrit branch) were collected before the cherry-pick plan was printed — the topic was never asked for; it was either taken from `--topic` as given or auto-derived as `{tickets joined by _}_{gerrit_branch}`
- [ ] Every repo found was correctly classified as core-nw or Mesh (owner matched against `config/config.yaml`'s vs `config/mesh_components.yaml`'s `github_org`, never hardcoded), and the GitHub identity was switched to match before any `gh`/`git` call touching that repo — verified via `gh auth status`, not assumed
- [ ] The default (core-nw) GitHub identity recorded at the start was restored before the Final Summary was printed, even if the run stopped early or a repo failed
- [ ] GitHub branch for core-nw repos was never asked for — it was resolved per repo from the Gerrit branch's `pkgrev.inc` tag, falling back to asking only when resolution genuinely failed for a specific repo (missing component, tag not on GitHub, or orphan tag unreachable from any branch)
- [ ] GitHub branch for Mesh repos was resolved from `meta-rdk-comcast-broadband`'s SRCREV commit (tip match reused directly, otherwise a new branch created from that commit with a name the user explicitly provided — never auto-derived for Mesh)
- [ ] A tag in an unexpected or non-stable2 format (plain semver, custom suffix, etc.) was never treated as a reason to ask the user — it was resolved from GitHub like any other tag
- [ ] When a tag's commit wasn't at any branch tip, a new branch was created automatically from that commit (or reused if it already existed) instead of asking the user — named `{existing_github_branch}_{gerrit_branch}` if the base was already a `support/*` branch, or `support/{gerrit_branch}` if the base was `develop`/any other non-`support/*` branch
- [ ] When a tag's commit was an exact tip match of an existing branch, that branch was reused directly as `{repo_github_branch}` unchanged — no new `_{gerrit_branch}`-suffixed branch was created for it
- [ ] "No branch points at tag X's tip" never appeared as a standalone question to the user — it's an internal, same-turn transition into the containing-branch check, not a stopping point
- [ ] `develop` (or the repo's default branch) was treated as a normal candidate for `{existing_github_branch}` when it contains the tag — never excluded or special-cased for *finding* it, only for how the new branch is *named*
- [ ] `develop` was never reused directly as `{repo_github_branch}` (even on an exact tip match) and was always preferred over `config.yaml`'s `support_branch` default when both qualified — so the new dedicated `support/{gerrit_branch}` branch, not develop or an unrelated support branch, is what cherry-picks actually land on
- [ ] Before declaring a tag an orphan (unreachable from any branch), the local clone was confirmed to have full, non-shallow history
- [ ] Every ticket's PRs were discovered and merged into one per-repo commit list before cherry-picking (no repo processed twice)
- [ ] Exactly one combined cherry-pick plan was printed and approved before any GitHub change was made
- [ ] Every repo's tag was computed from that repo's own latest stable2-pattern tag — or, if none existed yet, from its latest plain semver tag using today's date — and confirmed individually with the user, with override honored if given
- [ ] A `_v{N}` suffix was incremented as a plain version bump (`_v{N+1}`), never confused with a `_hotfix_v{N}` suffix, which increments separately
- [ ] Any repo with neither a stable2-pattern tag nor a plain semver tag stopped and asked the user directly instead of guessing (core-nw only — Mesh repos never go through Tag Naming at all)
- [ ] Before proposing a computed hotfix tag, it was checked against existing GitHub tags for that repo — if it already existed, the user was asked for a replacement instead of silently reusing/overwriting it or auto-incrementing further
- [ ] No Mesh (`rdk-gdcs`) repo ever reached Phase 3 or got a `gh release create` / tag — only Phase 2 (cherry-pick) and Phase 4B (SRCREV update)
- [ ] Every Mesh repo's `{srcrev_var}` in `{mesh_srcrev_file}` was identified unambiguously before editing — an unclear/ambiguous variable name was never guessed, the user was asked for the exact variable name instead
- [ ] `pr_list.yaml` was never written or overwritten
- [ ] `.stable2-meta-sync/meta-rdk-broadband` was never touched — only `.on-demand-cherry-pick/meta-rdk-broadband` (core-nw) and `.on-demand-cherry-pick/meta-rdk-comcast-broadband` (Mesh) were used
- [ ] No stable2 session-state file was read or written
- [ ] `gerrit-cherrypick-squash` was invoked unmodified, with all tickets in one call, without depending on its meta-rdk-broadband step
- [ ] Phase 4A's push, Phase 4B's push, and Phase 5's push(es) all share the exact same `{topic}`
- [ ] Multiple Gerrit changes to the same repo were squashed into one
- [ ] Final summary with all artifact links was printed, including the restored GitHub identity
