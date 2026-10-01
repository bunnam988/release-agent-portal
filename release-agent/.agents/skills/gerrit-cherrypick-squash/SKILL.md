---
name: gerrit-cherrypick-squash
description: >
  Use when cherry-picking Jira-linked Gerrit changes to a target branch across
  multiple repos. Handles dependency ticket traversal, rdkjenkins03 MERGED comment
  scanning, sprint/develop branch filtering, multi-repo cherry-pick, squash,
  commit message enforcement, and Gerrit topic tagging. Don't use for general git
  operations unrelated to Jira-driven Gerrit release workflows.
argument-hint: "Required: one or more Jira ticket keys, target branch, and Gerrit topic (collected as session parameters, see Step 1). Optional: '--gerrit-host <host>' to override config.yaml's gerrit_host for this run only."
metadata:
  author: Santhosh GujulvaJagadeesh
  source: internal
---

> **⚠️ THERE IS NO DEDICATED SCRIPT FOR THIS SKILL'S GERRIT/GIT MECHANICS** —
> do not look for or try to run `gerrit_cherrypick_squash.py`/`.sh` or
> anything similar; it does not exist. Cherry-picking, squashing, and
> pushing with topic are done directly via `git`/Gerrit REST calls shown in
> the steps below. The only script this skill ever invokes is the shared
> `scripts/jira_rest.py` helper, and only for the Jira read steps.

## Quick start

Given one or more Jira ticket keys, this skill:

1. Walks all linked/dependent tickets and sub-tasks (excluding CATR-* and "triage" titles)
2. Finds `rdkjenkins03` MERGED comments on `*_sprint` or `develop` branches only
3. Verifies each Gerrit is actually MERGED via the API
4. Groups by repository — cherry-picks all; squashes if multiple per repo
5. Enforces required commit message fields, pushes for review, sets a Gerrit topic

Jira key rule (generic, not RDK-only): `[A-Z0-9]+-\d+`
- Examples that MUST be accepted: `XB10-2860`, `CBR2-1234`, `RDKB-64184`

**Credentials** are always read from `config.yaml` in the working directory (never `.netrc`).

---

## Config file (`config.yaml`)

`gerrit_host` (no scheme prefix, e.g. `gerrit.teamccp.com`) can be overridden
for this run only with `--gerrit-host <host>`; otherwise it comes from
`config.yaml`. Never hardcode the host — always build URLs from the resolved
`{gerrit_host}` so an override actually takes effect everywhere.

```yaml
gerrit_host: "gerrit.teamccp.com"
```

Gerrit HTTPS credentials come from `~/.netrc` (a `machine {gerrit_host}` entry) — never store them in config.yaml.

Jira reads (Steps 2-3 below) go through `python3 scripts/jira_rest.py` (credentials
from `ccp_jira.env` in the working directory — never read `JIRA_TOKEN`/etc. directly,
and never call Jira MCP tools). If `ccp_jira.env` is missing or the script reports a
credential error, STOP and tell the user exactly that.

No dedicated script exists for the Gerrit/git cherry-pick and squash mechanics
themselves. The only script referenced anywhere in this file is `scripts/jira_rest.py`
above — everything else (cherry-picking, squashing, pushing with topic) is done
directly via `git`/Gerrit REST calls as shown in the steps below, not a separate
script. Do not assume one exists.

Build the authenticated Gerrit base URL at runtime:

```python
GERRIT_BASE = f"https://{cfg['gerrit_host']}/a"
```

---

## Workflow

Copy this checklist and track progress:

- [ ] Step 1: Gather session parameters
- [ ] Step 2: Fetch and filter Jira tickets
- [ ] Step 3: Scan comments and collect Gerrits
- [ ] Step 4: Verify Gerrit status
- [ ] Step 5: Cherry-pick and squash per repo
- [ ] Step 5a: Optionally prepare meta-rdk-broadband srcrev/pkgrev update
- [ ] Step 6: Push and set topic

---

### Step 1 — Gather session parameters

Collect before starting:

