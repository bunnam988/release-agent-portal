#!/usr/bin/env python3
"""
cherry_pick_to_stable2.py

Standalone script to cherry-pick RM-approved PR commits to support/stable2
across multiple GitHub repos.

Reads:
  - pr_list.yaml           : PRs with merge_commit_sha per Jira ticket
  - stable2_status_analysis.yaml : readiness status per Jira ticket

Processes only tickets where readiness == "READY".

Usage:
    python3 scripts/cherry_pick_to_stable2.py [OPTIONS]

Options:
  --pr-list FILE     Path to pr_list.yaml          (default: pr_list.yaml)
  --status  FILE     Path to stable2_status_analysis.yaml
                                                    (default: stable2_status_analysis.yaml)
  --work-dir DIR     Temp directory for git clones  (default: /tmp/stable2_cherrypick)
  --branch  NAME     Target branch                  (default: support/stable2)
  --dry-run          Preview actions; no git changes
  --no-push          Apply locally but do not push
  --repo    URL      Narrow scope to one repo  (org/repo or full GitHub URL)
  --commits SHAs     Comma-separated SHA overrides (requires --repo; skips YAML)

Examples:
  # Artifact-driven, dry-run
        python3 scripts/cherry_pick_to_stable2.py --dry-run

  # Single repo only
        python3 scripts/cherry_pick_to_stable2.py \\
      --repo rdkcentral/utopia

  # Manual: specify repo + SHAs directly (no YAML needed)
        python3 scripts/cherry_pick_to_stable2.py \\
      --repo https://github.com/rdkcentral/utopia \\
      --commits abc123,def456 --no-push
"""

import argparse
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# PyYAML is the only non-stdlib dependency
try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required.  Install with:  pip install pyyaml")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CommitEntry:
    ticket: str
    pr_number: int
    pr_url: str
    repo: str          # "org/repo" format, e.g. "rdkcentral/utopia"
    sha: str
    title: str = ""


@dataclass
class ConflictRecord:
    entry: CommitEntry
    unresolved_files: list
    auto_resolved_files: list
    raw_error: str


@dataclass
class RepoResult:
    repo_slug: str
    clone_url: str
    target_branch: str
    succeeded: list = field(default_factory=list)      # CommitEntry
    skipped: list = field(default_factory=list)        # CommitEntry (already present)
    conflicts: list = field(default_factory=list)      # ConflictRecord
    repo_error: Optional[str] = None                   # clone / checkout failure


