---
name: on-demand-cherry-pick
description: "On-demand cherry-pick for one or more Jira tickets to any target branch. Finds merged GitHub PRs to develop for all given tickets, cherry-picks them (batched per repo) to a GitHub branch resolved automatically per repo from the user-supplied Gerrit branch (via that repo's tag in meta-rdk-broadband's pkgrev.inc), computes a hotfix tag per repo from that repo's own latest stable2 tag, updates SRCREV/PKGREV in meta-rdk-broadband for the same Gerrit branch, and cherry-picks/squashes any Jira-linked Gerrit changes for all tickets to that same Gerrit branch — everything tagged/pushed once per repo, all under one shared Gerrit topic. Fully independent of the stable2 pipeline: uses its own workspace and its own artifact files, never the shared ones. Supports --dry-run and --test-repo."
mode: primary
license: Comcast
argument-hint: "Required: '--tickets <KEY[,KEY...]>' one or more Jira tickets (comma-separated), '--gerrit-branch <name>' target Gerrit branch, '--topic <name>' shared Gerrit topic. GitHub branch is NOT a user input — it's resolved automatically per repo from the Gerrit branch (see 'Resolve GitHub Branch' below). Tag names are computed per repo during the run and confirmed/overridden interactively — not provided upfront. Optional: '--dry-run', '--test-repo', '--gerrit-host <host>', '--gerrit-repo <path>' to override the matching config.yaml values for this run only."
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
- Main tag: `2.9.1` (plain semver — irrelevant here, that's main-tagging's
  concern, not this workflow's)
- Stable2 tag: `2.3.0_stable2_20260916` (`{base}_stable2_{YYYYMMDD}`)
- On-demand hotfix tag: `2.3.0_stable2_20260617_hotfix_v3`
  (`{stable2_tag}_hotfix_v{N}`)

For **each repo** that received new commits in Phase 2, compute its tag
like this:

1. Find the latest tag reachable from `{repo_github_branch}` that matches
   **specifically** the stable2 naming pattern:
   ```bash
   git tag --merged origin/{repo_github_branch} --sort=-creatordate \
     | grep -E '^[0-9]+\.[0-9]+\.[0-9]+_stable2_[0-9]{8}(_hotfix_v[0-9]+)?$' \
     | head -1
   ```
   This matches both a plain stable2 tag and an already-hotfixed one.
   **Ignore plain semver tags like `2.9.1` even if they are more recent** —
   they are not a valid base for this workflow.
2. **If no such tag is found for this repo:** do not guess, do not fall
   back to any other tag. Stop for this repo specifically and ask the
   user directly:
   ```
   No stable2-pattern tag found on {repo_github_branch} for {repo}.
   What tag would you like to use for this repo?
   ```
   Use whatever they type, verbatim, as this repo's tag (skip the
   hotfix-increment logic below for this repo since there is no base to
   increment from).
3. **If found and it has no `_hotfix_v` suffix** (a plain stable2 tag):
   `computed_tag = "{found_tag}_hotfix_v1"`
4. **If found and it already ends in `_hotfix_v{N}`**: keep everything
   before that suffix unchanged and increment the number:
   `computed_tag = "{everything_before}_hotfix_v{N+1}"`
   (e.g. `..._hotfix_v3` → `..._hotfix_v4`)
5. **Always confirm per repo before using it**, one repo at a time:
   ```
   {repo}: latest stable2 tag is {found_tag} → proposed new tag {computed_tag}
   Use this tag? [Y/n]
   ```
   If `Y`, use `computed_tag`. If `n`, ask
   `Enter the tag to use for {repo}:` and use whatever they type instead.
   This is a genuine per-repo decision point, not a "proceed to next
   phase?" gate — ask for every repo, even if there are several.
6. **Extract `{base_tag}`** — the plain `X.Y.Z` semver prefix — from the final
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
  step (its "5g").** This workflow pushes its own srcrev/pkgrev change
  directly in Phase 4 instead, and simply reuses the same `{topic}` string
  so Gerrit groups both under one topic — no modification to
  `gerrit-cherrypick-squash` is needed or wanted.
- **Credentials are unchanged**: Gerrit HTTPS auth still comes from
  `~/.netrc` (Gerrit entry only — do not rely on any `github.com` entries
  that might also be present there, they can shadow `gh`'s own credential
  helper with the wrong identity), GitHub operations still use the
  already-authenticated `gh` CLI, Jira lookups still go through
  `scripts/jira_rest.py` (credentials from `ccp_jira.env`, not MCP).

## Resolve GitHub Branch (per repo, from the Gerrit branch)

