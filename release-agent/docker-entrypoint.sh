#!/bin/sh
# `gh auth setup-git` configures git's credential.helper to use gh's stored
# token for github.com HTTPS operations. The token itself persists across
# rebuilds via the gh-auth named volume, but this credential.helper config
# lives in ~/.gitconfig, which is outside that volume -- so it silently
# disappears on every container recreate (found the hard way testing
# scripts/main_tagging_release.py: git push started failing with "could
# not read Username for 'https://github.com'" after an unrelated rebuild).
# Re-run it on every start; safe no-op if gh isn't authenticated yet.
gh auth setup-git 2>/dev/null || true

# Loud, hard-to-miss warning if the git commit identity is still the
# unconfigured placeholder (see Dockerfile / docker-compose.yml) -- easy
# to forget to set GIT_AUTHOR_NAME/EMAIL before a real deployment, and
# the consequence (a real repo's commit history showing "RDK Release
# Agent (REPLACE BEFORE DEPLOY)") is much harder to undo than avoid.
# Deliberately just a warning, not a hard failure: local testing without
# ever setting these should still work (git only needs *some* identity
# to exist, this placeholder satisfies that), per the explicit call to
# keep local usage working while this gets tightened up before a real
# deployment.
# GIT_AUTHOR_NAME (an env var) takes precedence over `git config
# user.name` for what a real commit actually uses -- but it does NOT
# change what `git config --get user.name` reports, since that only
# reads the static config value. Checking `git config --get user.name`
# alone would keep warning even after GIT_AUTHOR_NAME is correctly set.
# Mirror git's own actual precedence here: prefer the env var if set,
# fall back to git config otherwise.
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

exec "$@"
