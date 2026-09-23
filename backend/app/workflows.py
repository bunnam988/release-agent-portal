"""Curated catalog of release-agent workflows exposed to the UI.

Deliberately NOT sourced from opencode's raw /api/skill + /api/agent
listing: that surface also contains opencode's built-ins (`build`, `plan`,
...) and any skills picked up from the operator's global opencode config,
none of which should ever be exposed to release-agent users. Keeping this
list hand-written also lets us attach UI-friendly labels/args that don't
exist in SKILL.md/AGENT.md frontmatter.
"""

from pydantic import BaseModel


class WorkflowArg(BaseModel):
    name: str
    flag: str
    label: str
    kind: str  # "flag" | "text"
    default: str | None = None
    # Text args a user must fill in before the workflow can run at all
    # (e.g. the Jira ticket for on-demand-cherry-pick). Flags are never
    # required. Enforced both client-side (disables Run) and server-side
    # (see routers/sessions.py) since the client check is only a UX nicety.
    required: bool = False


class Workflow(BaseModel):
    id: str
    label: str
    description: str
    # If set, the opencode session is switched to this custom agent.
    # Otherwise the default agent handles it by recognizing the skill from
    # the starter_command text (matches today's CLI usage).
    agent: str | None = None
    starter_command: str
    args: list[WorkflowArg] = []
    # Skills that mutate Jira/GitHub/Gerrit state, vs. read-only ones.
    mutating: bool = False
    # Suggested next step in the documented pipeline (see the
    # stable2-release-orchestrator / stable2-meta-sync-orchestrator
    # AGENT.md phase order). Shown as a "Run next" button once this
    # workflow's session finishes. None = no obvious next step (e.g. the
    # full orchestrators already do everything, or a standalone workflow).
    next_workflow_id: str | None = None
    # MCP servers this workflow needs (matches keys from opencode's /mcp
    # status endpoint, e.g. "jira-ccp"). Checked before starting a session
    # -- see routers/sessions.py -- so a disconnected integration produces
    # one clear portal-native error instead of the agent trying, failing,
    # and telling the user to run CLI commands it has no way to act on.
    requires_mcp: list[str] = []
    # "primary": the handful of real use cases (biweekly scan, full
    # stable2 release, on-demand single-ticket cherry-pick) get prominent
    # hero cards on the dashboard. "advanced": everything else (individual
    # skills, useful for re-running one step or debugging) lives lower on
    # the page so it doesn't compete for attention with what people
    # actually come here to do.
    category: str = "advanced"
    # Role-based access (see backend/app/routers/auth.py) -- defaults to
    # True (admin-only) for every workflow, so a new entry added later is
    # safe by default and has to be *deliberately* opted into "user" role
    # access rather than accidentally exposed. Only "on-demand-cherry-pick"
    # is opted out of this today, per the current requirement. Has no
    # effect at all while auth is disabled (everyone is treated as admin).
    admin_only: bool = True
    # Plain-English, numbered walkthrough of what actually happens when
    # this runs, shown in the UI as a "How this works" section before
    # starting -- per user feedback that first-time users need more than
    # the one-line description to know what they're about to kick off.
    help_steps: list[str] = []


_DRY_RUN = WorkflowArg(name="dry_run", flag="--dry-run", label="Dry run", kind="flag")
_TEST_REPO = WorkflowArg(name="test_repo", flag="--test-repo", label="Test repos only", kind="flag")