# ──────────────────────────────────────────────────────────────────────────────
# Git helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run(cmd, cwd=None):
    """Run a command, returning (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _is_merge_commit(sha, cwd):
    rc, parents, _ = _run(
        ["git", "show", "--no-patch", "--format=%P", sha], cwd
    )
    return rc == 0 and len(parents.split()) > 1


def _already_in_branch(sha, cwd):
    """True if sha is reachable from HEAD (i.e. already cherry-picked)."""
    rc, _, _ = _run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd)
    return rc == 0


def _unmerged_files(cwd):
    rc, out, _ = _run(
        ["git", "diff", "--name-only", "--diff-filter=U"], cwd
    )
    return [f for f in out.splitlines() if f] if rc == 0 else []


# ──────────────────────────────────────────────────────────────────────────────
# Auto-resolution helpers
# ──────────────────────────────────────────────────────────────────────────────

# Files where we always keep our (target-branch) version because they are
# release-generated and differ legitimately between branches.
_ACCEPT_OURS = {
    "CHANGELOG.md", "ChangeLog", "NEWS", "AUTHORS",
}

# Files where we accept the incoming (cherry-pick) version.
_ACCEPT_THEIRS = {
    # nothing by default — expand as needed
}


def _auto_resolve(conflicted_files, cwd):
    """
    Attempt to auto-resolve trivial conflicts.
    Returns (auto_resolved, still_unresolved) — both lists of file paths.
    """
    auto_resolved = []
    still_unresolved = []

    for f in conflicted_files:
        base = os.path.basename(f)
        if base in _ACCEPT_OURS:
            rc, _, _ = _run(["git", "checkout", "--ours", "--", f], cwd)
            if rc == 0:
                _run(["git", "add", "--", f], cwd)
                auto_resolved.append(f)
                print(f"      auto-resolved (ours): {f}")
                continue
        elif base in _ACCEPT_THEIRS:
            rc, _, _ = _run(["git", "checkout", "--theirs", "--", f], cwd)
            if rc == 0:
                _run(["git", "add", "--", f], cwd)
                auto_resolved.append(f)
                print(f"      auto-resolved (theirs): {f}")
                continue
        still_unresolved.append(f)

    return auto_resolved, still_unresolved


# ──────────────────────────────────────────────────────────────────────────────
# YAML loaders
# ──────────────────────────────────────────────────────────────────────────────

def load_ready_tickets(path):
    with open(path) as fh:
        data = yaml.safe_load(fh)
    # Support both flat list and { tickets: [...] } / { jiras: [...] }
    rows = (
        data if isinstance(data, list)
        else data.get("tickets", data.get("jiras", []))
    )
    ready = set()
    for row in rows:
        # stable2_status_analysis.yaml uses 'key'; pr_list.yaml uses 'ticket'
        key = row.get("key") or row.get("ticket", "")
        if row.get("readiness") == "READY" and key:
            ready.add(key)
    return ready


def load_commits(pr_file, ready_tickets, narrow_repo=None):
    """
    Load CommitEntry objects for READY tickets from pr_list.yaml.
    narrow_repo filters by org/repo slug or full URL.
    """
    with open(pr_file) as fh:
        data = yaml.safe_load(fh)
    prs = data.get("prs", data) if isinstance(data, dict) else data

    entries = []
    for pr in prs:
        ticket = pr.get("ticket", "")
        if ticket not in ready_tickets:
            continue
        sha = pr.get("merge_commit_sha") or pr.get("sha", "")
        if not sha:
            continue
        repo = pr.get("repo", "")   # expected: "org/repo"
        if narrow_repo:
            norm = narrow_repo.rstrip("/").replace("https://github.com/", "")
            if norm != repo and norm not in repo:
                continue
        entries.append(CommitEntry(
            ticket=ticket,
            pr_number=int(pr.get("number", 0)),
            pr_url=pr.get("url", ""),
            repo=repo,
            sha=sha,
            title=pr.get("title", ""),
        ))
    # Sort oldest-first per repo by PR number so cherry-picks are sequential
    entries.sort(key=lambda e: (e.repo, e.pr_number))
    return entries


# ──────────────────────────────────────────────────────────────────────────────
# Per-repo cherry-pick orchestration
# ──────────────────────────────────────────────────────────────────────────────

def process_repo(clone_url, commits, target_branch, work_dir, dry_run, no_push):
    repo_slug = clone_url.rstrip("/").split("/")[-1].replace(".git", "")
    clone_path = os.path.join(work_dir, repo_slug)
    result = RepoResult(
        repo_slug=repo_slug,
        clone_url=clone_url,
        target_branch=target_branch,
    )

    SEP = "─" * 60
    print(f"\n{SEP}")
    print(f"Repo   : {clone_url}")
    print(f"Branch : {target_branch}")
    print(f"Commits: {len(commits)}")

    # ── Clone ────────────────────────────────────────────────────
    if os.path.exists(clone_path):
        shutil.rmtree(clone_path)
    print("  Cloning…", end=" ", flush=True)
    rc, _, err = _run(["git", "clone", clone_url, clone_path])
    if rc != 0:
        result.repo_error = f"clone failed: {err}"
        print(f"FAILED\n  {err}")
        return result
    print("OK")

    _run(["git", "fetch", "--all", "--tags", "--quiet"], clone_path)

    # ── Checkout target branch ───────────────────────────────────
    # Try remote branch first; fall back to creating from main then develop
    for ref in [f"origin/{target_branch}", "origin/main", "origin/develop"]:
        rc, _, _ = _run(
            ["git", "checkout", "-B", target_branch, ref], clone_path
        )
        if rc == 0:
            print(f"  Checked out {target_branch} from {ref}")
            break
    else:
        result.repo_error = f"Cannot checkout/create {target_branch}"
        print(f"  ERROR: {result.repo_error}")
        return result

    # ── Cherry-pick each commit ──────────────────────────────────
    for entry in commits:
        sha = entry.sha
        label = f"PR#{entry.pr_number} {sha[:10]}  [{entry.ticket}]  {entry.title[:55]}"
        print(f"\n  ▸ {label}")

        if dry_run:
            print(f"    DRY-RUN: would cherry-pick {sha}")
            result.succeeded.append(entry)
            continue

        # Check if already present
        if _already_in_branch(sha, clone_path):
            print(f"    SKIP: already in {target_branch}")
            result.skipped.append(entry)
            continue

        cp_cmd = ["git", "cherry-pick", "-x"]
        if _is_merge_commit(sha, clone_path):
            cp_cmd += ["-m", "1"]
        cp_cmd.append(sha)

        rc, out, err = _run(cp_cmd, clone_path)
        if rc == 0:
            print(f"    OK")
            result.succeeded.append(entry)
            continue

        # ── Conflict handling ────────────────────────────────────
        conflicted = _unmerged_files(clone_path)
        print(f"    CONFLICT: {len(conflicted)} file(s): {conflicted}")

        auto_ok, still_bad = _auto_resolve(conflicted, clone_path)

        if not still_bad:
            # All conflicts resolved; continue the cherry-pick
            rc2, _, err2 = _run(
                ["git", "cherry-pick", "--continue", "--no-edit"],
                clone_path,
            )
            if rc2 == 0:
                print(f"    OK (auto-resolved {len(auto_ok)} file(s))")
                result.succeeded.append(entry)
                continue
            else:
                # Continue itself failed (e.g. empty commit); absorb empty
                if "nothing to commit" in err2.lower() or "empty" in err2.lower():
                    _run(["git", "cherry-pick", "--skip"], clone_path)
                    print(f"    OK (empty — skipped)")
                    result.succeeded.append(entry)
                    continue
                still_bad = _unmerged_files(clone_path) or ["(unknown — see error)"]

        # Abort and record for manual resolution
        _run(["git", "cherry-pick", "--abort"], clone_path)
        result.conflicts.append(ConflictRecord(
            entry=entry,
            unresolved_files=still_bad,
            auto_resolved_files=auto_ok,
            raw_error=err.strip(),
        ))
        print(f"    FAILED — {len(still_bad)} file(s) need manual fix")

    # ── Push ─────────────────────────────────────────────────────
    if not dry_run and not no_push and result.succeeded:
        print(f"\n  Pushing {target_branch}…", end=" ", flush=True)
        rc, _, err = _run(["git", "push", "origin", target_branch], clone_path)
        if rc != 0:
            result.repo_error = f"push failed: {err}"
            print(f"FAILED\n  {err}")
        else:
            print("OK")
    elif dry_run:
        print(f"\n  DRY-RUN: push skipped")
    elif no_push:
        print(f"\n  --no-push: local commits ready in {clone_path}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Final report
# ──────────────────────────────────────────────────────────────────────────────

def print_report(results, dry_run, target_branch):
    SEP1 = "═" * 65
    SEP2 = "─" * 65
    print(f"\n{SEP1}")
    print("  CHERRY-PICK TO STABLE2 — FINAL REPORT")
    if dry_run:
        print("  (DRY-RUN: no changes were applied)")
    print(f"{SEP1}\n")

    total_ok = sum(len(r.succeeded) for r in results)
    total_skip = sum(len(r.skipped) for r in results)
    total_conflict = sum(len(r.conflicts) for r in results)
    total_err = sum(1 for r in results if r.repo_error)

    print(f"Repos processed        : {len(results)}")
    print(f"Commits succeeded      : {total_ok}")
    print(f"Commits skipped        : {total_skip}  (already in {target_branch})")
    print(f"Commits need manual fix: {total_conflict}")
    print(f"Repo-level errors      : {total_err}")
    print()

    # ── Successes ────────────────────────────────────────────────
    success_repos = [r for r in results if r.succeeded]
    if success_repos:
        print("✅  SUCCEEDED")
        for r in success_repos:
            print(f"    {r.repo_slug}  →  {r.target_branch}")
            for e in r.succeeded:
                flag = " (dry-run)" if dry_run else ""
                print(f"       {e.sha[:12]}  PR#{e.pr_number:<5}  [{e.ticket}]  "
                      f"{e.title[:55]}{flag}")
        print()

    # ── Skipped ──────────────────────────────────────────────────
    skip_repos = [r for r in results if r.skipped]
    if skip_repos:
        print(f"⏭   ALREADY IN {target_branch}")
        for r in skip_repos:
            print(f"    {r.repo_slug}")
            for e in r.skipped:
                print(f"       {e.sha[:12]}  PR#{e.pr_number:<5}  [{e.ticket}]")
        print()

    # ── Conflicts ────────────────────────────────────────────────
    conflict_repos = [r for r in results if r.conflicts]
    if conflict_repos:
        print(SEP2)
        print("⚠️   CONFLICTS REQUIRING MANUAL RESOLUTION")
        print(SEP2)
        for r in conflict_repos:
            for cr in r.conflicts:
                e = cr.entry
                print(f"""