The user never names a GitHub branch directly — it's derived, once per
repo, from the same `{gerrit_branch}` they already gave for the meta-layer
change. This runs once, for every repo in Phase 1's merged commit list,
before the cherry-pick plan is printed (see "Before Starting" step 4).

1. **Prepare the isolated meta-rdk-broadband clone now** (the same clone
   Phase 4 reuses later — see Isolation above for why it's dedicated):
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
         - **Exactly one match:** the tag is already at the tip — that
           branch is `{existing_github_branch}`. Continue to step 7 to
           compute `{repo_github_branch}` from it; a tip match is never
           reused directly as `{repo_github_branch}` itself — this run
           still gets its own dedicated branch, named per step 7's
           convention, same as every other case.
         - **More than one match:** prefer whichever matching branch name
           equals `support_branch` from `config/config.yaml` if it's
           among them as `{existing_github_branch}`; otherwise list the
           matches and ask the user which one to treat as
           `{existing_github_branch}`. Either way, continue to step 7
           next — never skip straight to step 9.
         - **No match:** do not ask anything yet. Continue immediately to
           step 2 below — the tag has simply moved past every branch
           tip, which is the common case for older/placeholder tags like
           `1.0.0`, and is resolved automatically the same way regardless
           of whether the tag looks like a stable2 tag or not.
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
      5. **More than one candidate branch:** prefer whichever equals
         `support_branch` from `config/config.yaml` if it's among them;
         otherwise list the candidates and ask the user which one to
         treat as `{existing_github_branch}` — this is picking from a
         real list of candidates, not inventing a branch name. Either
         way, continue to step 7 next.
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
      7. Compute the new branch name — the naming convention depends on
         whether `{existing_github_branch}` already follows the
         `support/*` convention:
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

## Critical Constraints — Read First

- **Three required inputs**: one or more Jira tickets (comma-separated),
  Gerrit branch, Gerrit topic. GitHub branch is NOT a user input — it's
  resolved automatically per repo (see Resolve GitHub Branch above). Tag
  names are also NOT a required input — they are computed per repo during
  the run (see Tag Naming) and confirmed interactively. If any of the
  three are missing from the invocation, ask for them before doing
  anything else.
- **Batch by repo, not by ticket.** If two tickets both touch
  `rdkcentral/utopia`, cherry-pick both tickets' commits to `utopia` in
  one pass, create exactly one new tag for `utopia`, do exactly one
  SRCREV/PKGREV update for `utopia` — never process the same repo twice
  because it appeared under two tickets.
- **GitHub branch is resolved per repo, not shared.** Unlike the single
  `{gerrit_branch}`, two different repos touched by the same run can
  legitimately resolve to two different GitHub branches — never assume
  they match each other or the Gerrit branch name.
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
- **Phase ordering still matters**: a repo only gets tagged if it
  succeeded in Phase 2; a repo only gets a srcrev/pkgrev update if it was
  tagged in Phase 3. Report — do not silently drop — any repo that drops
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
- **Do not invent a topic.** It comes from the user. If not provided, ask
  for it explicitly — do not derive it automatically.
- **When in doubt about anything not explicitly covered here, ask the
  user with concrete options rather than deciding unilaterally.**

## Before Starting

1. **Read config:** Load `config/config.yaml` (path is relative to the
   project root, not this agent file) as the **default** for `gerrit_host`,
   `gerrit_repo`, `github_org`, `srcrev_file`, `pkgrev_file`. If the user
   passed `--gerrit-host <host>` or `--gerrit-repo <path>`, use those
   instead for this run only. Print the resolved values before proceeding.
   Do **not** read or use `meta_sync_workspace` — this workflow uses its
   own fixed workspace path (see Isolation above).
2. **Parse arguments.** Expect `--tickets`, `--gerrit-branch`, `--topic`,
   plus optional `--dry-run` / `--test-repo`. `--tickets` may be a single
   key or a comma-separated list. For any of the three required values
   missing from the invocation, ask for it directly, one at a time:
   ```
   Enter one or more Jira ticket keys (comma-separated if more than one):
   Enter the target Gerrit branch (GitHub branch is resolved automatically from this):
   Enter the Gerrit topic to use for this change set:
   ```
   Empty input is not allowed for any of these — keep asking. Do **not**
   ask for a GitHub branch — see Resolve GitHub Branch above.
3. **Run discovery for the plan** (read-only, no confirmation needed yet):
   - For **each** ticket, run `jira-pr-lookup`'s traversal, then merge the
     resulting PR/commit lists by repo, deduplicating by commit SHA (the
     same commit could surface from two tickets if they're linked)
   - Run `gerrit-cherrypick-squash`'s discovery (dependency traversal +
     `rdkjenkins03` MERGED-comment scan + Gerrit verify) across **all**
     given tickets at once — it already supports multiple ticket keys
4. **Resolve each repo's GitHub branch** per the Resolve GitHub Branch
   section above, using the merged repo list from step 3.
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
Gerrit topic:    {topic}
Mode:            {DRY-RUN / TEST-REPO / PRODUCTION}

─────────────────────────────────────────────────────────────────
GITHUB CHERRY-PICK (GitHub branch resolved per repo from {gerrit_branch}'s pkgrev.inc)
   rdkcentral/utopia    → branch {repo_github_branch} (tag {repo_tag})   2 commit(s): PR#101 [{ticket1}], PR#102 [{ticket2}]
   rdkcentral/ccsp-wifi → branch {repo_github_branch} (tag {repo_tag})   1 commit(s): PR#123 [{ticket1}]

GERRIT — {tickets joined}'s linked Gerrit changes → {gerrit_branch}
   {project}: change {NNNNN}
   (or: "No Jira-linked Gerrit changes found for these tickets")
─────────────────────────────────────────────────────────────────

Note: tag names for each repo above will be computed from that repo's own
latest stable2 tag and confirmed with you individually once cherry-picking
succeeds — not decided now.

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
Starting" step 3, and the per-repo resolved GitHub branches from step 4.
Do not re-run either. If discovery produced zero PRs across all tickets,
note that clearly and skip Phases 2–3 for the GitHub side, proceeding
straight to Phase 4/5 for the Gerrit side only (tickets can be
Gerrit-only).

Per the Isolation section above: never let this step write `pr_list.yaml`.

---

## PHASE 2: GitHub Cherry-Pick (per repo, to its own resolved branch)

**Script:** `scripts/cherry_pick_to_stable2.py` (generic despite the name —
it takes an explicit `--branch`, it is not stable2-specific)

For each repo in the merged commit list, using **that repo's own
`{repo_github_branch}`** resolved in "Resolve GitHub Branch" above:
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

Print the script's own final report. Only repos with at least one
succeeded commit and no `repo_error` carry into Phase 3 — report, do not
silently drop, any repo that failed.

**If a cherry-pick conflict cannot be auto-resolved**, report the repo,
commit, and files, and ask specifically about that repo (`Skip {repo} and
continue with the rest? [Y/n]`) rather than aborting the whole run.

If zero repos succeeded, skip Phases 3–4 and go straight to Phase 5
(Gerrit-only).

---

## PHASE 3: Compute Tag + GitHub Release (per repo)

For each repo that succeeded in Phase 2, follow **Tag Naming** above in
full: detect the latest stable2-pattern tag reachable from **that repo's
own `{repo_github_branch}`** (or stop and ask if none exists for that
repo), compute the hotfix tag, confirm it with the user (or take their
override) — one repo at a time.

Once the final tag is confirmed for a repo, create the release with the changelog
spanning from `{base_tag}` (extracted in Tag Naming step 6 above) to the new tag —
if `{base_tag}` couldn't be extracted for this repo, omit `--notes-start-tag` and
let `--generate-notes` fall back to its own default (previous tag):
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
exists), report it and exclude that repo from Phase 4 — do not stop the
whole run.

---

## PHASE 4: Update SRCREV / PKGREV and Push to Gerrit

**Script:** `.agents/skills/stable2-srcrev-updater/scripts/resolve_srcrev.py`
(generic: takes `--ref` and repeatable `--repo`; here, call it once per
repo since each repo now has its own distinct tag/ref, not once for all
repos)

### 4.1 — Resolve each repo's own new tag's SHA

For every repo tagged in Phase 3, using **that repo's own `final_tag`**:
```bash
python3 .agents/skills/stable2-srcrev-updater/scripts/resolve_srcrev.py \
  --ref {that_repo_final_tag} \
  --repo {that_owner_repo}
```

If a repo is not in the script's built-in SRCREV mapping (see
`specs/srcrev-parsing.md`), report it and skip that repo's `.inc` update.

