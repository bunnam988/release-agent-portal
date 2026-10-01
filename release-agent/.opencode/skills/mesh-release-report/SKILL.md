---
name: mesh-release-report
description: "Use when the user wants a read-only report on unreleased commits across Mesh Components repos (rdk-gdcs org) -- finding commits present in develop but not in main, mapping them to Jira IDs, checking Jira ticket status, and identifying which commits should be reverted -- without taking any action (no file writes, no PRs, no pipeline runs). Also used internally by mesh-release-pipeline to gather this same information before it acts. Don't use for the 41 core-nw component repos (rdkcentral org) -- see main-tagging/stable2 skills for those; Mesh Components release differently (PR-based to main, not direct git-flow tagging)."
license: Comcast
argument-hint: "Optional: a specific set of repos (name, URL, or org/repo) to narrow scope instead of the full 15-repo Mesh Components list"
metadata:
  author: ported from github-release-scripts (Mesh Maintainers team), rewritten onto this repo's gh/jira_rest.py conventions
  source: local
---

# Skill: Mesh Release Report

Produces a read-only release tracking report: which commits are in `develop` but not yet in `main` across Mesh Components repos, their Jira ticket status, and which commits should be reverted due to non-approved tickets. **This skill never writes files, never creates branches/PRs/tags, and never runs scripts that mutate anything.** It only reads from GitHub and Jira and reports findings in chat.

This skill can be invoked directly by a user for informational purposes, or invoked by the `mesh-release-pipeline` orchestrator as a delegated data-gathering step.

Ported from the original `github-release-tracker` skill (a standalone toolkit the Mesh Maintainers team already had) — rewritten onto this repo's own conventions: `gh` CLI instead of GitHub MCP, `scripts/jira_rest.py` instead of Jira MCP. Functionally equivalent to the original; only the underlying tool calls changed.

---

## Critical Constraints — Read First

- **GitHub identity**: Mesh Components repos live under `rdk-gdcs`, a different GitHub org than this repo's usual `rdkcentral` core-nw work, requiring a different account. See Step 0 and Step 8 below — before any `gh`/git command, switch to the mesh identity (`$GH_MESH_USER`); before finishing (success, failure, or early stop), switch back to whichever account was active beforehand. **Never leave the mesh identity active when this skill finishes** — every other skill in this repo assumes the default (core-nw) identity is active.
  - This is a real, known limitation (see DESIGN.md "Mesh Components release support"): if this skill is interrupted before its own cleanup step, the wrong identity could be left active for whatever runs next. Not a reason to skip the restore step — always attempt it, including on error paths.
- Use `gh` CLI for all GitHub reads (commits, branches, tags, PR search) — never raw GitHub API tokens, never a different auth mechanism.
- **`jira_rest.py`'s exact, only location is `/workspace/scripts/jira_rest.py`** (the top-level `scripts/` directory shared by every skill in this repo — main-tagging, track-for-stable2, etc. all use this exact same file). It does **not** live inside this skill's own folder (`.opencode/skills/mesh-release-report/`), and this skill has no `scripts/` subdirectory of its own. Run it as `python3 scripts/jira_rest.py ...` from `/workspace` (the working directory every skill in this repo already runs from) — do not go looking for it anywhere else first. Confirmed as a real mistake during testing: a run once checked for it inside this skill's own folder, found nothing there (correctly — it was never there), and wrongly concluded the script was "missing," stopping to ask the user how to proceed over nothing. **Do not pre-emptively verify this or any other file's existence before using it** — the workflow steps below already tell you exactly what to run and where; just run it, and handle a real failure (a non-zero exit / actual error from the command) if and when one actually happens.
- Use `python3 scripts/jira_rest.py` (the shared Jira REST helper, credentials from `ccp_jira.env`) for every Jira read. Do NOT call Jira MCP tools, raw `curl`, or read `JIRA_TOKEN`/etc. directly. Jira credentials are the *same* service account already used for core-nw work — no identity switch needed for Jira, only for GitHub.
- If `ccp_jira.env` is missing or `jira_rest.py` reports a credential error, STOP and tell the user exactly that rather than falling back to any other Jira access method.
- This skill is **read-only** toward GitHub and Jira. It performs no writes of any kind: no `git push`, no `gh pr create`, no `gh release create`, no branch/tag creation, no Jira label/ticket writes, no running `mesh-release-pipeline.sh`. If any step appears to require a write, STOP and tell the user this skill cannot perform it — direct them to `mesh-release-pipeline` instead.

---

## Autonomous Operation (within its read-only scope)

This skill runs **fully autonomously with zero user confirmation**, because it never takes an action with side effects — there is nothing to confirm.