Repo    : {r.clone_url}
Commit  : {e.sha}
PR      : {e.pr_url}
Ticket  : {e.ticket}
Title   : {e.title}

Auto-resolved ({len(cr.auto_resolved_files)}) : {cr.auto_resolved_files or 'none'}
Still unresolved ({len(cr.unresolved_files)}) :""")
                for f in cr.unresolved_files:
                    print(f"    - {f}")
                print(f"""
── Steps to resolve manually ──
  git clone {r.clone_url} /tmp/fix_{r.repo_slug}
  cd /tmp/fix_{r.repo_slug}
  git fetch origin
  git checkout -B {r.target_branch} origin/{r.target_branch}
  git cherry-pick -x {e.sha}
  # Edit and resolve: {', '.join(cr.unresolved_files)}
  git add {' '.join(cr.unresolved_files)}
  git cherry-pick --continue
  git push origin {r.target_branch}""")
        print()

    # ── Repo errors ──────────────────────────────────────────────
    err_repos = [r for r in results if r.repo_error]
    if err_repos:
        print(SEP2)
        print("❌  REPO-LEVEL ERRORS")
        print(SEP2)
        for r in err_repos:
            print(f"  {r.repo_slug}: {r.repo_error}")
        print()

    print(SEP1)
    ok = total_conflict == 0 and total_err == 0
    status = "✅  ALL DONE" if ok else "⚠️   COMPLETED WITH ISSUES"
    print(f"  {status}")
    print(f"{SEP1}\n")
    return ok


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Cherry-pick RM-approved PR commits to support/stable2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--pr-list", default="pr_list.yaml",
                   help="Path to pr_list.yaml (default: pr_list.yaml)")
    p.add_argument("--status", default="stable2_status_analysis.yaml",
                   help="Path to stable2_status_analysis.yaml")
    p.add_argument("--work-dir", default="/tmp/stable2_cherrypick",
                   help="Working directory for clones (default: /tmp/stable2_cherrypick)")
    p.add_argument("--branch", default="support/stable2",
                   help="Target branch (default: support/stable2)")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview; no git changes made")
    p.add_argument("--no-push", action="store_true",
                   help="Apply locally but do not push")
    p.add_argument("--repo", default=None,
                   help="Narrow to one repo (org/repo or full GitHub URL)")
    p.add_argument("--commits", default=None,
                   help="Comma-separated SHA list; bypasses YAML (requires --repo)")
    return p.parse_args()


def main():
    args = parse_args()

    if args.commits and not args.repo:
        print("ERROR: --commits requires --repo")
        sys.exit(1)

    os.makedirs(args.work_dir, exist_ok=True)

    # ── Build CommitEntry list ────────────────────────────────────
    if args.commits:
        # Manual override mode — no YAML needed
        shas = [s.strip() for s in args.commits.split(",") if s.strip()]
        slug = args.repo.rstrip("/").replace("https://github.com/", "")
        entries = [
            CommitEntry(ticket="MANUAL", pr_number=0, pr_url="",
                        repo=slug, sha=sha, title="(manual)")
            for sha in shas
        ]
        print(f"Manual mode: {len(entries)} commit(s) for {slug}")
    else:
        # Artifact-driven mode
        for path, label in [(args.pr_list, "--pr-list"), (args.status, "--status")]:
            if not os.path.exists(path):
                print(f"ERROR: file not found ({label}): {path}")
                sys.exit(1)

        ready = load_ready_tickets(args.status)
        if not ready:
            print("No READY tickets found in stable2_status_analysis.yaml. Exiting.")
            sys.exit(0)
        print(f"READY tickets : {sorted(ready)}")

        entries = load_commits(args.pr_list, ready, narrow_repo=args.repo)
        if not entries:
            scope = f" for {args.repo}" if args.repo else ""
            print(f"No matching commits found{scope}. Exiting.")
            sys.exit(0)

    print(f"Commits found : {len(entries)}")
    if args.dry_run:
        print("Mode          : DRY-RUN (no changes will be made)\n")
    elif args.no_push:
        print("Mode          : APPLY (no push)\n")
    else:
        print(f"Mode          : APPLY + PUSH → {args.branch}\n")

    # ── Group by repo and process ─────────────────────────────────
    by_repo = defaultdict(list)
    for e in entries:
        by_repo[e.repo].append(e)

    results = []
    for repo_slug, commits in by_repo.items():
        # Build clone URL: if slug already looks like a full URL, use as-is
        if repo_slug.startswith("http"):
            clone_url = repo_slug
        else:
            clone_url = f"https://github.com/{repo_slug}.git"

        result = process_repo(
            clone_url=clone_url,
            commits=commits,
            target_branch=args.branch,
            work_dir=args.work_dir,
            dry_run=args.dry_run,
            no_push=args.no_push,
        )
        results.append(result)

    ok = print_report(results, args.dry_run, args.branch)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