### 4.2 — Reuse the ISOLATED meta-rdk-broadband workspace

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

### 4.3 — Apply the updates

In `{srcrev_file}` (`conf/include/generic-srcrev.inc`): update only the
`SRCREV_pn-<component>` lines for resolved components to each one's own
new SHA. If a matching `SRCREV_scope_branch_pn-<component>` line exists,
update it to that component's own `final_tag`. Leave every other line
untouched.

In `{pkgrev_file}` (`conf/include/generic-pkgrev.inc`): update only the
entries for resolved components, each to its own `final_tag`.

If `--dry-run`, print the exact line-level changes per component and stop
here — do not edit files, commit, or push.

### 4.4 — Commit and push directly under the shared topic

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

---

## PHASE 5: Gerrit Cherry-Pick (ticket-linked changes only, all tickets)

**Skill:** `gerrit-cherrypick-squash`, invoked exactly as documented there
— it already accepts multiple ticket keys in one invocation. **Do not**
rely on or trigger its optional "5g" meta-rdk-broadband step; Phase 4
above already pushed that change directly under the same topic.

```bash
/gerrit-cherrypick-squash {ticket1} {ticket2} ... --branch {gerrit_branch} --topic {topic} {test_repo_flag} {dry_run_flag}
```

This unmodified skill handles: Jira dependency/subtask traversal across
all given tickets, `rdkjenkins03` MERGED-comment scanning restricted to
`*_sprint`/`develop`, Gerrit-API verification, per-repo cherry-pick with
squash if multiple changes land in the same repo, commit-message field
enforcement, push to `refs/for/{gerrit_branch}`, and setting the topic.
Because Phase 4 already pushed the meta-layer change under `{topic}`,
Gerrit will group both under the same topic without `gerrit-cherrypick-squash`
needing to know about it.