- Steps 1–7 run with zero user confirmation.
- Markdown tables shown along the way are for visibility only — never a confirmation checkpoint.
- **Exceptions are a closed list — nothing else qualifies:**
  - Step 1/2: the GitHub org is ambiguous for a user-provided repo, or a specific repository returns 404 and cannot be found.
  - Step 7 (Jira lookups): a Jira ticket cannot be fetched at all (401/404/wrong instance) — reported as a warning, not a question.
  - The agent must not reason its way into a new exception. If a situation is not on this list, proceed and report what was found as information, not as a question.

## When invoked by `mesh-release-pipeline`

If invoked as a delegated step, skip any introductory "read-only reporting only" framing toward the user and simply produce all outputs from Steps 1–7, returning to the caller:

- The final release tracking table (Step 6, with Jira columns added in Step 7)
- The Jira status table (Step 7)
- The definitive "Commits flagged for revert" list (Step 7) — the caller decides what to do with it; this skill never decides to revert anything itself.

---

## Component List

### Default list

Read `/workspace/config/mesh_components.yaml` for the `github_org` (`rdk-gdcs`) and the full repo list — do not hardcode the list here, so there is exactly one place (that file) to update if a repo is added.

### User-provided list

If the user explicitly provides a set of repositories (name, URL, or org/repo path), use **only** those — do not filter against the default list. If the org isn't obvious from the input, ask.

Output table column headers (preserve exactly):
`S.No | Component | Commit Date | Commit ID | Jira ID | GitHub PR develop | Component Release Version | GitHub PR main | Component Release Commit Hash`

**CRITICAL — Context preservation rule:** The Step 6 output table must NEVER be compressed or summarized. Exclude it from any context compression — it's the primary deliverable and must stay in full, uncompressed form for the rest of the session.

---

## Workflow

**Do not delegate this skill's invocation itself to a subagent/task.** Only
Step 3's per-repo commit-diffing may be parallelized across subagents (one
repo per subagent, as that step says) — Steps 0, 1, 2, and 7-8 must run in
the main conversation thread directly, not inside a spawned task, so that a
Step 0 stop (missing `GH_MESH_USER`, failed identity switch) is reported to
the user directly and immediately, not silently absorbed into a subagent's
own final summary.

### Step 0 — Switch to the mesh GitHub identity

1. **Check `$GH_MESH_USER` is actually set and non-empty first** (e.g. `echo "${GH_MESH_USER:-}"`, or check the value directly). If it's empty/unset, STOP immediately and tell the user: "GH_MESH_USER is not configured — the Mesh Components GitHub identity isn't set up yet. This must be fixed before this skill can run." Do NOT attempt a `gh auth switch` with an empty value — `gh auth switch --user ""` can silently "succeed" by leaving whichever account was already active unchanged (confirmed in testing), which would silently run every subsequent GitHub operation under the *wrong* identity instead of failing loudly.
2. Run `gh auth status --hostname github.com` and note which account currently shows `Active account: true` — this is what Step 8 must restore. If none is active or the command errors, note that explicitly (Step 8 then has nothing to restore).
3. Run `gh auth switch --hostname github.com --user "$GH_MESH_USER"`.
4. Confirm the switch worked by running `gh auth status --hostname github.com` again and checking that the account name shown with `Active account: true` **exactly matches** the `$GH_MESH_USER` value from Step 0.1 — not just that the command exited successfully. If it doesn't match, STOP and tell the user the mesh GitHub identity isn't authenticated — do not proceed with any GitHub operation under the wrong identity.

### Step 1 — Determine the component list and org

- If the user explicitly provided repos, use those; determine the org from their input (ask if ambiguous).
- Otherwise, read `/workspace/config/mesh_components.yaml` for the default list and `rdk-gdcs` org.

### Step 2 — Derive repository URLs

- Each component maps to `https://github.com/<org>/<component-name>`.
- Only ask the user if a specific repository returns 404 and cannot be found.

### Step 3 — Find commits in `develop` not in `main` (per repo)

**MANDATORY, non-optional: delegate this entire step to one subagent per repository, via the Task tool — never fetch more than one repository's data in the main conversation thread or in a single subagent.** This is not a performance nicety; skipping it has caused a real context-length failure during testing (a 15-repo run without per-repo delegation produced 256k tokens against a 128k model limit and hard-failed with nothing to show for it). The "don't delegate this skill's invocation" rule earlier in this file is about *not* handing off the whole skill (Steps 0-2, 7-8 must run in the main thread) — it does not apply to this step, which must always be split one-subagent-per-repo.