| Parameter | Example |
|---|---|
| Parent Jira ticket(s) | `RDKB-64184` |
| Default target branch | `topic/cujo-agent-app` |
| Per-repo branch overrides | `meta-rdk-sr213 → 25Q4_sprint_kirkstone` |
| Gerrit topic | `re_testing_1` (or a Jira key like `RDKB-66564` when invoked from `stable2-meta-sync-orchestrator` — see Step 5g) |
| Commit message defaults | See template below |

Optional prepared inputs from upstream stable2 sync skills:
- `stable2_release_tags.yaml`
- `stable2_srcrev_updates.yaml`
- Prepared workspace: `{meta_sync_workspace}/meta-rdk-broadband`
- Prepared workspace branch: `meta_support_branch` from `config.yaml` (for example `stable2`)

---

### Step 2 — Fetch and filter Jira tickets

Fetch the parent ticket:

```bash
python3 scripts/jira_rest.py get-issue {KEY} --fields issuelinks,subtasks,summary
```

Collect all `inwardIssue` / `outwardIssue` keys from `.fields.issuelinks`, plus all
`.fields.subtasks`.

**Exclude:**
- Project key is `CATR`
- Summary contains `triage` (case-insensitive)

Recursively fetch each linked ticket's own links and subtasks if needed (one level is usually sufficient).

---

### Step 3 — Scan comments for MERGED Gerrits

For each non-excluded ticket:

```bash
python3 scripts/jira_rest.py get-comments {KEY}
```

Filter `.comments[]` where `author.name == "rdkjenkins03"` AND body contains `"MERGED"`.

Extract the Gerrit change number and branch using this regex pattern:

```
\[#(\d+)\s+[^\[]+\[([^\]]+)\]
```

**Keep only** branches matching `_sprint` or `develop` as substrings.  
**Skip** `stable2` and all other branches.

---

### Step 4 — Verify Gerrit status

For each collected change ID:

```
GET {GERRIT_BASE}/changes/{ID}
Strip )]}'\n prefix before JSON parsing.
```

Keep only if `status == "MERGED"` AND `branch` contains `_sprint` or `develop`.

Group survivors by `project`:

```python
by_project = defaultdict(list)
for gid, (project, branch) in gerrit_map.items():
    by_project[project].append(gid)
```

---

### Step 5 — Cherry-pick and squash per repo

Run all repos in parallel (use subagents). For each repo:

#### 5a. Check/create target branch

```bash
curl -s "{GERRIT_BASE}/projects/{project_encoded}/branches/{branch_encoded}"
```

- If `revision` exists → clone with `--branch <target> --depth 5`
- If not → clone default (`--depth 1`), create branch via:
  ```bash
  curl -s -X PUT -H "Content-Type: application/json" \
    -d '{"revision":"<parent_sha>"}' \
    "{GERRIT_BASE}/projects/{project_encoded}/branches/{branch_encoded}"
  ```
  Get `parent_sha` from PS1 of the first change: `git log --format=%P -1 FETCH_HEAD`.

#### 5b. Find a non-empty patchset

```
GET {GERRIT_BASE}/changes/{ID}?o=ALL_REVISIONS
```

Sort patchsets **latest-first**. For each:

```bash
git fetch --depth=2 origin refs/changes/XX/CHANGEID/PS_NUM
git diff --name-only FETCH_HEAD^..FETCH_HEAD
```

Where `XX = str(CHANGEID % 100).zfill(2)`.

Use first patchset that produces file output. If none found → skip the change.

#### 5c. Cherry-pick

```bash
git cherry-pick -x FETCH_HEAD
```

- `empty` / `nothing to commit` → `git cherry-pick --skip`
- conflict → resolve (see conflict resolution below), then `git cherry-pick --continue --no-edit`
- other error → report and stop

#### 5d. Squash (if multiple picks)

```bash
git reset --soft HEAD~N
git commit -F <tempfile_with_combined_subjects>
```

#### 5e. Enforce commit message fields

Required fields (inject before `Change-Id:` if absent):

```
Reason for change: <value>
Test Procedure: <value>
Risks: Low|Medium|High
Priority: P1|P2|P3
Signed-off-by: <email>
```

If no `Change-Id` present, generate:

```python
import hashlib, time
tree   = run(["git", "log", "--format=%T", "-1"])
author = run(["git", "log", "--format=%ae", "-1"])
cid = "I" + hashlib.sha1(f"{tree}{author}{time.time()}".encode()).hexdigest()
```