CATALOG: list[Workflow] = [
    Workflow(
        id="main-tagging",
        label="Main Tagging",
        description=(
            "Tags and releases any tracked repo whose develop branch is "
            "ahead of its latest release tag, via the standard git-flow "
            "release process."
        ),
        starter_command=(
            "Run `python scripts/main_tagging_release.py --yes` and show its full output exactly "
            "as printed, without summarizing or skipping any repo."
        ),
        args=[_DRY_RUN],
        mutating=True,
        category="primary",
        help_steps=[
            "Checks every tracked repo's develop branch against its latest release tag.",
            "For any repo that's ahead, runs the standard git-flow release process: generates a changelog, creates the release tag, and pushes it.",
            "With Dry run checked, shows exactly what would be tagged/released without actually doing it.",
        ],
    ),
    Workflow(
        id="track-for-stable2",
        label="Track for stable2",
        description=(
            "Discovers newly merged PR commits on develop and adds the "
            "track_for_stable2 label to their Jira tickets. Runs "
            "incrementally using saved per-repo state."
        ),
        starter_command="/track-for-stable2",
        args=[
            WorkflowArg(name="label", flag="--label", label="Jira label (default: track_for_stable2)", kind="text"),
            _DRY_RUN,
            _TEST_REPO,
        ],
        mutating=True,
        next_workflow_id="stable2-candidates",
        help_steps=[
            "First run ever bootstraps from the latest tag; every run after that only looks at commits merged since the last run, using saved per-repo state.",
            "For each newly merged develop commit, finds its linked Jira ticket.",
            "Adds the track_for_stable2 label (or your chosen label) to that ticket.",
            "With Dry run checked, shows which tickets would be labeled without labeling them.",
        ],
    ),
    Workflow(
        id="stable2-candidates",
        label="Filter stable2 candidates",
        description=(
            "Finds Jira tickets with a chosen candidate label that are not "
            "yet marked *_considered. Read-only; writes "
            "stable2_candidates.yaml."
        ),
        starter_command="/stable2-candidates",
        args=[
            WorkflowArg(name="candidate_label", flag="--candidate-label", label="Candidate label", kind="text"),
            _TEST_REPO,
        ],
        next_workflow_id="stable2-status-evaluator",
        help_steps=[
            "Searches Jira for tickets carrying your chosen candidate label.",
            "Filters out any already marked *_considered from a previous cycle.",
            "Writes the remaining list to stable2_candidates.yaml — read-only, nothing in Jira/GitHub/Gerrit changes.",
            "Feeds directly into Evaluate stable2 readiness next.",
        ],
    ),
    Workflow(
        id="stable2-status-evaluator",
        label="Evaluate stable2 readiness",
        description=(
            "Fetches detailed Jira status (RM Approved, parent/linked "
            "tickets, dependencies) for candidates and groups them by "
            "readiness. Read-only."
        ),
        starter_command="/stable2-status-evaluator",
        args=[_TEST_REPO],
        next_workflow_id="jira-pr-lookup",
        help_steps=[
            "Takes the candidates list from the previous step.",
            "Fetches each ticket's real Jira status: RM Approved flag, parent ticket, and linked dependency tickets.",
            "Groups candidates into ready vs. not-ready based on that.",
            "Read-only — writes its evaluation, doesn't change Jira. Feeds Look up PRs for a Jira ticket next.",
        ],
    ),
    Workflow(
        id="jira-pr-lookup",
        label="Look up PRs for a Jira ticket",
        description=(
            "Finds all merged PRs to develop for a Jira user story, "
            "including subtasks and dependency tickets."
        ),
        starter_command="/jira-pr-lookup",
        args=[
            WorkflowArg(name="ticket", flag="", label="Jira ticket key", kind="text"),
            _DRY_RUN,
            _TEST_REPO,
        ],
        next_workflow_id="stable2-release-tracking-ticket",
        help_steps=[
            "Starting from the ticket you give it, walks every subtask and dependency ticket (breadth-first).",
            "For each one, finds the PR(s) that were actually merged to develop.",
            "Writes the full list to pr_list.yaml for the labeling/tagging steps that follow.",
            "With Dry run checked, shows the lookup without writing the file.",
        ],
    ),
    Workflow(
        id="stable2-ready-considered-labeler",
        label="Label READY tickets as considered",
        description=(
            "Adds track_for_stable2_considered to READY Jira tickets before "
            "the stable2 meta-sync phases. Previews before applying labels."
        ),
        starter_command="/stable2-ready-considered-labeler",
        args=[
            WorkflowArg(
                name="label",
                flag="--label",
                label="Jira label (default: track_for_stable2_considered)",
                kind="text",
            ),
            _DRY_RUN,
            _TEST_REPO,
        ],
        mutating=True,
        next_workflow_id="stable2-github-release-tagger",
        help_steps=[
            "Takes the READY-grouped tickets from the readiness evaluation step.",
            "Shows a preview of exactly which tickets will be labeled before touching anything.",
            "On confirmation, adds the *_considered label to each — this marks them officially in-scope for this sync cycle.",
            "Run this before the GitHub tagging / Gerrit cherry-pick steps, since those only act on tickets already marked considered.",
        ],
    ),
    Workflow(
        id="stable2-github-release-tagger",
        label="Create stable2 GitHub release tags",
        description="Creates GitHub stable2 release tags for eligible repos.",
        starter_command="/stable2-github-release-tagger",
        args=[
            WorkflowArg(name="support_branch", flag="--support-branch", label="Support branch (from config.yaml if blank)", kind="text"),
            WorkflowArg(name="meta_support_branch", flag="--meta-support-branch", label="Meta support branch (from config.yaml if blank)", kind="text"),
            WorkflowArg(name="gerrit_host", flag="--gerrit-host", label="Gerrit host (from config.yaml if blank)", kind="text"),
            WorkflowArg(name="gerrit_repo", flag="--gerrit-repo", label="Gerrit repo path (from config.yaml if blank)", kind="text"),
            _DRY_RUN,
            _TEST_REPO,
        ],
        mutating=True,
        next_workflow_id="stable2-srcrev-updater",
        help_steps=[
            "For every repo with a considered ticket, computes the next stable2 tag name.",
            "Creates the GitHub release for that tag, with auto-generated changelog notes covering everything since the last release.",
            "With Dry run checked, shows which tags would be created without creating them.",
        ],
    ),
    Workflow(
        id="stable2-srcrev-updater",
        label="Update SRCREV entries",
        description=(
            "Resolves GitHub SHAs for eligible repos and updates "
            "generic-srcrev.inc in the meta-rdk-broadband workspace."
        ),
        starter_command="/stable2-srcrev-updater",
        args=[_DRY_RUN, _TEST_REPO],
        mutating=True,
        next_workflow_id="gerrit-cherrypick-squash",
        help_steps=[
            "Resolves the real GitHub commit SHA for each eligible repo's new stable2 tag.",
            "Updates generic-srcrev.inc in the local meta-rdk-broadband git worktree to point at that SHA.",
            "Doesn't push anything itself — that happens as part of the Gerrit cherry-pick step next.",
        ],
    ),
    Workflow(
        id="gerrit-cherrypick-squash",
        label="Gerrit cherry-pick + squash",
        description=(
            "Cherry-picks Jira-linked Gerrit changes to a target branch "
            "across multiple repos, squashes, and tags with a shared topic."
        ),
        starter_command="/gerrit-cherrypick-squash",
        args=[
            WorkflowArg(name="gerrit_host", flag="--gerrit-host", label="Gerrit host (from config.yaml if blank)", kind="text"),
            _DRY_RUN,
            _TEST_REPO,
        ],
        mutating=True,
        help_steps=[
            "Finds Gerrit changes linked to the in-scope (considered) Jira tickets.",
            "Cherry-picks each one onto the target branch, per repo.",
            "Squashes each repo's cherry-picked changes into a single commit.",
            "Pushes under a shared Gerrit topic — normally the bi-weekly sync ticket's key, so run Create stable2 sync tracking ticket first.",
        ],
    ),
    Workflow(
        id="stable2-release-tracking-ticket",
        label="Create stable2 sync tracking ticket",
        description=(
            "Creates the bi-weekly stable2 sync Jira Task and links every "
            "successfully cherry-picked ticket to it with a 'deploys' link. "
            "Its key becomes the Gerrit topic used later, so run this before "
            "Gerrit cherry-pick + squash."
        ),
        starter_command="/stable2-release-tracking-ticket",
        args=[_DRY_RUN, _TEST_REPO],
        mutating=True,
        next_workflow_id="stable2-ready-considered-labeler",
        help_steps=[
            "Creates one Jira Task representing this bi-weekly sync cycle.",
            "Links every successfully cherry-picked ticket to it with a 'deploys' link.",
            "Its ticket key becomes the Gerrit topic used later — this is why it needs to run before Gerrit cherry-pick + squash, not after.",
        ],
    ),
    Workflow(
        id="stable2-pr-labeler",
        label="Label PRs for cherry-pick automation",
        description=(
            "Adds the 'cherry-pick to support/stable2' label to GitHub PRs "
            "collected from jira-pr-lookup, triggering GitHub automation."
        ),
        starter_command="/stable2-pr-labeler",
        args=[
            WorkflowArg(
                name="label",
                flag="--label",
                label="GitHub label (default: cherry-pick to support/stable2)",
                kind="text",
            ),
            _DRY_RUN,
            _TEST_REPO,
        ],
        mutating=True,
        help_steps=[
            "Takes the PRs found by Look up PRs for a Jira ticket.",
            "Adds the 'cherry-pick to support/stable2' label (or your chosen label) to each one on GitHub.",
            "That label is what triggers GitHub's own existing automation to actually open the cherry-pick PR — this workflow only adds the label, it doesn't open PRs itself.",
        ],
    ),
    Workflow(
        id="stable2-release-orchestrator",
        label="Full stable2 Release",
        description=(
            "Runs the complete stable2 release orchestration across all "
            "phases, with per-phase confirmation gates."
        ),
        agent="stable2-release-orchestrator",
        starter_command="Start the stable2 release orchestration.",
        args=[
            _DRY_RUN,
            WorkflowArg(name="candidate_label", flag="--candidate-label", label="Candidate label", kind="text"),
            WorkflowArg(
                name="reset_state",
                flag="--reset-state",
                label="Delete existing state files before starting (cannot be undone)",
                kind="flag",
            ),
        ],
        mutating=True,
        category="primary",
        help_steps=[
            "Runs every phase of the stable2 pipeline in the documented order automatically — tracking, candidates, readiness, PR lookup, labeling, ticket creation, GitHub tagging, SRCREV, Gerrit cherry-pick.",
            "Pauses between each phase for your explicit confirmation before continuing.",
            "Delete existing state files checkbox wipes all prior progress before starting — cannot be undone, only use this for a genuinely fresh cycle.",
            "This is the one-click option if you don't need to run/inspect individual phases yourself.",
        ],
    ),
    Workflow(
        id="stable2-meta-sync-orchestrator",
        label="stable2 meta sync orchestration",
        description=(
            "Post-PR-collection sync: GitHub cherry-pick, then creates the "
            "bi-weekly Jira tracking ticket (its key becomes the Gerrit topic "
            "used later), considered labeling, GitHub tagging, SRCREV prep, "
            "and finally Gerrit cherry-pick/squash under that topic."
        ),
        agent="stable2-meta-sync-orchestrator",
        starter_command="Start the stable2 meta sync orchestration.",
        args=[_DRY_RUN, _TEST_REPO],
        mutating=True,
        help_steps=[
            "Assumes PR collection already happened (jira-pr-lookup / PR labeling ran separately) — this picks up from there.",
            "Runs GitHub cherry-pick, then creates the bi-weekly Jira sync ticket (its key becomes the Gerrit topic used later).",
            "Continues with considered labeling, GitHub tagging, and SRCREV prep.",
            "Finishes with Gerrit cherry-pick/squash under that same topic — in that specific order, matching the documented pipeline.",
        ],
    ),
    Workflow(
        id="on-demand-cherry-pick",
        label="On-Demand Cherry-Pick",
        description=(
            "Takes one or more Jira tickets, cherry-picks their PRs to a "
            "branch you specify, computes a new tag per repo, updates "
            "SRCREV/PKGREV, and syncs any linked Gerrit changes under a "
            "shared topic."
        ),
        agent="on-demand-cherry-pick",
        starter_command="Start the on-demand cherry-pick workflow.",
        args=[
            WorkflowArg(
                name="tickets",
                flag="--tickets",
                label="Jira ticket(s) — comma-separated if more than one",
                kind="text",
                required=True,
            ),
            WorkflowArg(
                name="github_branch",
                flag="--github-branch",
                label="GitHub target branch",
                kind="text",
                required=True,
            ),
            WorkflowArg(
                name="gerrit_branch",
                flag="--gerrit-branch",
                label="Gerrit target branch",
                kind="text",
                required=True,
            ),
            WorkflowArg(name="topic", flag="--topic", label="Gerrit topic", kind="text", required=True),
            _DRY_RUN,
        ],
        mutating=True,
        category="primary",
        admin_only=False,
        help_steps=[
            "Takes the Jira ticket(s) you specify — comma-separated if more than one.",
            "Finds and cherry-picks each ticket's merged PR(s) onto the GitHub branch you name.",
            "Computes a new hotfix tag per repo and updates SRCREV/PKGREV to match.",
            "Syncs any linked Gerrit changes under the Gerrit topic you specify.",
        ],
    ),
]

