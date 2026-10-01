# Prerequisites

## Tools

The following tools must be installed and available in your `PATH`:

| Tool | Purpose | Install |
|------|---------|---------|
| `git` | Version control operations | Pre-installed on macOS/Linux |
| `gh` | GitHub CLI - PRs, releases, API access | [Install guide](https://cli.github.com/) |
| `jq` | JSON parsing and config manipulation | `brew install jq` (macOS) / `apt install jq` (Linux) |
| [OpenCode](https://opencode.ai) | AI coding agent (runs the skills) | See [OpenCode docs](https://opencode.ai/docs) |

## OpenCode Configuration

### 1. MCP Server Configuration

Add the following to your `opencode.json` (or `opencode.jsonc`) configuration file:

```json
"mcp": {
  "github": {
    "type": "remote",
    "url": "https://flow.api.de.comcast.com/orgs/ide-common/pools/github/mcp",
    "enabled": true,
    "oauth": {}
  },
  "jira-ccp": {
    "type": "remote",
    "url": "https://flow.api.de.comcast.com/orgs/ide-common/pools/jira-ccp/mcp",
    "enabled": true,
    "oauth": {}
  }
}
```

> **Optional**: To explore more about OpenCode configuration, see https://github.com/comcast-ai-community/opencode-config

### 2. FLOW:Intelligence Studio Enrollment

Both the GitHub and Jira MCP servers are provided through Comcast FLOW:Intelligence Studio. You must authenticate before OpenCode can use them.

1. **Login to FLOW:Intelligence Studio** — https://ui-flow-intel.flow.ai-prod.cnap.comcast.net/login
2. **If not already enrolled**, get onboarding details here — https://etwiki.sys.comcast.net/spaces/DAIS/pages/1821882759/FLOW+Intelligence+Studio
3. **Connect OAuth providers**: Go to **Profile > Settings > OAuth Connections** and connect:
   - `Jira CCP OAuth`
   - `GitHub OAuth`

### 3. Authenticate MCP Servers

All MCP servers in this config are hosted on Comcast's internal Flow platform and use OAuth for authentication. You must authenticate each server before the agent can use it:

```bash
opencode mcp auth jira-ccp
opencode mcp auth github
```

### 4. Git & GitHub CLI Authentication

- **Git**: SSH or HTTPS access configured for `rdk-gdcs` repos.
- **GitHub CLI**: Must be authenticated with access to `rdk-gdcs` org repositories.
  ```bash
  gh auth login
  gh auth status  # verify access
  ```

## MCP Server Details

The OpenCode skills rely on MCP (Model Context Protocol) servers to interact with external services:

| MCP Server | Used By | Purpose |
|------------|---------|---------|
| **GitHub MCP** | `github-release-tracker` | Fetches commits from `develop`/`main` branches, searches PRs by commit SHA, reads release tags, and lists repository data across `rdk-gdcs` org |
| **Jira MCP** | `github-release-tracker`, `jira-release-ticket` | Queries Jira ticket status/details (RM Approved checks), creates release tickets in RDKB project, updates ticket descriptions with release tables |

### GitHub MCP Server

Required for the `github-release-tracker` skill to:
- List commits on `develop` and `main` branches
- Search for pull requests by commit SHA
- Fetch tags and releases from repositories
- Access all 15 repositories under the `rdk-gdcs` organization

### Jira MCP Server

Required for both skills. Connects to Comcast Jira (CCP) via the FLOW:IS provider.

The `github-release-tracker` skill uses it to:
- Query ticket status for all identified Jira IDs (RDKB, LTE, DTMESH, BTEX patterns)
- Check if tickets are `RM Approved` or `Ready for Release Test`

The `jira-release-ticket` skill uses it to:
- Test connection (`test_connection`) before any operations
- Discover issue types and field metadata (`list_issue_types`, `get_create_metadata`)
- Create new tickets in the RDKB project (`create_issue`)
- Fetch and update existing tickets (`get_issue`, `update_issue`)

The skill will refuse to proceed if the Jira MCP connection test fails.

## OpenCode Skills

The `.opencode/skills/` directory contains skill definitions that OpenCode discovers automatically when you run it from this repository. No additional registration is required — OpenCode searches `.opencode/skills/<name>/SKILL.md` in the project directory by default.

Available skills:

- `github-release-tracker` — Identifies unreleased commits across repositories
- `jira-release-ticket` — Creates/updates JIRA release tickets

These skills will appear in OpenCode's `<available_skills>` list automatically when working within this repo.

### Using Skills Outside This Repository

To use these skills globally (from any directory), copy them to your OpenCode global skills directory:

```bash
cp -r .opencode/skills/github-release-tracker ~/.config/opencode/skills/
cp -r .opencode/skills/jira-release-ticket ~/.config/opencode/skills/
```

Once copied, the skills will be available in all OpenCode sessions regardless of the current working directory.
