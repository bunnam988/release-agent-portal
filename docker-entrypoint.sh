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

# See deploy-credentials/README.md and cnap-webservice.yaml's
# serviceClaims block. Four tiers, highest priority first:
#   1. Runtime volume mount (local docker-compose.yml) -- always wins,
#      already in place by the time this script runs.
#   2. Comcast xVault, via CNAP's servicebinding.io mount (kept for
#      compatibility, but NOT what production actually uses right now
#      -- see tier 3's comment for why). CNAP mounts each key from that
#      Vault secret as its own file under
#      /bindings/release-agent-portal-files/<key>.
#   3. Comcast xVault, via a plain env var holding the WHOLE file's
#      content (the real production mechanism right now). CNAP's
#      WebService controller has a confirmed platform bug
#      (`_|_ // field not allowed (bottom kind)`, CNAP support
#      escalated) that fails EVERY serviceClaim the moment a WebService
#      has 2 or more of them -- a single claim works fine. So instead of
#      a second "release-agent-portal-files" / servicebinding.io claim,
#      these 4 files' full content now live as 4 extra keys (*_CONTENT)
#      inside the SAME xVault path as "release-agent-portal-config"
#      (single "env"-type claim already injects every key at that path
#      as an env var -- no manifest change needed when new keys are
#      added there). See cnap-webservice.yaml's serviceClaims comment.
#   4. Baked into the image at build time (deploy-credentials/ --
#      local-dev-only fallback).
# Placed BEFORE the git-identity check below, deliberately: that check
# reads GIT_AUTHOR_NAME, which only ever gets set by one of these tiers
# (xVault's "env"-type claim sets it directly as a container env var, no
# file/tier logic needed for that one -- see cnap-webservice.yaml) --
# checking it first would always see it unset and print a false warning
# even though the real value gets set moments later.
_maybe_use_credential() {
  bind_key="$1"
  content_env_var="$2"
  baked_in_file="$3"
  dest="$4"
  if [ -f "$dest" ]; then
    return 0
  fi
  xvault_src="/bindings/release-agent-portal-files/$bind_key"
  if [ -f "$xvault_src" ]; then
    mkdir -p "$(dirname "$dest")"
    cp "$xvault_src" "$dest"
    echo "Using xVault-bound (servicebinding.io mount) credential for $dest"
    return 0
  fi
  content_value="${!content_env_var:-}"
  if [ -n "$content_value" ]; then
    mkdir -p "$(dirname "$dest")"
    printf '%s' "$content_value" > "$dest"
    echo "Using xVault-bound (env var content) credential for $dest"
    return 0
  fi
  baked_in_src="/opt/deploy-credentials/$baked_in_file"
  if [ -f "$baked_in_src" ]; then
    mkdir -p "$(dirname "$dest")"
    cp "$baked_in_src" "$dest"
    echo "Using baked-in credential for $dest (no runtime mount or xVault binding provided one)"
  fi
}
_maybe_use_credential "ccp-jira-env" "CCP_JIRA_ENV_CONTENT" "ccp_jira.env" "/workspace/ccp_jira.env"
_maybe_use_credential "gerrit-netrc" "GERRIT_NETRC_CONTENT" "gerrit.netrc" "/root/.netrc"
_maybe_use_credential "gh-hosts-yml" "GH_HOSTS_YML_CONTENT" "gh-hosts.yml" "/root/.config/gh/hosts.yml"
_maybe_use_credential "opencode-auth-json" "OPENCODE_AUTH_JSON_CONTENT" "opencode-auth.json" "/root/.local/share/opencode/auth.json"

# Same priority as above for the portal config values (passwords,
# session secret, git identity, SAT client creds, mesh GitHub user) --
# only sets a var from the baked-in file if it isn't already set, so a
# real value always wins over the baked-in fallback. In production on
# CNAP these arrive as real env vars already, straight from xVault (see
# cnap-webservice.yaml's "release-agent-portal-config" serviceClaim,
# type: env) -- no file/tier logic needed for that case, this block is
# then simply a no-op since every one of these is already set by the
# time it runs. Only local docker-compose dev (or a CNAP deploy that
# hasn't been migrated to the xVault claim yet) actually falls through
# to the baked-in file below.
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

