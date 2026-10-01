#!/usr/bin/env python3
"""
main_tagging_release.py

Standalone, self-contained replacement for the old extract_list_2.py +
release_3.py pair. Combines both into one script and removes the
open_source_commits_monitor.xlsx dependency entirely:

  - Old flow: read xlsx -> filter rows with commits -> scrape GitHub's
    /tags HTML page for versions -> shell out to release_3.py per repo,
    which used a raw GITHUB_TOKEN env var for PR/release creation.
  - New flow: read config/tracked_repos.yaml (the same static, resolved
    repo list used by track-for-stable2) -> for each repo, compare
    `develop` against its latest semver tag via the GitHub API -> any
    repo with commits ahead of its latest tag needs tagging -> run the
    same git-flow release process, using the `gh` CLI throughout (no
    HTML scraping, no GITHUB_TOKEN env var — matches this repo's existing
    convention of using an already-authenticated `gh`/GitHub MCP instead
    of raw tokens).

Needs-tagging rule (no date range, no spreadsheet):
    A repo needs tagging if `develop` has at least one commit not
    reachable from its latest strict-semver tag (X.Y.Z). If a repo has
    no semver tag at all, it needs tagging with a default of 1.0.0.

Next-version rule (same as main-tagging's Step 10.1 / the original
extract_list_2.py compute_next_version):
    - Latest tag created in the current year AND month -> bump patch
    - Latest tag created in the current year, a different month -> bump
      minor, reset patch
    - Latest tag created in a previous year -> bump major, reset minor
      and patch
    - No existing semver tag -> 1.0.0

Usage:
    python3 scripts/main_tagging_release.py [OPTIONS]

Options:
  --tracked-repos FILE   Path to tracked_repos.yaml (default: config/tracked_repos.yaml)
  --work-dir DIR         Temp directory for git clones (default: /tmp/main_tagging_release)
  --dry-run              Preview only; no git/gh mutating commands run
  --repo URL             Narrow scope to one repo (org/repo or full GitHub URL)
  --repos URL,URL,...    Narrow scope to several repos (comma-separated org/repo or full
                         GitHub URLs) -- for picking a specific subset instead of either a
                         single repo (--repo) or every tracked repo (default)
  --test-repo [URL]      Restrict to the approved 6-repo fork allowlist (or one URL from it)
  --yes, -y              Skip the interactive confirmation prompt (non-interactive/agent use)

Requires: `gh` CLI already authenticated (gh auth status), `git`, `git-flow`,
`auto-changelog` on PATH. PyYAML for parsing tracked_repos.yaml.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


# ──────────────────────────────────────────────────────────────────────────────
# Shell helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run(cmd, cwd=None, env=None):
    """Run a command, returning (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def gh_json(*args):
    """Run `gh api ...` (or any gh subcommand) and parse JSON stdout."""
    rc, out, err = _run(["gh", *args])
    if rc != 0:
        return None, err
    try:
        return json.loads(out) if out else None, None
    except json.JSONDecodeError:
        return None, f"non-JSON output: {out[:200]}"


# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RepoPlan:
    owner_repo: str
    latest_tag: Optional[str]
    latest_tag_date: Optional[str]
    ahead_by: int          # real commits ahead, excluding release bookkeeping noise
    raw_ahead_by: int      # unfiltered GitHub compare() ahead_by, for visibility
    new_version: str
    needs_tagging: bool
    error: Optional[str] = None


@dataclass
class RepoResult:
    owner_repo: str
    version: str
    succeeded: bool = False
    pr_urls: list = field(default_factory=list)
    release_url: Optional[str] = None
    error: Optional[str] = None
    # True if the changelog commit failed (e.g. "nothing to commit" because
    # auto-changelog produced no delta) and was tolerated rather than
    # treated as fatal -- matches release_3.py's `|| true` on that one line.
    changelog_commit_skipped: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# Step 1 — Repo scope (replaces xlsx extraction)
# ──────────────────────────────────────────────────────────────────────────────

# Same 6-repo allowlist used by main-tagging, jira-pr-lookup,
# gerrit-cherrypick-squash, and cherry_pick_to_stable2.py's --test-repo
# mode -- kept identical so this script's test scope means the same thing
# everywhere else in this repo.
TEST_REPO_ALLOWLIST = [
    "https://github.com/Suganya-Sugumar/data-model-cli",
    "https://github.com/Suganya-Sugumar/test-and-diagnostic",
    "https://github.com/Suganya-Sugumar/xconf-client",
    "https://github.com/Suganya-Sugumar/moca-agent",
    "https://github.com/Suganya-Sugumar/utopia",
    "https://github.com/bunnam988/provisioning-and-management",
]


def load_tracked_repos(path, narrow_repo=None, narrow_repos=None):
    with open(path) as fh:
        data = yaml.safe_load(fh)
    urls = [r["url"] for r in data.get("repos", []) if r.get("url")]
    if narrow_repo:
        norm = narrow_repo.rstrip("/").replace("https://github.com/", "")
        urls = [u for u in urls if norm in u]
    elif narrow_repos:
        # Multiple explicit repos (e.g. from the portal's repo picker) --
        # keep tracked-repo order, not the order repos were passed in, so
        # output stays consistent with the "no narrowing" case.
        norms = {r.rstrip("/").replace("https://github.com/", "") for r in narrow_repos}
        urls = [u for u in urls if any(norm in u for norm in norms)]
    return urls


def resolve_test_repo_scope(test_repo_arg):
    """--test-repo (no value) -> all 6 allowlisted repos.
    --test-repo <url> -> just that one, must be allowlisted."""
    if test_repo_arg is True:
        return list(TEST_REPO_ALLOWLIST)
    url = test_repo_arg.rstrip("/")
    if url not in TEST_REPO_ALLOWLIST:
        print(f"ERROR: {url} is not one of the approved --test-repo URLs:")
        for u in TEST_REPO_ALLOWLIST:
            print(f"  {u}")
        sys.exit(1)
    return [url]


def owner_repo_from_url(url):
    parts = url.rstrip("/").replace("https://github.com/", "").split("/")
    return parts[0], parts[1]


# ──────────────────────────────────────────────────────────────────────────────
# Step 2 — Determine which repos need tagging (replaces HTML scraping)
# ──────────────────────────────────────────────────────────────────────────────

def latest_semver_tag(owner, repo):
    """Return (tag_name, iso_date, error) for the semantically latest tag
    matching ^X.Y.Z$ -- picked by comparing parsed (major, minor, patch)
    version numbers, not by commit date or GitHub API tag-list order --
    or (None, None, None) if no such tag exists.

    Previously this fetched every candidate tag's underlying commit (one
    extra GitHub API call each) and picked the one with the newest
    committer date, on the theory that this avoids a naive lexicographic
    string sort (where "2.9.0" > "2.10.0" as strings). That's true, but
    commit date was the wrong fix for it, and a real incident proved it:
    a transient GitHub API error on 2.10.1's commit lookup for
    rdkcentral/test-and-diagnostic silently dropped it from consideration
    (no retry, no warning) -- the script fell back to the older 2.10.0 as
    "latest", computed 2.10.1 as the next version, and collided with the
    2.10.1 release that already existed.

    Comparing version numbers directly is both the semantically correct
    notion of "latest release" (semver ordering is a property of the
    version number, not of when its commit happened to be authored) and
    removes that whole failure mode -- no extra API call, so nothing to
    silently drop. The one commit lookup still needed (for
    compute_next_version's year-based major-bump rule) now happens only
    for the already-determined winner, and a failure there is surfaced
    as a real per-repo error instead of quietly picking a different tag.
    """
    tags, err = gh_json("api", f"repos/{owner}/{repo}/tags", "--paginate")
    if err:
        return None, None, err
    candidates = [t for t in (tags or []) if SEMVER_PATTERN.match(t.get("name", ""))]
    if not candidates:
        return None, None, None

    def version_key(t):
        return tuple(int(part) for part in t["name"].split("."))

    winner = max(candidates, key=version_key)
    sha = winner["commit"]["sha"]
    commit, cerr = gh_json("api", f"repos/{owner}/{repo}/commits/{sha}")
    if cerr or not commit:
        return None, None, cerr or f"could not fetch commit {sha} for tag {winner['name']}"
    date = commit.get("commit", {}).get("committer", {}).get("date")
    if not date:
        return None, None, f"tag {winner['name']}'s commit has no committer date"
    return winner["name"], date, None


# Commits that `git flow release finish` itself creates when merging the
# release branch back into develop (changelog commit, the merge-back
# commit itself). These are real, unique commits by SHA -- they exist on
# develop and nowhere else -- so a plain ahead_by count treats every repo
# as "needing tagging" again immediately after it was just tagged, with
# zero actual new feature work. Filter them out before deciding.
_RELEASE_BOOKKEEPING_PATTERNS = [
    re.compile(r"^Merge tag '[^']+' into develop\b"),
    re.compile(r"^Merge branch 'release/[^']+'"),
    re.compile(r"^Add changelog for release\b"),
]


def _is_release_bookkeeping(commit_message):
    first_line = commit_message.splitlines()[0] if commit_message else ""
    return any(p.match(first_line) for p in _RELEASE_BOOKKEEPING_PATTERNS)


def commits_ahead_of_tag(owner, repo, tag, branch="develop"):
    """Real (non-release-bookkeeping) commit count on `branch` not
    reachable from `tag`, via the GitHub compare API (no cloning needed
    just to answer this). Returns (real_ahead, raw_ahead, error)."""
    result, err = gh_json("api", f"repos/{owner}/{repo}/compare/{tag}...{branch}")
    if err or result is None:
        return None, None, err
    raw_ahead = result.get("ahead_by", 0)
    commits = result.get("commits", [])
    real_commits = [c for c in commits if not _is_release_bookkeeping(c.get("commit", {}).get("message", ""))]
    return len(real_commits), raw_ahead, None


def compute_next_version(latest_tag, tag_date_str):
    if not latest_tag or not tag_date_str:
        return "1.0.0"
    major, minor, patch = map(int, latest_tag.split("."))
    commit_dt = datetime.fromisoformat(tag_date_str.replace("Z", "+00:00"))
    today = datetime.now(timezone.utc)

    if commit_dt.year == today.year and commit_dt.month == today.month:
        return f"{major}.{minor}.{patch + 1}"
    if commit_dt.year == today.year:
        return f"{major}.{minor + 1}.0"
    return f"{major + 1}.0.0"


def build_plan(owner_repo_urls):
    plans = []
    for url in owner_repo_urls:
        owner, repo = owner_repo_from_url(url)
        slug = f"{owner}/{repo}"
        print(f"  Checking {slug}…", end=" ", flush=True)

        tag, tag_date, err = latest_semver_tag(owner, repo)
        if err:
            print(f"ERROR ({err})")
            plans.append(RepoPlan(slug, None, None, 0, 0, "", False, error=err))
            continue

        if tag is None:
            # No semver tag at all: default to 1.0.0, always needs tagging.
            plans.append(RepoPlan(slug, None, None, 0, 0, "1.0.0", True))
            print("no existing tag -> 1.0.0")
            continue

        real_ahead, raw_ahead, err = commits_ahead_of_tag(owner, repo, tag)
        if err:
            print(f"ERROR ({err})")
            plans.append(RepoPlan(slug, tag, tag_date, 0, 0, "", False, error=err))
            continue

        needs = (real_ahead or 0) > 0
        new_version = compute_next_version(tag, tag_date) if needs else tag
        plans.append(RepoPlan(slug, tag, tag_date, real_ahead or 0, raw_ahead or 0, new_version, needs))
        noise = (raw_ahead or 0) - (real_ahead or 0)
        noise_note = f", {noise} release-bookkeeping" if noise else ""
        print(
            f"{tag} (+{real_ahead or 0} real{noise_note} on develop)"
            + (f" -> {new_version}" if needs else " up to date")
        )
    return plans


# ──────────────────────────────────────────────────────────────────────────────
# Step 3 — Release a single repo (replaces release_3.py)
# ──────────────────────────────────────────────────────────────────────────────

def release_repo(owner, repo, version, work_dir, dry_run):
    slug = f"{owner}/{repo}"
    clone_path = os.path.join(work_dir, repo)
    result = RepoResult(owner_repo=slug, version=version)

    if dry_run:
        print(f"  DRY-RUN: would clone, git-flow release {version}, open 2 PRs, "
              f"finish, push, and create a GitHub release for {slug}")
        result.succeeded = True
        return result

    if os.path.exists(clone_path):
        shutil.rmtree(clone_path)

    rc, _, err = _run(["gh", "repo", "clone", slug, clone_path])
    if rc != 0:
        result.error = f"clone failed: {err}"
        return result

    def step(cmd, **kw):
        return _run(cmd, cwd=clone_path, **kw)

    # `gh repo clone` auto-adds an `upstream` remote (and fetches its tags
    # into local refs) when the target is a fork. Those tags are from a
    # completely unrelated release history but still collide with
    # git-flow's local tag-existence check -- and removing the remote does
    # NOT remove refs already fetched from it. We only ever push to
    # `origin`, so drop the remote and clear all local tag refs (git-flow
    # will create the one new tag we actually want at `release finish`,
    # and it's the only one `git push --tags` will have left to push).
    # No-op for real (non-fork) rdkcentral repos, which never have an
    # `upstream` remote or pre-existing local tags after a fresh clone.
    step(["git", "remote", "remove", "upstream"])
    _, existing_tags, _ = step(["git", "tag", "-l"])
    if existing_tags:
        step(["git", "tag", "-d", *existing_tags.splitlines()])

    rc, _, err = step(["git", "checkout", "main"])
    if rc != 0:
        result.error = f"checkout main failed: {err}"
        return result

    rc, _, err = step(["git", "flow", "init", "-d"])
    if rc != 0:
        result.error = f"git flow init -d failed: {err}"
        return result
    rc, _, err = step(["git", "flow", "release", "start", version])
    if rc != 0:
        result.error = f"git flow release start failed: {err}"
        return result

    if not shutil.which("auto-changelog"):
        result.error = "auto-changelog not found on PATH"
        return result

    # Exact release_3.py sequence from here: auto-changelog -> git add ->
    # git commit. release_3.py appends `|| true` to the commit specifically
    # (and only that line) so that if auto-changelog produced no delta and
    # there's nothing to commit, the release still proceeds to `git flow
    # release publish` rather than stopping — deliberately replicated here,
    # not "fixed", since that script is the real, actually-used behavior.
    rc, _, err = step(["auto-changelog", "-v", version])
    if rc != 0:
        result.error = f"auto-changelog failed: {err}"
        return result
    step(["git", "add", "CHANGELOG.md"])
    rc, _, _ = step(["git", "commit", "-m", f"Add changelog for release {version}", "CHANGELOG.md"])
    if rc != 0:
        # Tolerated, matching `|| true` in release_3.py -- e.g. "nothing to
        # commit" when auto-changelog found no new entries for this repo.
        result.changelog_commit_skipped = True

    rc, _, err = step(["git", "flow", "release", "publish", version])
    if rc != 0:
        result.error = f"git flow release publish failed: {err}"
        return result

    # `gh pr create` has no --json output mode; it prints the created PR's
    # URL as the last line of stdout on success.
    result.pr_urls = []
    for base in ("main", "develop"):
        rc, out, err = _run([
            "gh", "pr", "create", "--repo", slug,
            "--base", base, "--head", f"release/{version}",
            "--title", f"Release {version}", "--body", f"Release {version}",
        ])
        if rc != 0:
            result.error = f"PR to {base} failed: {err}"
            return result
        if out:
            result.pr_urls.append(out.strip().splitlines()[-1])

    env = os.environ.copy()
    env["GIT_MERGE_AUTOEDIT"] = "no"
    rc, _, err = step(["git", "flow", "release", "finish", "-m", f"Release {version}", version], env=env)
    if rc != 0:
        result.error = f"git flow release finish failed: {err}"
        return result

    for push_cmd in (["git", "push", "origin", "main"], ["git", "push", "origin", "develop"], ["git", "push", "origin", "--tags"]):
        rc, _, err = step(push_cmd)
        if rc != 0:
            result.error = f"{' '.join(push_cmd)} failed: {err}"
            return result

    rc, out, err = _run([
        "gh", "release", "create", version, "--repo", slug,
        "--title", f"Release {version}", "--generate-notes",
    ])
    if rc != 0:
        result.error = f"gh release create failed: {err}"
        return result
    result.release_url = out.strip().splitlines()[-1] if out else None

    result.succeeded = True
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────

def print_plan(plans):
    print("\n" + "═" * 70)
    print("  MAIN TAGGING — PLAN (no xlsx, no date range)")
    print("═" * 70)
    needs = [p for p in plans if p.needs_tagging and not p.error]
    up_to_date = [p for p in plans if not p.needs_tagging and not p.error]
    errored = [p for p in plans if p.error]

    print(f"\nRepos checked        : {len(plans)}")
    print(f"Need tagging         : {len(needs)}")
    print(f"Up to date           : {len(up_to_date)}")
    print(f"Errors               : {len(errored)}\n")

    if needs:
        print("NEEDS TAGGING:")
        for p in needs:
            base = p.latest_tag or "(no tag)"
            print(f"  {p.owner_repo:<45} {base:>10} -> {p.new_version:<10} (+{p.ahead_by} commits)")
    if errored:
        print("\nERRORS:")
        for p in errored:
            print(f"  {p.owner_repo}: {p.error}")
    print()
    return needs


def print_report(results, dry_run):
    print("\n" + "═" * 70)
    print("  MAIN TAGGING — FINAL REPORT" + ("  (DRY-RUN)" if dry_run else ""))
    print("═" * 70)
    ok = [r for r in results if r.succeeded]
    failed = [r for r in results if not r.succeeded]
    print(f"\nSucceeded: {len(ok)}   Failed: {len(failed)}\n")
    for r in ok:
        flag = " (dry-run)" if dry_run else ""
        print(f"  ✅ {r.owner_repo} -> {r.version}{flag}")
        if r.changelog_commit_skipped:
            print(f"     (no changelog commit — auto-changelog produced no delta for this repo)")
        for pr in r.pr_urls:
            print(f"     PR: {pr}")
        if r.release_url:
            print(f"     Release: {r.release_url}")
    for r in failed:
        print(f"  ❌ {r.owner_repo}: {r.error}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Find repos needing a main-branch release tag and release them — no xlsx required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--tracked-repos", default="config/tracked_repos.yaml",
                    help="Path to tracked_repos.yaml (default: config/tracked_repos.yaml)")
    p.add_argument("--work-dir", default="/tmp/main_tagging_release",
                    help="Working directory for clones (default: /tmp/main_tagging_release)")
    p.add_argument("--dry-run", action="store_true", help="Preview only; no git/gh mutating commands")
    p.add_argument("--repo", default=None, help="Narrow to one repo (org/repo or full GitHub URL)")
    p.add_argument(
        "--repos", default=None,
        help="Narrow to several repos, comma-separated (org/repo or full GitHub URLs)",
    )
    p.add_argument(
        "--test-repo", nargs="?", const=True, default=False, metavar="URL",
        help="Restrict scope to the approved 6-repo fork allowlist (or one URL from it), "
             "ignoring --tracked-repos entirely",
    )
    p.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the interactive confirmation prompt (for non-interactive/agent-driven use). "
             "The caller is responsible for having shown the plan and gotten approval first "
             "-- e.g. via a prior --dry-run run.",
    )
    return p.parse_args()


