---
name: mesh-release-ticket
description: "Use when creating or updating a Mesh Components Release JIRA ticket in the RDKB project. Handles creating new release tickets with the standard format and updating existing tickets with component release data. Don't use for non-release JIRA tickets, tickets outside the RDKB project, or the core-nw stable2 sync tracking ticket (see stable2-release-tracking-ticket for that -- different ticket format, different project usage)."
license: Comcast
argument-hint: "Optional: an existing RDKB ticket key to update instead of creating a new one"
metadata:
  author: ported from github-release-scripts (Mesh Maintainers team), rewritten onto this repo's jira_rest.py conventions
  source: local
---

# Skill: Mesh Release Ticket

Creates or updates a `[<DD-MM-YYYY>] Mesh Components Release` ticket in the RDKB project on Jira. Supports two modes: **Create** (new ticket) and **Update** (existing ticket with release data).

Ported from the original `jira-release-ticket` skill (standalone toolkit already used by the Mesh Maintainers team) — rewritten onto `scripts/jira_rest.py` instead of Jira MCP. Same credentials/service account already used by every other Jira-touching skill in this repo (`ccp_jira.env`) — no GitHub identity concerns here, this skill never touches GitHub directly.

---

## Critical Constraints — Read First

- **`jira_rest.py`'s exact, only location is `/workspace/scripts/jira_rest.py`** (the top-level `scripts/` directory shared by every skill in this repo, not a subfolder of this skill). Run it as `python3 scripts/jira_rest.py ...` from `/workspace`. Do not pre-emptively check whether it exists somewhere else first, or conclude it's "missing" without having actually run it from that exact path — confirmed as a real mistake in a sibling skill's testing (mesh-release-report), where checking the wrong location produced a false "missing script" conclusion.
- Use `python3 scripts/jira_rest.py` for every Jira operation (`get-issue`, `create-issue`, `update-issue`, `get-create-metadata` is not a real jira_rest.py subcommand — see Step 3 workaround below). Never call Jira MCP tools or read `JIRA_TOKEN`/etc. directly.
- If `ccp_jira.env` is missing or `jira_rest.py` reports a credential error, STOP and tell the user exactly that.
- **Never guess Jira field IDs.** `jira_rest.py` has no `get_create_metadata`/`list_issue_types` equivalent (those were Jira MCP-specific tools) — create issues using Jira's standard field names directly (`summary`, `issuetype`, `priority`, `components`, `description`, `customfield_*` only if a specific custom field is confirmed to exist for this project). If a field write fails with a 400 referencing an unknown/invalid field, report the exact Jira error to the user rather than guessing a different field name.
- Only the ticket **description** is ever updated in Update Mode — never priority, status, assignee, or any other field.
- Always confirm with the user before creating or updating a ticket, **unless** invoked as a delegated autonomous step (see below).

---

## Autonomous invocation (from `mesh-release-pipeline`)

When loaded as a delegated step from `mesh-release-pipeline` (the caller provides a release tracking table as context and is running its own autonomous workflow), run **fully autonomously with no confirmation questions**, unless a sanity check genuinely fails or a required value cannot be determined:

- **Mode selection (Step 1):** don't ask which mode to use.
  - Default to **Create Mode**.
  - If the original user prompt explicitly requested updating an existing ticket, use **Update Mode** with the ticket key the caller provides. If no key was provided in that case, ask the user for it (unavoidable).
  - If the original prompt explicitly said not to create a new ticket and didn't name an existing one to update, skip this skill entirely and report that back to the caller.
- **Step 2 confirmation:** skip it — create the ticket directly with the standard metadata.
- **Step 4:** after creating, automatically proceed to Update Mode (Step 7) to populate the release table — don't ask.
- **Step 8 confirmation:** skip it — write the table to the description directly.
- Genuine failure conditions (Jira connectivity down, Step 6 sanity checks failing, an unresolvable field error) still STOP and report — autonomy removes routine confirmation prompts, not correctness checks.
- When invoked standalone (not delegated), follow the normal interactive workflow below unchanged.

---

## Component List

Same source as `mesh-release-report`: read `/workspace/config/mesh_components.yaml` for the default 15-repo list and `rdk-gdcs` org, unless the user (or the calling skill) explicitly provided a different set of repos — in which case use only those, unfiltered against the default list.

### Description table column headers (preserve exactly):
`S.No | Component | Commit Date | Commit ID | Jira ID | GitHub PR develop | Component Release Version | GitHub PR main | Component Release Commit Hash`

---

## Pre-requisite

### Step 0 — Verify Jira connectivity

Run `python3 scripts/jira_rest.py get-issue RDKB-1 --fields key` (or any known-valid ticket) as a connectivity smoke test — if `jira_rest.py` reports a credential/connection error, **STOP** and tell the user: "Jira is not reachable right now (see error above). Please resolve this before proceeding." Do not proceed to any further step.

---

## Mode Selection

### Step 1 — Ask the user which mode to use

Use the Question tool: **"Would you like to create a new release ticket or update an existing one?"** Options: `Create new ticket`, `Update existing ticket`. Route to Create Mode (Step 2) or Update Mode (Step 5).

---

## Create Mode

### Step 2 — Gather ticket metadata

