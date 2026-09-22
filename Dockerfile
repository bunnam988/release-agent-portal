# Single-container build: opencode-server (running release-agent's
# skills/agents) + the FastAPI backend + the built React frontend, all in
# one image -- matches the one-container-per-app CNAP pattern this team
# already uses elsewhere, instead of splitting into 3 containers that
# need internal-only routing worked out between them. See DESIGN.md.

# ── Stage 1: build the React frontend ──────────────────────────────────
FROM node:22-slim AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ── Stage 2: final image ───────────────────────────────────────────────
FROM node:22-slim

# ripgrep is a required opencode dependency (used by its search tools).
# python3 + python3-yaml: release-agent's scripts/*.py are real Python
# scripts the skills shell out to, not just LLM-in-context
# reimplementations. python3-pip: needed for the FastAPI backend's own
# requirements.txt below (release-agent's own scripts never needed pip,
# just python3-yaml).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep git ca-certificates curl gnupg python3 python3-yaml python3-pip \
    git-flow \
    && rm -rf /var/lib/apt/lists/*

# auto-changelog — release-agent/scripts/main_tagging_release.py shells
# out to it during the git-flow release process.
RUN npm install -g auto-changelog

# GitHub CLI (`gh`) — several release-agent skills shell out to
# `gh api`/`gh pr`/`gh release`.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g opencode-ai@1.18.7

# git-flow/auto-changelog need a git commit identity to exist at all --
# without this every commit fails with "Author identity unknown". This
# default is deliberately an obvious, unfinished-looking placeholder --
# see docker-entrypoint.sh, which warns on startup if it's still active.
# Override with GIT_AUTHOR_NAME/EMAIL + GIT_COMMITTER_NAME/EMAIL (see
# docker-compose.yml) for a real identity in a given deployment.
RUN git config --system user.name "RDK Release Agent (REPLACE BEFORE DEPLOY)" \
    && git config --system user.email "replace-me@example.com"

# release-agent's skills/agents/scripts (the git submodule at ./release-agent)
WORKDIR /workspace
COPY release-agent/ .

# FastAPI backend
WORKDIR /app
COPY backend/requirements.txt .
# --break-system-packages: this container IS the app, not a shared
# system -- Debian's PEP 668 protection has nothing to protect here.
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt
COPY backend/app ./app

# Built frontend, served by FastAPI as static files -- see
# backend/app/main.py's _FRONTEND_DIST block, which only activates when
# this directory actually exists (absent in local `uvicorn` dev without
# this Docker build).
COPY --from=frontend-build /app/dist /app/frontend/dist

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000
# opencode-server's own port (4096) is intentionally NOT exposed here --
# it's bound to 127.0.0.1 by docker-entrypoint.sh and only ever reached
# via localhost from the backend process in this same container, never
# a separate network endpoint. It holds every real credential.

# Mirrors the /api/health check docker-compose.yml and the CNAP
# WebService manifest both use -- gives plain `docker run`/`docker ps`
# visibility into container health too, not just k8s-level probes.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
