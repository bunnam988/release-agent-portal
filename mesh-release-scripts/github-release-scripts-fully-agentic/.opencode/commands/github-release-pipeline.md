---
description: Run the full autonomous GitHub release pipeline (commit gathering, Jira status, reverts, release-config.json, Jira ticket, pipeline stage 1) — stops once before the irreversible stage 2.
subtask: false
---

You are being invoked directly by the user via the `/github-release-pipeline` command. This is a human-triggered action — proceed with the full autonomous workflow below exactly as written, with no additional scope confirmation beyond what the workflow itself specifies.

If the user supplied arguments, they are provided here: $ARGUMENTS

Follow this workflow in full:

@.opencode/prompts/github-release-pipeline-workflow.md
