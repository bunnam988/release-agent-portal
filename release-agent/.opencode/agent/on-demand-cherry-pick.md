---
name: on-demand-cherry-pick
description: "On-demand cherry-pick for one or more Jira tickets to any target branch. Finds merged GitHub PRs to develop for all given tickets, cherry-picks them (batched per repo) to a user-supplied GitHub branch, computes a hotfix tag per repo from that repo's own latest stable2 tag, updates SRCREV/PKGREV in meta-rdk-broadband for a user-supplied Gerrit branch, and cherry-picks/squashes any Jira-linked Gerrit changes for all tickets to that same Gerrit branch — everything tagged/pushed once per repo, all under one shared Gerrit topic. Fully independent of the stable2 pipeline: uses its own workspace and its own artifact files, never the shared ones. Supports --dry-run and --test-repo."
mode: primary
license: Comcast
argument-hint: "Required: '--tickets <KEY[,KEY...]>' one or more Jira tickets (comma-separated), '--github-branch <name>' target GitHub branch, '--gerrit-branch <name>' target Gerrit branch, '--topic <name>' shared Gerrit topic. Tag names are computed per repo during the run and confirmed/overridden interactively — not provided upfront. Optional: '--dry-run', '--test-repo', '--gerrit-host <host>', '--gerrit-repo <path>' to override the matching config.yaml values for this run only."
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
2. Cherry-pick all of those commits, batched per repo, to a **GitHub
   branch the user names**
3. For each repo that got new commits, **compute** a hotfix tag from that
   repo's own latest stable2 tag (see Tag Naming below) — confirmed or
   overridden by the user per repo, never assumed
4. Resolve each repo's new tag's SHA, update `generic-srcrev.inc` /
   `generic-pkgrev.inc` in a **dedicated, isolated** local
   `meta-rdk-broadband` clone on a **Gerrit branch the user names** (can
   differ from the GitHub branch), and push that one combined change to
   Gerrit directly
5. Find any Jira-linked Gerrit changes for **all** given tickets (reuses
   `gerrit-cherrypick-squash`'s dependency traversal + `rdkjenkins03`
   MERGED-comment scan, invoked completely unmodified — it already
   accepts multiple ticket keys) and cherry-pick/squash them onto that
   same Gerrit branch, under the **same shared topic** as the Phase 4 push

## Tag Naming — compute, never assume

