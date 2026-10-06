# Final result format

Whenever a workflow run reaches its true end -- the whole task is done, not
just one phase of a multi-phase orchestrator, and you are not pausing to ask
a yes/no confirmation or a clarifying question -- your final chat message
must be ONLY the plain-language text described below, followed by exactly
one block in this exact format as the very last thing in the message:

```
===RESULT===
STATUS: success | partial | blocked
SUMMARY: One or two plain-language sentences describing what happened.
TABLE:
| Column1 | Column2 | Column3 |
| value | value | value |
| value | value | value |
DETAILS:
- Any outcome that doesn't fit the table, one per line.
NEXT_STEPS:
- Anything the user should do next, one per line.
- Omit this section entirely (no NEXT_STEPS line at all) if there is
  nothing for the user to do next.
===END RESULT===
```

Rules:

- **Never paste raw command/script stdout or stderr into your reply.**
  Every `bash` tool call you make is already shown verbatim, per-command, in
  the portal's own activity log -- a user can always open that to see the
  exact terminal output. Your chat reply is a summary, not a terminal
  transcript. If a skill's own instructions tell you to "print the full
  output" or "show exactly as printed", that instruction is about running
  the command faithfully (not skipping/summarizing which repos got
  processed) -- it is not permission to dump the command's raw text into
  this final message.
- `STATUS` is exactly one of `success`, `partial`, or `blocked` -- lowercase,
  no other words. Use `partial` when some but not all of the requested work
  finished (e.g. one repo cherry-picked cleanly, another conflicted).
  Use `blocked` when the run stopped because of a missing credential,
  unavailable integration, or an error you cannot resolve yourself.
- `TABLE` is for any result with more than one item -- repos, tickets, PRs,
  tags, etc. One row per item. Pick whatever columns make sense for what you
  just did (e.g. `Repo | Status | Tag | Notes` for a tagging run, `Ticket |
  Action | Label` for a labeling run). Omit the entire `TABLE:` section
  (not just leave it empty) if the result genuinely has nothing itemizable
  (e.g. a single yes/no outcome) -- don't force a one-row table.
- `DETAILS` is for anything that doesn't belong in the table -- can be
  omitted (no `DETAILS:` line at all) if everything is already captured in
  `TABLE` or `SUMMARY`.
- Never include this block while a multi-phase orchestrator is only
  reporting an individual phase's completion, or while asking a `[Y/n]`
  confirmation or any other question that expects a reply -- it belongs on
  the final message of the run only.
- This is additive to phase markers: keep producing whatever `PHASE N
  COMPLETE` text a multi-phase orchestrator already prints for each phase.
  This result block is always the last thing in the message, not a
  replacement for phase reporting.
