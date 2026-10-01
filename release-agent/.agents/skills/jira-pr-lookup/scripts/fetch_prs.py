#!/usr/bin/env python3
"""
fetch_prs.py — Find all merged PRs to the develop branch for a Jira ticket.

Usage:
    python3 fetch_prs.py <TICKET-KEY>

Credentials are loaded from ccp_jira.env in the current working directory.
Required keys: JIRA_BASE_URL, JIRA_API_VERSION, JIRA_USER, JIRA_TOKEN
"""

import subprocess
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_env(path="ccp_jira.env"):
    env = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


_env = _load_env()


def _require(key):
    val = _env.get(key) or os.environ.get(key)
    if not val:
        raise EnvironmentError(
            f"Missing required config key: {key}\n"
            f"Add it to ccp_jira.env or set it as an environment variable."
        )
    return val


JIRA_BASE_URL = _require("JIRA_BASE_URL").rstrip("/")
JIRA_API_VERSION = _require("JIRA_API_VERSION")
JIRA_USER = _require("JIRA_USER")
JIRA_TOKEN = _require("JIRA_TOKEN")

CREDS = f"{JIRA_USER}:{JIRA_TOKEN}"
JIRA_BASE = f"{JIRA_BASE_URL}/rest/api/{JIRA_API_VERSION}"
DEV_STATUS_BASE = (
    f"{JIRA_BASE_URL}/rest/dev-status/1.0/issue/detail"
    f"?applicationType=githube&dataType=pullrequest"
)

# Link types to follow (hard functional dependencies only)
INCLUDE_LINK_TYPES = {
    "Dependency",
    "Block",
    "Depends on",
    "blocks",
    "is blocked by",
    "requires",
    "is required by",
}

MAX_DEPTH = 5
MAX_WORKERS = 10


# ---------------------------------------------------------------------------
# Jira API helpers
# ---------------------------------------------------------------------------


def get_issue_data(issue_key):
    """
    Fetch issue metadata: id, subtasks, and filtered issuelinks.

    Returns:
        (issue_id, subtasks, links, has_subtasks)
        - subtasks: list of (id, key, summary)
        - links: list of (id, key, summary, link_type, direction)
        - has_subtasks: bool
    """
    url = f"{JIRA_BASE}/issue/{issue_key}?fields=id,key,summary,subtasks,issuelinks"
    r = subprocess.run(
        ["curl", "-s", "--max-time", "15", "-u", CREDS, url],
        capture_output=True,
        text=True,
    )
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None, [], [], False

    if "fields" not in d:
        return None, [], [], False

    issue_id = d.get("id")
    raw_subtasks = d["fields"].get("subtasks", [])
    subtasks = [(s["id"], s["key"], s["fields"]["summary"]) for s in raw_subtasks]
    has_subtasks = len(subtasks) > 0

    links = []
    for link in d["fields"].get("issuelinks", []):
        lt = link["type"]["name"]
        if lt not in INCLUDE_LINK_TYPES:
            continue
        linked = link.get("inwardIssue") or link.get("outwardIssue")
        if linked:
            direction = "inward" if "inwardIssue" in link else "outward"
            links.append(
                (
                    linked["id"],
                    linked["key"],
                    linked["fields"]["summary"],
                    lt,
                    direction,
                )
            )

    return issue_id, subtasks, links, has_subtasks


def get_prs(issue_id):
    """
    Fetch all PRs associated with a Jira issue (by numeric ID).

    Returns:
        list of dicts with keys: status, url, title, author, base
    """
    url = f"{DEV_STATUS_BASE}&issueId={issue_id}"
    r = subprocess.run(
        ["curl", "-s", "--max-time", "15", "-u", CREDS, url],
        capture_output=True,
        text=True,
    )
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []

    results = []
    for detail in d.get("detail", []):
        for pr in detail.get("pullRequests", []):
            dest = pr.get("destination", {})
            branch = dest.get("branch", "N/A") if isinstance(dest, dict) else "N/A"
            results.append(
                {
                    "status": pr.get("status", ""),
                    "url": pr.get("url", ""),
                    "title": pr.get("name", ""),
                    "author": pr.get("author", {}).get("name", ""),
                    "base": branch,
                }
            )
    return results


# ---------------------------------------------------------------------------
# Graph traversal
# ---------------------------------------------------------------------------