Real examples of what exists on these branches today:
- Main tag: `2.9.1` (plain semver — irrelevant here, that's main-tagging's
  concern, not this workflow's)
- Stable2 tag: `2.3.0_stable2_20260916` (`{base}_stable2_{YYYYMMDD}`)
- On-demand hotfix tag: `2.3.0_stable2_20260617_hotfix_v3`
  (`{stable2_tag}_hotfix_v{N}`)

For **each repo** that received new commits in Phase 2, compute its tag
like this:

1. Find the latest tag reachable from `{github_branch}` that matches
   **specifically** the stable2 naming pattern:
   ```bash
   git tag --merged origin/{github_branch} --sort=-creatordate \
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
   No stable2-pattern tag found on {github_branch} for {repo}.
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
  clone** at `.on-demand-cherry-pick/meta-rdk-broadband` instead.
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

## Critical Constraints — Read First

- **Four required inputs**: one or more Jira tickets (comma-separated),
  GitHub branch, Gerrit branch, Gerrit topic. Tag names are NOT a required
  input — they are computed per repo during the run (see Tag Naming) and
  confirmed interactively. If any of the four are missing from the
  invocation, ask for them before doing anything else.
- **Batch by repo, not by ticket.** If two tickets both touch
  `rdkcentral/utopia`, cherry-pick both tickets' commits to `utopia` in
  one pass, create exactly one new tag for `utopia`, do exactly one
  SRCREV/PKGREV update for `utopia` — never process the same repo twice
  because it appeared under two tickets.
- **GitHub branch and Gerrit branch are independent.** Never assume they
  are the same value even if the user's naming looks similar.
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
2. **Parse arguments.** Expect `--tickets`, `--github-branch`,
   `--gerrit-branch`, `--topic`, plus optional `--dry-run` / `--test-repo`.
   `--tickets` may be a single key or a comma-separated list. For any of
   the four required values missing from the invocation, ask for it
   directly, one at a time:
   ```
   Enter one or more Jira ticket keys (comma-separated if more than one):
   Enter the target GitHub branch to cherry-pick to:
   Enter the target Gerrit branch (can differ from the GitHub branch):
   Enter the Gerrit topic to use for this change set:
   ```
   Empty input is not allowed for any of these — keep asking.
3. **Run discovery for the plan** (read-only, no confirmation needed yet):
   - For **each** ticket, run `jira-pr-lookup`'s traversal, then merge the
     resulting PR/commit lists by repo, deduplicating by commit SHA (the
     same commit could surface from two tickets if they're linked)
   - Run `gerrit-cherrypick-squash`'s discovery (dependency traversal +
     `rdkjenkins03` MERGED-comment scan + Gerrit verify) across **all**
     given tickets at once — it already supports multiple ticket keys
4. **Print ONE combined plan covering the cherry-pick scope, then ask
   once** (tag names are NOT decided yet — that happens per repo in Phase
   3):

```
╔═════════════════════════════════════════════════════════════════╗
║                                                                 ║
║         ON-DEMAND CHERRY-PICK — CHERRY-PICK PLAN                ║
║                                                                 ║
╚═════════════════════════════════════════════════════════════════╝

Tickets:         {ticket1}, {ticket2}, ...
GitHub branch:   {github_branch}
Gerrit branch:   {gerrit_branch}
Gerrit topic:    {topic}
Mode:            {DRY-RUN / TEST-REPO / PRODUCTION}

─────────────────────────────────────────────────────────────────
GITHUB CHERRY-PICK → {github_branch}  (batched per repo across all tickets)
   rdkcentral/utopia         2 commit(s): PR#101 [{ticket1}], PR#102 [{ticket2}]
   rdkcentral/ccsp-wifi      1 commit(s): PR#123 [{ticket1}]

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
Starting" step 3. Do not re-run it. If it produced zero PRs across all
tickets, note that clearly and skip Phases 2–3 for the GitHub side,
proceeding straight to Phase 4/5 for the Gerrit side only (tickets can be
Gerrit-only).

Per the Isolation section above: never let this step write `pr_list.yaml`.

---

## PHASE 2: GitHub Cherry-Pick to `{github_branch}`

**Script:** `scripts/cherry_pick_to_stable2.py` (generic despite the name —
it takes an explicit `--branch`, it is not stable2-specific)

For each repo in the merged commit list:
```bash
python3 scripts/cherry_pick_to_stable2.py \
  --repo {owner_repo} \
  --commits {sha1,sha2,...} \
  --branch {github_branch} \
  {dry_run_flag}
```

This bypasses `pr_list.yaml`/`stable2_status_analysis.yaml` READY-gating
entirely (manual `--repo`/`--commits` mode). It already handles cloning,
creating/checking out `{github_branch}`, cherry-picking with `-x`,
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
full: detect the latest stable2-pattern tag (or stop and ask if none
exists for that repo), compute the hotfix tag, confirm it with the user
(or take their override) — one repo at a time.

Once the final tag is confirmed for a repo, create the release with the changelog
spanning from `{base_tag}` (extracted in Tag Naming step 6 above) to the new tag —
if `{base_tag}` couldn't be extracted for this repo, omit `--notes-start-tag` and
let `--generate-notes` fall back to its own default (previous tag):
```bash
gh release create {final_tag} \
  --repo {owner_repo} \
  --target {github_branch} \
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

### 4.2 — Prepare the ISOLATED meta-rdk-broadband workspace

Use `.on-demand-cherry-pick/meta-rdk-broadband` — **not**
`.stable2-meta-sync/meta-rdk-broadband`. Create the parent directory if
needed.

If the clone does not exist:
```bash
git clone https://{gerrit_host}/{gerrit_repo} .on-demand-cherry-pick/meta-rdk-broadband
cd .on-demand-cherry-pick/meta-rdk-broadband
git fetch origin
git checkout {gerrit_branch} 2>/dev/null || git checkout -b {gerrit_branch} origin/{gerrit_branch} 2>/dev/null
```
If `{gerrit_branch}` does not exist yet on Gerrit, create it via the
Gerrit REST API (same approach as `gerrit-cherrypick-squash` step 5a) from
the clone's current HEAD, then check it out.

If the clone already exists (from a previous on-demand run):
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
  1. Find PRs           ✓ {M} PRs across {N} repos, batched by repo (no shared files written)
  2. GitHub cherry-pick  ✓ {S} repos succeeded, {F} need manual fix
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
- [ ] All four required inputs were collected before the cherry-pick plan was printed
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