# Human-friendly names for requires_mcp entries, used in user-facing error
# messages (never expose raw MCP server ids to the UI). Empty for now --
# every Jira-touching skill was migrated off the jira-ccp MCP connection
# onto the ccp_jira.env service-account REST token (see
# release-agent/scripts/jira_rest.py), so nothing in the catalog declares
# an MCP dependency anymore. Left in place, not deleted, in case a future
# workflow adds a real MCP dependency later.
INTEGRATION_LABELS: dict[str, str] = {}

_BY_ID = {w.id: w for w in CATALOG}


def get_workflow(workflow_id: str) -> Workflow:
    try:
        return _BY_ID[workflow_id]
    except KeyError:
        raise ValueError(f"Unknown workflow id: {workflow_id}")


def build_message(workflow: Workflow, args: dict[str, str | bool]) -> str:
    """Compose the initial chat message exactly as an engineer would type it
    at the opencode CLI, e.g. "/track-for-stable2 --dry-run --test-repo".
    """
    parts = [workflow.starter_command]
    for arg in workflow.args:
        value = args.get(arg.name)
        if not value:
            continue
        if arg.kind == "flag":
            parts.append(arg.flag)
        else:
            parts.append(f"{arg.flag} {value}".strip() if arg.flag else str(value))
    return " ".join(parts)
