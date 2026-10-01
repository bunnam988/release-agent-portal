---
name: jira-release-ticket
description: Use when creating or updating a Mesh Components Release JIRA ticket in the RDKB project. Handles creating new release tickets with the standard format and updating existing tickets with component release data. Don't use for non-release JIRA tickets or tickets outside the RDKB project.
---

# JIRA Release Ticket

Creates or updates a `[<DD-MM-YYYY>] Mesh Components Release` ticket in the RDKB project on JIRA (`https://ccp.sys.comcast.net/`). Supports two modes: **Create** (new ticket) and **Update** (existing ticket with release data).

---

## Autonomous invocation (from `github-release-tracker`)

When this skill is loaded as a delegated step from the `github-release-tracker` skill (i.e. the caller provides a release tracking table as context and is running its own autonomous workflow), run **fully autonomously with no confirmation questions**, unless a sanity check genuinely fails or a required value cannot be determined:

- **Mode selection (Step 1):** Do not ask which mode to use.
  - Default to **Create Mode** (a new release ticket).
  - If the original user prompt (as relayed by the caller) explicitly requested updating an **existing** ticket, use **Update Mode** with the ticket key provided by the caller instead. If no ticket key was provided in that case, ask the user for it (this is unavoidable).
  - If the original user prompt explicitly said not to create a new ticket and did not name an existing ticket to update, skip this skill's execution entirely and report that back to the caller.
