#!/bin/bash
# Single-container entrypoint: runs opencode-server and the FastAPI
# backend as two processes in the same container (see Dockerfile), so
# this matches the one-container-per-app CNAP pattern used elsewhere on
# this team instead of needing a separate WebService + internal-only
# routing for opencode-server.
#
# opencode-server binds 127.0.0.1 only -- it is never a separate network
# endpoint even inside the container's own namespace, only reachable via
# localhost from the backend process started below (see
# backend/app/config.py's opencode_url default). This is deliberate: it
# holds every real credential (GitHub token, Gerrit .netrc, ccp_jira.env)
# and has no auth of its own.
set -e

cd /workspace

# `gh auth setup-git` configures git's credential.helper to use gh's
# stored token for github.com HTTPS operations. Re-run on every start;
# safe no-op if gh isn't authenticated yet. See the original
# release-agent/docker-entrypoint.sh this was merged from for the fuller
# explanation of why this specifically needs re-running every start.
gh auth setup-git 2>/dev/null || true

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

# On CNAP: these 4 files arrive via a WebService `serviceClaims` entry
# of type `servicebinding.io` (see cnap-webservice.yaml + cnap-secret.yaml),
# which mounts each key of a Secret as a file under
# /bindings/<claim-name>/<key> -- the standard servicebinding.io
# convention this type name follows. Copy them into the paths this app
# actually expects. Locally (docker-compose.yml), that /bindings path
# never exists at all, so this loop is a silent no-op and the existing
# runtime volume mounts are the only thing that ever provides these --
# completely unaffected either way.
_maybe_use_bound_credential() {
  src="/bindings/creds-files/$1"
  dest="$2"
  if [ -f "$src" ] && [ ! -f "$dest" ]; then
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    echo "Using CNAP-bound credential for $dest"
  fi
}
_maybe_use_bound_credential "ccp-jira-env" "/workspace/ccp_jira.env"
_maybe_use_bound_credential "gerrit-netrc" "/root/.netrc"
_maybe_use_bound_credential "gh-hosts-yml" "/root/.config/gh/hosts.yml"
_maybe_use_bound_credential "opencode-auth-json" "/root/.local/share/opencode/auth.json"

opencode serve --hostname 127.0.0.1 --port 4096 &
OPENCODE_PID=$!

cd /app
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
UVICORN_PID=$!

# Forward termination signals to both children instead of only killing
# whichever one happens to be PID 1.
trap 'kill -TERM "$OPENCODE_PID" "$UVICORN_PID" 2>/dev/null || true' TERM INT

# If either process exits -- crash or otherwise -- stop the other and
# exit the container. A k8s liveness/startup probe hitting a
# half-broken container (one process silently dead) is worse than the
# whole pod restarting cleanly.
wait -n "$OPENCODE_PID" "$UVICORN_PID"
EXIT_CODE=$?
kill -TERM "$OPENCODE_PID" "$UVICORN_PID" 2>/dev/null || true
wait
exit "$EXIT_CODE"
