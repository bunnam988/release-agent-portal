# Headless opencode server hosting this repo's skills/agents for the
# release-agent-portal UI. See ../release-agent-portal/DESIGN.md.
FROM node:22-slim

# ripgrep is a required opencode dependency (used by its search tools).
# python3 + python3-yaml: scripts/cherry_pick_to_stable2.py and
# jira-pr-lookup/scripts/fetch_prs.py are real Python scripts the skills
# shell out to, not just LLM-in-context reimplementations (confirmed by
# running skills end-to-end — the model fell back to `node -e` one-liners
# when python3 was missing, which works but isn't what the skills expect).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep git ca-certificates curl gnupg python3 python3-yaml \
    git-flow \
    && rm -rf /var/lib/apt/lists/*

# auto-changelog — scripts/main_tagging_release.py shells out to it during
# the git-flow release process (same as the release_3.py it replaces).
RUN npm install -g auto-changelog

# GitHub CLI (`gh`) — several skills (stable2-github-release-tagger,
# stable2-pr-labeler, jira-pr-lookup) shell out to `gh api`/`gh pr`/`gh
# release`. Official install steps: https://github.com/cli/cli/blob/trunk/docs/install_linux.md
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g opencode-ai@1.18.7

# git-flow/auto-changelog need a git commit identity to exist at all --
# without this every commit fails with "Author identity unknown" (found
# the hard way testing main_tagging_release.py). This default is
# deliberately an obvious, unfinished-looking placeholder (not a plain
# "Release Agent <release-agent@localhost>" that could pass for a real
# value) -- it should NEVER show up in a real commit. For local laptop
# testing, export GIT_AUTHOR_NAME/EMAIL and GIT_COMMITTER_NAME/EMAIL on
# your host before `docker compose up` (see docker-compose.yml) to use
# your own identity. Before any real/CNAP deployment, those same env
# vars MUST instead be set to a dedicated bot identity (e.g. "RDK
# Release Agent" with a real, monitored team address) -- NOT any
# individual's name: the portal's own login + audit log already records
# who triggered a run; git commit authorship should reflect what
# performed the action (the automation), not who asked for it. See
# docker-entrypoint.sh, which warns on startup if this placeholder is
# still active.
RUN git config --system user.name "RDK Release Agent (REPLACE BEFORE DEPLOY)" \
    && git config --system user.email "replace-me@example.com"

WORKDIR /workspace
COPY . .

RUN chmod +x docker-entrypoint.sh

# Credentials (Gerrit .netrc, ccp_jira.env, GitHub token) are mounted at
# runtime from CNAP secrets — never baked into the image. See DESIGN.md
# "Open items that block a real deployment".
EXPOSE 4096

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