If none of the tickets have any Jira-linked Gerrit changes, that's fine —
the topic then covers just the Phase 4 meta-layer change (if any). If
Phase 4 also had nothing to push, report that the run produced no Gerrit
changes at all rather than treating it as an error.

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
  1. Find PRs            ✓ {M} PRs across {N} repos, batched by repo (no shared files written)
  2. GitHub cherry-pick  ✓ {S} repos succeeded, {F} need manual fix (branch resolved per repo from {gerrit_branch})
  3. Tag + release       ✓ {N} releases created (one tag per repo, listed below)
  4. SRCREV/PKGREV        ✓ {N} components updated + pushed to Gerrit under topic {topic}
  5. Gerrit cherry-pick   ✓ {N} Gerrit change(s) pushed under topic {topic}

GitHub releases:
  {repo} → {final_tag} → {release URL}
  ...

Gerrit changes:
  {list of repo → change number, including the Phase 4 meta-layer change}

Gerrit topic: https://{gerrit_host}/q/topic:{topic}

Workspace used: .on-demand-cherry-pick/meta-rdk-broadband (independent of
the stable2 pipeline's own .stable2-meta-sync workspace — nothing there
was touched)
```

## Verify Completion

Before declaring done, confirm:
- [ ] All three required inputs were collected before the cherry-pick plan was printed
- [ ] GitHub branch was never asked for — it was resolved per repo from the Gerrit branch's `pkgrev.inc` tag, falling back to asking only when resolution genuinely failed for a specific repo (missing component, tag not on GitHub, or orphan tag unreachable from any branch)
- [ ] A tag in an unexpected or non-stable2 format (plain semver, custom suffix, etc.) was never treated as a reason to ask the user — it was resolved from GitHub like any other tag
- [ ] When a tag's commit wasn't at any branch tip, a new branch was created automatically from that commit (or reused if it already existed) instead of asking the user — named `{existing_github_branch}_{gerrit_branch}` if the base was already a `support/*` branch, or `support/{gerrit_branch}` if the base was `develop`/any other non-`support/*` branch
- [ ] "No branch points at tag X's tip" never appeared as a standalone question to the user — it's an internal, same-turn transition into the containing-branch check, not a stopping point
- [ ] `develop` (or the repo's default branch) was treated as a normal candidate for `{existing_github_branch}` when it contains the tag — never excluded or special-cased for *finding* it, only for how the new branch is *named*
- [ ] Before declaring a tag an orphan (unreachable from any branch), the local clone was confirmed to have full, non-shallow history
- [ ] Every ticket's PRs were discovered and merged into one per-repo commit list before cherry-picking (no repo processed twice)
- [ ] Exactly one combined cherry-pick plan was printed and approved before any GitHub change was made
- [ ] Every repo's tag was computed from that repo's own latest stable2-pattern tag (never a plain main tag, never assumed) and confirmed individually with the user, with override honored if given
- [ ] Any repo with no stable2-pattern tag stopped and asked the user directly instead of guessing
- [ ] `pr_list.yaml` was never written or overwritten
- [ ] `.stable2-meta-sync/meta-rdk-broadband` was never touched — only `.on-demand-cherry-pick/meta-rdk-broadband` was used
- [ ] No stable2 session-state file was read or written
- [ ] `gerrit-cherrypick-squash` was invoked unmodified, with all tickets in one call, without depending on its meta-rdk-broadband step
- [ ] The Phase 4 push and Phase 5 pushes share the exact same `{topic}`
- [ ] Multiple Gerrit changes to the same repo were squashed into one
- [ ] Final summary with all artifact links was printed