def main():
    args = parse_args()

    rc, out, _ = _run(["gh", "auth", "status"])
    if rc != 0:
        print("ERROR: `gh` is not authenticated. Run `gh auth login` and retry.")
        sys.exit(1)

    if not args.dry_run:
        rc, _, _ = _run(["git", "flow", "version"])
        if rc != 0:
            print("ERROR: `git-flow` is not installed or not on PATH. Install it and retry.")
            sys.exit(1)

    if args.test_repo:
        print("╔" + "═" * 68 + "╗")
        print("║  TEST-REPO MODE".ljust(69) + "║")
        print("║  Scope is restricted to approved fork repo(s) only.".ljust(69) + "║")
        print("║  Real rdkcentral repos will NOT be touched.".ljust(69) + "║")
        print("╚" + "═" * 68 + "╝")
        urls = resolve_test_repo_scope(args.test_repo)
        source = "--test-repo allowlist"
    else:
        if args.repo and args.repos:
            print("ERROR: --repo and --repos are mutually exclusive -- use one or the other.")
            sys.exit(1)
        if not os.path.exists(args.tracked_repos):
            print(f"ERROR: tracked repos file not found: {args.tracked_repos}")
            sys.exit(1)
        narrow_repos = [r.strip() for r in args.repos.split(",") if r.strip()] if args.repos else None
        urls = load_tracked_repos(args.tracked_repos, narrow_repo=args.repo, narrow_repos=narrow_repos)
        source = args.tracked_repos

    if not urls:
        print("No repos in scope. Exiting.")
        sys.exit(0)

    print(f"Repo scope: {len(urls)} repo(s) from {source}")
    print("\nDetermining which repos need tagging (develop vs latest semver tag)…")
    plans = build_plan(urls)
    needs = print_plan(plans)

    if not needs:
        print("No repos need tagging. Exiting.")
        sys.exit(0)

    if not args.dry_run and not args.yes:
        answer = input(f"Release {len(needs)} repo(s) listed above? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted by user. No changes made.")
            sys.exit(0)

    os.makedirs(args.work_dir, exist_ok=True)
    results = []
    for p in needs:
        owner, repo = p.owner_repo.split("/")
        print(f"\n▸ Releasing {p.owner_repo} @ {p.new_version}")
        results.append(release_repo(owner, repo, p.new_version, args.work_dir, args.dry_run))

    print_report(results, args.dry_run)
    sys.exit(0 if all(r.succeeded for r in results) else 1)


if __name__ == "__main__":
    main()
