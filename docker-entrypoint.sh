#!/bin/bash
# Single-container entrypoint: runs opencode-server, the FastAPI
# backend, and the LLM token-injection proxy as three processes in the
# same container (see Dockerfile), matching the one-container-per-app
# CNAP pattern used elsewhere on this team instead of needing separate
# WebServices + internal-only routing between them.
#
# opencode-server and the LLM proxy both bind 127.0.0.1 only -- neither
# is ever a separate network endpoint even inside the container's own
# namespace, only reachable via localhost from the processes started
# below (see backend/app/config.py's opencode_url default, and
# opencode.json's provider.rdkb-release-agent.options.baseURL). This is
# deliberate: opencode holds every real credential (GitHub token, Gerrit
# .netrc, ccp_jira.env) and the proxy holds the LLM gateway's bearer
# token -- neither has any auth of its own beyond that.
set -e

cd /workspace

# `gh auth setup-git` configures git's credential.helper to use gh's
# stored token for github.com HTTPS operations. Re-run on every start;
# safe no-op if gh isn't authenticated yet. See the original
# release-agent/docker-entrypoint.sh this was merged from for the fuller
# explanation of why this specifically needs re-running every start.
gh auth setup-git 2>/dev/null || true

# See deploy-credentials/README.md. Two tiers, highest priority first:
#   1. Runtime volume mount (local docker-compose.yml) -- always wins,
#      already in place by the time this script runs.
#   2. Baked into the image at build time (this deployment's approach on
#      CNAP -- see DESIGN.md "Credential status" for the accepted
#      trade-off vs. a real CNAP secret mechanism).
# Placed BEFORE the git-identity check below, deliberately: that check
# reads GIT_AUTHOR_NAME, which for this deployment approach only ever
# gets set by config.env sourced here -- checking it first would always
# see it unset and print a false warning even though the real value
# gets set moments later.
_maybe_use_baked_credential() {
  src="/opt/deploy-credentials/$1"
  dest="$2"
  if [ -f "$src" ] && [ ! -f "$dest" ]; then
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    echo "Using baked-in credential for $dest (no runtime mount provided one)"
  fi
}
_maybe_use_baked_credential "ccp_jira.env" "/workspace/ccp_jira.env"
_maybe_use_baked_credential "gerrit.netrc" "/root/.netrc"
_maybe_use_baked_credential "gh-hosts.yml" "/root/.config/gh/hosts.yml"
_maybe_use_baked_credential "opencode-auth.json" "/root/.local/share/opencode/auth.json"

# Same two-tier priority for the portal config values (passwords,
# session secret, git identity) -- only sets a var from the baked-in
# file if it isn't already set (e.g. by a real env var some other way),
# so a real value always wins over the baked-in one.
if [ -f /opt/deploy-credentials/config.env ]; then
  set -a
  # shellcheck disable=SC1091
  . <(grep -v '^\s*#' /opt/deploy-credentials/config.env | while IFS='=' read -r k v; do
        [ -z "$k" ] && continue
        eval "current=\${$k:-}"
        [ -z "$current" ] && echo "$k=$v"
      done)
  set +a
fi

# Loud warning if the git commit identity is still the unconfigured
# placeholder (see release-agent/Dockerfile / docker-compose.yml).
#
# GIT_AUTHOR_NAME (an env var) takes precedence over `git config
# user.name` for what a real commit actually uses -- but it does NOT
# change what `git config --get user.name` reports, since that only
# reads the static config value. Checking `git config --get user.name`
# alone would keep warning even after GIT_AUTHOR_NAME is correctly set
# (caught this the hard way: warning fired despite GIT_AUTHOR_NAME being
# visibly set in `env` output). Mirror git's own actual precedence here:
# prefer the env var if set, fall back to git config otherwise.
current_name="${GIT_AUTHOR_NAME:-$(git config --get user.name 2>/dev/null || true)}"
case "$current_name" in
  *"REPLACE BEFORE DEPLOY"*)
    echo "=================================================================="
    echo "WARNING: git commit identity is still the unconfigured placeholder"
    echo "  ($current_name)"
    echo "Any commit made right now will show this, not a real identity."
    echo "Set GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL / GIT_COMMITTER_NAME /"
    echo "GIT_COMMITTER_EMAIL (see docker-compose.yml) before real use."
    echo "=================================================================="
    ;;
esac

# LLM token-injection proxy -- see backend/app/llm_token_proxy.py for
# why this exists (opencode reads its provider apiKey once at startup,
# but the Flow gateway's SAT token expires every 24h; this proxy
# refreshes it lazily on every request instead of needing opencode
# itself to restart). Started before opencode so it's already up by the
# time any real model request happens.
#
# IMPORTANT: run this and uvicorn in a subshell with their own `cd`,
# not the top-level shell's -- `cd /app` here previously changed the
# *whole script's* working directory for everything after it,
# including opencode serve below, which silently made opencode look
# for /app/opencode.json (doesn't exist) instead of the real
# /workspace/opencode.json this entire deployment's provider config
# lives in. Confirmed via opencode's own logs (`directory=/app`) after
# a real provider silently never loading at all despite the config file
# itself being completely correct.
(cd /app && exec uvicorn app.llm_token_proxy:app --host 127.0.0.1 --port 4097) &
LLM_PROXY_PID=$!

opencode serve --hostname 127.0.0.1 --port 4096 &
OPENCODE_PID=$!

(cd /app && exec uvicorn app.main:app --host 0.0.0.0 --port 8000) &
UVICORN_PID=$!

# Forward termination signals to all three children instead of only
# killing whichever one happens to be PID 1.
trap 'kill -TERM "$LLM_PROXY_PID" "$OPENCODE_PID" "$UVICORN_PID" 2>/dev/null || true' TERM INT

# If any one process exits -- crash or otherwise -- stop the others and
# exit the container. A k8s liveness/startup probe hitting a
# half-broken container (one process silently dead) is worse than the
# whole pod restarting cleanly.
wait -n "$LLM_PROXY_PID" "$OPENCODE_PID" "$UVICORN_PID"
EXIT_CODE=$?
kill -TERM "$LLM_PROXY_PID" "$OPENCODE_PID" "$UVICORN_PID" 2>/dev/null || true
wait
exit "$EXIT_CODE"