For each repository, in its own subagent:
- **Always use `--jq` to extract only the fields actually needed — never fetch full raw commit objects.** Full objects (author/committer metadata, verification signatures, parent SHAs, URLs) are the direct cause of the context-length failure above; filtered output is a small fraction of the size for the same information.
  ```
  gh api "repos/<org>/<repo>/commits?sha=develop&per_page=15" --jq '.[] | {sha, date: .commit.author.date, message: .commit.message}'
  ```
  Same for `main`. Collect SHAs present in `develop`'s filtered list but absent from `main`'s.
- **Verification step (critical):** also fetch each branch's tip commit directly, filtered the same way (`gh api repos/<org>/<repo>/branches/develop --jq '{sha: .commit.sha, date: .commit.commit.author.date}'`, same for `main`). If the tips are the same date or `main` is ahead, treat the branches as in sync — discard any apparent SHA differences as pre-migration history artifacts.
- Return only the small, filtered per-repo result to the parent (a handful of SHA/date/message triples, not raw API responses) — the parent thread aggregates these across all repos for Step 4 onward.

### Step 4 — For each differing commit, extract fields

- **Commit Date:** `commit.author.date` (`YYYY-MM-DD`).
- **Commit ID:** first 12 characters of the SHA.
- **Jira ID:** parse from the commit message using the generic pattern `<PROJECT-KEY>-<NUMBER>` (e.g. `RDKB-NNNNN`, `LTE-NNNN`, `DTMESH-NNNN`, `BTEX-NNNN`, or any other uppercase key + digits). Take the first match.
- **GitHub PR develop:** do NOT extract the PR number from the commit message — always search for closed PRs targeting `develop` and match by commit SHA, filtered the same way as Step 3 (never fetch full unfiltered PR objects):
  ```
  gh pr list --repo <org>/<repo> --base develop --state closed --search <sha> --json number,url --jq '.[] | {number, url}'
  ```
- **Skip tag merge commits** — messages matching `Merge tag '...' into develop` have no associated PR; mark as "Tag merge only".

### Step 5 — Handle special cases

| Situation | What to put in the table |
|-----------|--------------------------|
| Branches are in sync (0 meaningful commits ahead) | `In sync` |
| Branch returns 404 | `Branches not found` |
| Only tag merge commits exist (no real PRs) | `Tag merge only (vX.Y.Z), no PR` |
| Repo has multiple qualifying commits | Expand to multiple rows (repeat the S.No) |

### Step 6 — Output the table

- Use the exact column headers from the Component List section.
- Populate only: `Commit Date`, `Commit ID`, `Jira ID`, `GitHub PR develop`.
- Leave `Component Release Version`, `GitHub PR main`, `Component Release Commit Hash` empty.
- Output as markdown in chat.

### Step 7 — Query Jira status for all identified tickets

- Collect all unique Jira IDs found.
- For each, run `python3 scripts/jira_rest.py get-issue <KEY> --fields issuetype,summary,status,assignee,priority,parent`.
- **Sub-task handling (critical):** if `issuetype` is `Sub-task`, fetch the `parent` ticket the same way and use **its** type/summary/status/assignee/priority for the row instead. In the `Jira ID` column, show `<PARENT-KEY> (derived from sub-task <SUB-TASK-KEY>)`.
- Output a second table: `Jira ID | Type | Summary | Status | Assignee | Priority`.
- **Warning / revert-candidate rule:** for every ticket NOT in status `RM Approved`, `Ready for Release Test`, or `Ready for Patch Test`, emit:
  > ⚠️ WARNING: `<JIRA-ID>` is in status `<status>` — not yet RM Approved or Ready for Release Test or Ready for Patch Test. Associated commit(s) flagged for revert: `<short-sha-1>, ...` in `<owner/repo>`.
  - For sub-task-derived rows, evaluate the **parent's** status and reference the parent Jira ID in the warning.
- If a ticket can't be fetched (401/404/wrong instance), note `Inaccessible` in the status column and warn the user.
- **Final summary:** an explicit "Commits flagged for revert" list (full SHA, Jira ID, Jira status, commit message first line, repository) for every commit tied to a non-approved ticket — state "None" if empty, never omit this list. This is what `mesh-release-pipeline` relies on to decide what to revert.

### Step 8 — Restore the previous GitHub identity

Run `gh auth switch --hostname github.com --user "<the account noted in Step 0.1>"` before finishing, regardless of how the rest of the skill went (including early stops/errors) — always attempt this step even if earlier steps failed. Confirm via `gh auth status --hostname github.com` that the restore succeeded before reporting the skill as complete. If Step 0.1 found no account was active beforehand, there is nothing to restore — just note that in the final summary.

This is the end of this skill's scope. It does not classify release types, compute version numbers, write `release-config.json`, create Jira tickets, or run any pipeline — that is `mesh-release-pipeline`'s responsibility.