- **Project:** RDKB
- **Issue Type:** Task
- **Title:** `[<DD-MM-YYYY> today's date] Mesh Components Release`
- **Priority:** P1
- **Components:** `meta-rdk-comcast-broadband`
- **Telemarker:** Not Applicable (if this project has a custom "Telemarker" field; omit if it doesn't exist — see the field-guessing rule above)
- **Branch:** stable2 (if this project has a custom "Branch" field; omit if it doesn't exist — see the field-guessing rule above)

Before creating, confirm with the user (skip if delegated autonomously):
> Creating RDKB Task: `[<today's date>] Mesh Components Release`, Priority P1, Component: meta-rdk-comcast-broadband, Telemarker: Not Applicable, Branch: stable2. Proceed?

### Step 3 — Create the ticket

Build a fields JSON matching this shape and pass it to `jira_rest.py create-issue -` via stdin:

```json
{
  "project": {"key": "RDKB"},
  "issuetype": {"name": "Task"},
  "summary": "[<DD-MM-YYYY>] Mesh Components Release",
  "priority": {"name": "P1"},
  "components": [{"name": "meta-rdk-comcast-broadband"}]
}
```

**On failure:** do not guess or fabricate field values. Report the exact Jira error to the user (e.g. an unknown field name, invalid component, invalid priority value) and ask how they'd like to proceed — e.g. drop the offending field and retry, or supply a corrected value.

### Step 4 — Report the created ticket

- Display the ticket key and a link (`https://ccp.sys.comcast.net/browse/<KEY>`).
- Ask: **"Would you like to proceed to Update Mode to populate the release table in the ticket description?"**
- If yes, proceed to **Step 7** (skip Steps 5–6, the ticket was just created and its key is already known).
- If no, **STOP** here.

---

## Update Mode

### Step 5 — Get the ticket key

Ask the user for the RDKB ticket key to update (e.g. `RDKB-55123`), unless already provided by the caller.

### Step 6 — Sanity checks (ALL must pass)

Fetch via `python3 scripts/jira_rest.py get-issue <KEY> --fields summary,project,status,created` and verify all four. If any fails, **STOP** and report which one(s) — do not proceed.

| Check | Condition | Failure message |
|-------|-----------|------------------|
| Title format | Summary matches `[<DATE>] Mesh Components Release` | "Ticket title does not match the expected release ticket format." |
| Project | Project key is `RDKB` | "Ticket is not in the RDKB project." |
| Status | Status is one of: `New`, `Analyzing`, `Code Development` | "Ticket status is `<status>` — must be New, Analyzing, or Code Development to update." |
| Age | Created within the last 14 days | "Ticket is older than 2 weeks (created `<date>`). Refusing to update a stale ticket." |

### Step 7 — Gather table data

- Use table data already available in the current conversation context (e.g. from `mesh-release-report` output, or `mesh-release-pipeline`'s own tracking table). If not available, ask the user to provide it or run `mesh-release-report` first.
- Component list: the one determined in the "Component List" section above.
- Map data into all columns, same field mapping as the original table (`S.No`, `Component`, `Commit Date`, `Commit ID`, `Jira ID`, `GitHub PR develop`, `Component Release Version`, `GitHub PR main`, `Component Release Commit Hash`).

#### PR URL inference

When input data has a PR reference as just a number (`#42`, `42`, `PR #42`) instead of a full URL, reconstruct:
```
https://github.com/<org>/<component-name>/pull/<PR-number>
```
- **Org** defaults to `rdk-gdcs` for the default component list; confirm with the user for a user-provided list, unless already stated earlier in the conversation.
- If org, component name, or PR number can't be reliably determined, don't guess — ask for the full PR URL for that entry.

### Step 8 — Confirm before updating

Display the full table in markdown. Ask: **"This table will be written to the description of `<TICKET-KEY>`. Does it look correct?"** Apply any requested changes and re-confirm. Only proceed on explicit confirmation. (Skip this confirmation when invoked as a delegated autonomous step — see above.)

### Step 9 — Update the ticket description

1. Fetch the current ticket once more right before writing (`python3 scripts/jira_rest.py get-issue <KEY> --fields summary,description`) — a final freshness check immediately before the write, in case the description changed between Step 6's sanity check and now (e.g. someone else edited the ticket in between). If the description already contains a release tracking table that looks substantively different from what Step 8 confirmed, stop and flag this to the user rather than overwriting it silently.
2. Format the table as Jira wiki markup:
   ```
   ||S.No||Component||Commit Date||Commit ID||Jira ID||GitHub PR develop||Component Release Version||GitHub PR main||Component Release Commit Hash||
   |1|mesh-agent|2025-06-15|abc123def456|RDKB-12345|[PR #42|https://github.com/rdk-gdcs/mesh-agent/pull/42]| | | |
   ```
3. Write `{"description": "<the wiki-markup table>"}` to a temp JSON file (or pipe via stdin) and run:
   ```
   python3 scripts/jira_rest.py update-issue <KEY> -
   ```

### Step 10 — Confirm the update

Report success: "Ticket `<TICKET-KEY>` description updated with the release component table." If it fails, report the exact error and ask the user how to proceed.

---

## Rules

1. **Never guess Jira field names or values** not already confirmed to exist for this project — see the field-guessing rule at the top.
2. **Never update a ticket that fails sanity checks.** All four in Step 6 must pass.
3. **Always confirm with the user** before creating or updating a ticket, except when explicitly delegated autonomously.
4. **Only the description field is ever written in Update Mode** — never priority, status, assignee, etc.
5. **Preserve existing description content** if the user asks to append rather than replace.
6. **Component list:** the shared `config/mesh_components.yaml`, unless the user explicitly provides their own set of repos.
7. **Date formats:** `DD-MM-YYYY` in the title, `YYYY-MM-DD` in the table.