def should_exclude_key(key):
    """Exclude CATR-* (QA/test tracking) tickets."""
    return key.startswith("CATR-")


def get_develop_prs(root_key):
    """
    BFS traversal from root_key. Follows subtasks unconditionally.
    Follows dependency links only when the linked ticket has NO subtasks
    (i.e., it is not itself a user story).

    Returns:
        Sorted list of PR dicts (merged, develop branch only), deduplicated by URL.
    """
    # visited: key -> (issue_id, how_reached, include_prs)
    visited = {}

    root_id, root_subtasks, root_links, _ = get_issue_data(root_key)
    if root_id is None:
        print(f"ERROR: Could not fetch root ticket {root_key}", file=sys.stderr)
        sys.exit(1)

    visited[root_key] = (root_id, "root", True)
    queue = []

    for nid, key, _ in root_subtasks:
        if key not in visited and not should_exclude_key(key):
            visited[key] = (nid, f"subtask of {root_key}", None)
            queue.append((nid, key, "subtask"))

    for nid, key, _, lt, direction in root_links:
        if key not in visited and not should_exclude_key(key):
            visited[key] = (nid, f"{lt}/{direction} from {root_key}", None)
            queue.append((nid, key, "link"))

    def fetch_data(item):
        nid, key, kind = item
        issue_id, subtasks, links, has_subtasks = get_issue_data(key)
        return key, nid, kind, subtasks, links, has_subtasks

    for depth in range(MAX_DEPTH):
        if not queue:
            break
        next_queue = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(fetch_data, item): item for item in queue}
            for f in as_completed(futures):
                key, nid, kind, subtasks, links, has_subtasks = f.result()
                old_id, old_how, _ = visited[key]

                if kind == "link" and has_subtasks:
                    visited[key] = (
                        old_id,
                        old_how + " [EXCLUDED: is user story]",
                        False,
                    )
                    continue

                visited[key] = (old_id, old_how, True)

                for snid, skey, _ in subtasks:
                    if skey not in visited and not should_exclude_key(skey):
                        visited[skey] = (snid, f"subtask of {key}", None)
                        next_queue.append((snid, skey, "subtask"))

                for lnid, lkey, _, lt, direction in links:
                    if lkey not in visited and not should_exclude_key(lkey):
                        visited[lkey] = (lnid, f"{lt}/{direction} from {key}", None)
                        next_queue.append((lnid, lkey, "link"))

        queue = next_queue

    # Collect PRs only from included tickets
    active = {k: (nid, how) for k, (nid, how, include) in visited.items() if include}

    seen_urls = {}

    def fetch_prs_for(item):
        key, nid, how = item
        return key, how, get_prs(nid)

    items = [(key, nid, how) for key, (nid, how) in active.items()]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_prs_for, item): item for item in items}
        for f in as_completed(futures):
            key, how, prs = f.result()
            for pr in prs:
                url = pr["url"]
                if pr["status"] == "MERGED" and url not in seen_urls:
                    seen_urls[url] = {**pr, "ticket": key, "how": how}

    return sorted(
        [p for p in seen_urls.values() if p["base"] == "develop"],
        key=lambda x: x["ticket"],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 fetch_prs.py <TICKET-KEY>", file=sys.stderr)
        sys.exit(1)

    root_key = sys.argv[1].upper()
    print(f"Fetching merged develop PRs for {root_key}...\n")

    prs = get_develop_prs(root_key)

    if not prs:
        print("No merged PRs found for develop branch.")
        return

    # Print table
    col_ticket = max(len("Ticket"), max(len(p["ticket"]) for p in prs))
    col_author = max(len("Author"), max(len(p["author"]) for p in prs))
    col_title = max(len("Title"), max(len(p["title"][:60]) for p in prs))

    header = (
        f"{'Ticket':<{col_ticket}}  "
        f"{'Author':<{col_author}}  "
        f"{'Title':<{col_title}}  URL"
    )
    print(header)
    print("-" * len(header))

    for p in prs:
        title = p["title"][:60]
        print(
            f"{p['ticket']:<{col_ticket}}  "
            f"{p['author']:<{col_author}}  "
            f"{title:<{col_title}}  {p['url']}"
        )

    print(f"\nTotal: {len(prs)} merged PR(s) to develop")


if __name__ == "__main__":
    main()