#### 5f. Push

```bash
git push {GERRIT_BASE}/{project} HEAD:refs/for/{target_branch}
```

Set `GIT_TERMINAL_PROMPT=0` to prevent interactive prompts.

#### 5g. Optional meta-rdk-broadband update (MINIMAL EXTENSION)

Do NOT change the existing Gerrit discovery/cherry-pick behavior above.
Only add this optional step if upstream stable2 sync artifacts/workspace exist.

If the following exist:
- `stable2_release_tags.yaml`
- `stable2_srcrev_updates.yaml`
- prepared workspace `{meta_sync_workspace}/meta-rdk-broadband`

Then include one additional Gerrit change for `meta-rdk-broadband`:

1. Use the prepared workspace on configured `meta_support_branch`
2. Verify only intended files are modified:
  - `conf/include/generic-srcrev.inc`
  - `conf/include/generic-pkgrev.inc`
3. Stage both files
4. Create ONE squashed commit for all meta-layer changes
5. If the Gerrit topic for this run matches a Jira key (`[A-Z0-9]+-\d+`, e.g. the bi-weekly
   sync tracking ticket key when invoked from `stable2-meta-sync-orchestrator`), add
   `Sync-Ticket: {topic}` as an extra field in this commit's message (same placement rule as
   Step 5e's other enforced fields, before `Change-Id:`) — this commit has no ticket of its own
   to reference otherwise, unlike the individual per-repo cherry-picks above. Do NOT add this
   field when the topic isn't Jira-key-shaped (e.g. an ad-hoc topic like `re_testing_1`).
6. Push that commit to Gerrit using the SAME topic as all other repo changes

Rules:
- This is one extra repo-level Gerrit change under the same topic, not a replacement for existing behavior
- Do NOT split srcrev/pkgrev into separate Gerrit reviews
- Do NOT change how non-meta repos are handled
- Do NOT add the `Sync-Ticket:` field to any other commit — only this meta-layer one (see rule above)
- If no prepared meta-layer changes exist, skip this step silently

---

### Step 6 — Set Gerrit topic

Extract new change number from push output (`https://gerrit.teamccp.com/NNNNN`):

```bash
curl -s -X PUT -H "Content-Type: application/json" \
  -d '{"topic":"<topic>"}' \
  "{GERRIT_BASE}/changes/{NEW_ID}/topic"
```

---

## Conflict resolution

The most common conflict pattern: target branch is missing default-value entries that the cherry-pick adds (e.g., `system_defaults_*`, `utopia.bbappend`).

Default strategy — **accept incoming**:

```bash
git checkout --theirs <conflicting_file>
git add <conflicting_file>
git cherry-pick --continue --no-edit
```

Always confirm with the user before resolving if the conflict is non-trivial (e.g., both sides have substantive changes to the same block).

---

## Commit message template

```text
<subject line>

Reason for change: <description>
Test Procedure:
1) <test steps>

Risks: Low|Medium|High
Priority: P1|P2|P3

Change-Id: I<sha1>
Signed-off-by: <email>
(cherry picked from commit <sha>)
```

---

## Key technical notes

- **Always use source branch SHA**, not `refs/changes` patchset refs — merged commits on `refs/changes` often appear empty because their diff was absorbed into a merge commit.
- **Check all patchsets latest-first** — if the latest is empty, try earlier ones (e.g., PS19 for some broadband repos).
- **macOS**: `tac` unavailable — reverse lists with Python. GNU awk syntax in Gerrit commit-msg hook is incompatible with macOS `awk` — generate `Change-Id` via Python SHA1 instead.
- **`refs/for/<branch>`** requires the target branch to exist on Gerrit first — create via REST API if absent.
- **Parallel execution**: run each repo as a separate subagent for speed. Conflicts in one repo do not block others.
- **URL-encode project paths**: `/` → `%2F` when used in Gerrit REST API paths.

---

## Session record format

After completing a run, record results in this format for the session doc:

```markdown
| New Gerrit | Repository | Source(s) | Target Branch | Notes |
|---|---|---|---|---|
| [NNNNNN](https://gerrit.teamccp.com/NNNNNN) | `repo-name` | 123456 | `branch` | Single/Squashed/Conflict resolved |
```