- **Step 2 confirmation:** Skip the "Proceed?" confirmation — create the ticket directly with the standard metadata.
- **Step 4:** After creating the ticket, automatically proceed to Update Mode (Step 7) to populate the release table — do not ask the user whether to proceed.
- **Step 8 confirmation:** Skip the "Does it look correct?" confirmation — write the table to the ticket description directly.
- All other checks that are genuine failure conditions (JIRA connectivity down, sanity checks in Step 6 failing, ambiguous org/PR values that can't be inferred) still apply and must still stop and report to the user — autonomy does not override correctness/safety checks, only removes routine confirmation prompts.
- When this skill is invoked **standalone** (not delegated from `github-release-tracker`), follow the normal interactive workflow below unchanged.

---

## Component List

### Default list

If the user does **not** provide a specific set of repos, use this hardcoded list (the standard mesh components under `rdk-gdcs`):

| S.No | Component |
|------|-----------|
| 1 | mesh-agent |
| 2 | mesh-wifi-optimizer |
| 3 | onewifi-extender |
| 4 | opensync-core-extender |
| 5 | opensync-core-gateway |
| 6 | opensync-fut |
| 7 | opensync-mso-comcast |
| 8 | opensync-platform-qca |
| 9 | opensync-platform-rdk |
| 10 | opensync-service-provider-comcast |
| 11 | opensync-thirdparty |
| 12 | opensync-vendor-comcast |
| 13 | opensync-vendor-plume |
| 14 | soc-qualcomm-qsdk |
| 15 | mesh-deps-toolkit |

### User-provided list

If the user explicitly provides a set of repositories (by name, URL, or from tracker output), use **only** those repos for the table. Do not filter them against the default list — they may be entirely different repos.

- Number rows sequentially starting at 1.
- If the GitHub org is not obvious from the repo names, ask the user for it (needed for PR URL inference).
- All other workflow steps apply identically regardless of which list is used.

### Description table column headers (preserve exactly):
`S.No | Component | Commit Date | Commit ID | Jira ID | GitHub PR develop | Component Release Version | GitHub PR main | Component Release Commit Hash`

---

## Pre-requisite

### Step 0 — Verify JIRA connectivity

- Test the JIRA MCP server connection using the `test_connection` tool.
- If the connection fails, **STOP** and tell the user: "JIRA MCP server is not connected. Please ensure it is active and authenticated before proceeding."
- Do **not** proceed to any further steps until connectivity is confirmed.

---

## Mode Selection

### Step 1 — Ask the user which mode to use

- Use the Question tool to ask: **"Would you like to create a new release ticket or update an existing one?"**
- Options: `Create new ticket`, `Update existing ticket`
- Route to **Create Mode** (Step 2) or **Update Mode** (Step 5) based on the answer.

---

## Create Mode

### Step 2 — Gather ticket metadata

- **Project:** RDKB (RDK Broadband)
- **Issue Type:** Task
- **Title:** `[<DD-MM-YYYY>] Mesh Components Release` — use today's date in DD-MM-YYYY format.
- **Priority:** P1
- **Components:** `meta-rdk-comcast-broadband`
- **Telemarker:** Not Applicable
- **Branch:** stable2

Before creating, confirm with the user:
> Creating RDKB Task: `[<today's date>] Mesh Components Release`, Priority P1, Component: meta-rdk-comcast-broadband, Telemarker: Not Applicable, Branch: stable2. Proceed?

### Step 3 — Discover field IDs and create the ticket

1. Call `get_create_metadata` for project `RDKB` with issue type `Task` to find exact field IDs for: summary, priority, components, telemarker, branch, and any other required fields.
2. Create the ticket using the discovered field IDs and values.

**On failure:** Do **not** guess or fabricate field values. Report the exact error to the user and ask them how they'd like to proceed. Show the fields that failed and their expected formats.

### Step 4 — Report the created ticket

- Display the ticket key (e.g., `RDKB-55123`) and a link to the ticket.
- Ask the user: **"Would you like to proceed to Update Mode to populate the release table in the ticket description?"**
- If the user says yes, proceed to **Step 7** (Gather table data) — skip Steps 5 and 6 since the ticket was just created and its key is already known.
- If the user declines, **STOP** here.

---

## Update Mode

### Step 5 — Get the ticket key

- Ask the user for the RDKB ticket key to update (e.g., `RDKB-55123`).

### Step 6 — Sanity checks (ALL must pass)

Fetch the ticket using `get_issue` and verify **all four** conditions. If **any** check fails, **STOP** and report which check(s) failed. Do **not** proceed with the update.

| Check | Condition | Failure message |
|-------|-----------|-----------------|
| Title format | Summary matches pattern `[<DATE>] Mesh Components Release` | "Ticket title does not match the expected release ticket format." |
| Project | Project key is `RDKB` | "Ticket is not in the RDKB project." |
| Status | Status is one of: `New`, `Analyzing`, `Code Development` | "Ticket status is `<status>` — must be New, Analyzing, or Code Development to update." |
| Age | Ticket was created within the last 14 days | "Ticket is older than 2 weeks (created `<date>`). Refusing to update a stale ticket." |

### Step 7 — Gather table data

- Check if table data is available from the current conversation context (e.g., output from the `github-release-tracker` skill or data provided by the user).
- If data is **not** available in context, ask the user to provide it or to run the `github-release-tracker` skill first.

**Determine the component list to use:**
- If the user explicitly provided a set of repos earlier in the conversation, use that list.
- Otherwise, use the default 15-component list.

**Map the data to the chosen component list,** filling in all available columns:
  - `S.No` — sequential number starting at 1
  - `Component` — from the chosen list
  - `Commit Date` — format `YYYY-MM-DD`
  - `Commit ID` — first 12 characters of SHA
  - `Jira ID` — e.g., `RDKB-NNNNN`, `LTE-NNNN`
  - `GitHub PR develop` — PR link or `In sync` / `Branches not found`
  - `Component Release Version` — e.g., `v1.2.3` (leave empty if not yet determined)
  - `GitHub PR main` — PR link (leave empty if not yet created)
  - `Component Release Commit Hash` — full SHA (leave empty if not yet merged)

#### PR URL inference

When the input data contains a PR reference as just a number (e.g., `#42`, `42`, `PR #42`, `PR 42`) instead of a full URL, reconstruct the full GitHub PR URL using:

```
https://github.com/<org>/<component-name>/pull/<PR-number>
```

- **Org** defaults to `rdk-gdcs` when using the default component list.
- **Org** must be confirmed with the user when using a user-provided component list, unless it was already stated earlier in the conversation.
- **Repo name** is the `Component` value for that row.
- **PR number** is the numeric value extracted from the input.

This applies to both the `GitHub PR develop` and `GitHub PR main` columns.

If any of these three values (org, component name, PR number) cannot be reliably determined for a given row, **do not guess** — ask the user to provide the full PR URL for that entry.

### Step 8 — Confirm before updating

- Display the full table to the user in markdown format.
- Ask: **"This table will be written to the description of `<TICKET-KEY>`. Does it look correct?"**
- If the user requests changes, apply them and re-confirm.
- Only proceed when the user explicitly confirms.

### Step 9 — Update the ticket description

1. Fetch the current ticket to get the latest `version` number (for optimistic locking).
2. Format the table as a JIRA-compatible description. Use JIRA wiki markup table format:
   ```
   ||S.No||Component||Commit Date||Commit ID||Jira ID||GitHub PR develop||Component Release Version||GitHub PR main||Component Release Commit Hash||
   |1|mesh-agent|2025-06-15|abc123def456|RDKB-12345|[PR #42|https://github.com/rdk-gdcs/mesh-agent/pull/42]| | | |
   ```
3. Update the ticket description using `update_issue`.

### Step 10 — Confirm the update

- Report success: "Ticket `<TICKET-KEY>` description updated with the release component table."
- If the update fails, report the error and ask the user how to proceed.

---

## Rules

1. **Never guess JIRA field IDs or values.** Always discover them via `get_create_metadata`.
2. **Never update a ticket that fails sanity checks.** All four checks in Step 6 must pass.
3. **Always confirm with the user** before creating or updating a ticket.
4. **Do not modify other ticket fields** (priority, status, assignee, etc.) during an update — only the description.
5. **Preserve existing description content** if the user asks to append rather than replace.
6. **Component list:** Use the default 15-component list unless the user explicitly provides their own set of repos. When user-provided, do not filter or validate against the default list.
7. **Date format in title:** `DD-MM-YYYY`. Date format in table: `YYYY-MM-DD`.