# Persist the top-level state files several skills read/write directly
# in /workspace's project root (last_run.txt, track_for_stable2_state.yaml,
# stable2_candidates.yaml, stable2_status_analysis.yaml, pr_list.yaml,
# stable2_release_tags.yaml, stable2_ready_tickets.txt -- see
# main-tagging/track-for-stable2/jira-pr-lookup/etc. SKILL.md files).
# Confirmed the hard way: unlike .stable2-meta-sync (already a named
# volume), these were never actually persisted -- a container
# recreation (image rebuild, or this script's own crash-loop giveup
# below) silently wiped an in-progress multi-component run's whole
# history/progress. Symlinking each known filename into the
# release-agent-run-state volume (see docker-compose.yml) makes every
# skill's existing hardcoded `/workspace/<file>` path keep working with
# zero changes to the skills themselves. Idempotent and safe to re-run
# on every start: an already-correct symlink is just overwritten with
# itself, and real pre-existing content (first run after this fix) is
# moved into the persisted store once rather than discarded.
mkdir -p /workspace/.run-state
for f in last_run.txt track_for_stable2_state.yaml stable2_candidates.yaml \
         stable2_status_analysis.yaml stable2_ready_tickets.txt pr_list.yaml \
         stable2_release_tags.yaml; do
  target="/workspace/.run-state/$f"
  link="/workspace/$f"
  if [ -e "$link" ] && [ ! -L "$link" ]; then
    mv "$link" "$target"
  fi
  [ -e "$target" ] || : > "$target"
  ln -sf "$target" "$link"
done

# Runs "$@" in a restart-with-backoff loop instead of letting one crash
# propagate -- see the call sites below. A single OOM-killed/crashed
# opencode-server (the heaviest, most crash-prone of the three
# processes, especially during a large multi-component run) used to
# tear down this *entire* container via `wait -n` below -- killing the
# FastAPI backend and every other user's in-flight session along with
# it, for one process's momentary failure. Restarting just the crashed
# process in place keeps the container (and everyone else's session)
# up; opencode's own session history lives in its SQLite DB on the
# opencode-auth volume regardless, so it survives this either way.
#
# Only gives up (returns non-zero, letting the caller treat it like a
# single unrecoverable process exit -- see the `wait -n` below) after
# MAX_FAILURES consecutive failures within FAILURE_WINDOW seconds: a
# real crash loop (bad config, corrupted install, a genuinely
# undersized memory limit) still surfaces as a full container
# restart/crash rather than retrying forever and hiding a real problem.
# `set -e` is intentionally not in effect for the `wait "$pid"` below
# (guarded by an `if`) -- letting it trip here would exit this whole
# subshell the first time the supervised process returns non-zero,
# which is the one outcome this function exists to handle instead of
# propagating.
supervise() {
  name="$1"; shift
  max_failures=5
  failure_window=120
  failures=0
  window_start=$(date +%s)
  shutting_down=0
  trap 'shutting_down=1; [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null; true' TERM INT
  while true; do
    "$@" &
    pid=$!
    if wait "$pid"; then
      code=0
    else
      code=$?
    fi
    pid=""
    [ "$shutting_down" = "1" ] && return 0
    now=$(date +%s)
    if [ $((now - window_start)) -gt "$failure_window" ]; then
      failures=0
      window_start=$now
    fi
    failures=$((failures + 1))
    echo "[$(date -u +%FT%TZ)] $name exited (code $code) -- restarting it ($failures/$max_failures within ${failure_window}s)" >&2
    if [ "$failures" -ge "$max_failures" ]; then
      echo "[$(date -u +%FT%TZ)] $name keeps crashing ($failures times within ${failure_window}s) -- giving up, this will restart the whole container" >&2
      return 1
    fi
    sleep $((failures > 10 ? 20 : failures * 2))
  done
}

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
supervise LLM_PROXY bash -c 'cd /app && exec uvicorn app.llm_token_proxy:app --host 127.0.0.1 --port 4097' &
SUP_LLM_PROXY_PID=$!

supervise OPENCODE opencode serve --hostname 127.0.0.1 --port 4096 &
SUP_OPENCODE_PID=$!

supervise UVICORN bash -c 'cd /app && exec uvicorn app.main:app --host 0.0.0.0 --port 8000' &
SUP_UVICORN_PID=$!

# Forward termination signals to all three supervisors (each forwards it
# on to whichever real child it currently has running -- see supervise's
# own trap above).
trap 'kill -TERM "$SUP_LLM_PROXY_PID" "$SUP_OPENCODE_PID" "$SUP_UVICORN_PID" 2>/dev/null || true' TERM INT

# A supervisor only returns (making `wait -n` here fire) on a graceful
# shutdown or once it's given up after repeated crash-looping -- a
# single transient crash is now retried in place above, never reaching
# this point at all.
wait -n "$SUP_LLM_PROXY_PID" "$SUP_OPENCODE_PID" "$SUP_UVICORN_PID"
EXIT_CODE=$?
kill -TERM "$SUP_LLM_PROXY_PID" "$SUP_OPENCODE_PID" "$SUP_UVICORN_PID" 2>/dev/null || true
wait
exit "$EXIT_CODE"
