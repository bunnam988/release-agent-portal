#!/usr/bin/env python3
"""
jira_rest.py — Shared Jira REST helper, replacing Jira MCP tool calls with
the ccp_jira.env service-account token (see DESIGN discussion: svc-autotriage
has CREATE_ISSUES/EDIT_ISSUES/LINK_ISSUE permissions, i.e. everything the
MCP connection was used for across every Jira-touching skill).

Mirrors jira-pr-lookup/scripts/fetch_prs.py's own credential-loading and
curl-based request style, generalized into reusable subcommands so every
other skill can shell out to one place instead of re-implementing this.

Credentials are loaded from ccp_jira.env in the current working directory
(or the environment). Required keys: JIRA_BASE_URL, JIRA_API_VERSION,
JIRA_USER, JIRA_TOKEN. Never hardcode credentials.

Usage:
    python3 jira_rest.py search "<jql>" [--max-results N] [--fields f1,f2,...]
    python3 jira_rest.py get-issue <KEY> [--fields f1,f2,...] [--expand changelog]
    python3 jira_rest.py get-comments <KEY>
    python3 jira_rest.py add-label <KEY> <label>
    python3 jira_rest.py remove-label <KEY> <label>
    python3 jira_rest.py add-link <FROM_KEY> <TO_KEY> <LINK_TYPE> [--direction outward|inward]
    python3 jira_rest.py create-issue <json-fields-file-or-'-' for stdin>
    python3 jira_rest.py update-issue <KEY> <json-fields-file-or-'-' for stdin>

All subcommands print a single JSON document to stdout on success (an
issue list for `search`, an issue object for `get-issue`, {"ok": true} for
label/link/update-issue mutations, {"key": "..."} for create-issue) and
exit non-zero with a JSON {"error": "..."} on failure -- keeps every caller's parsing
identical regardless of which subcommand it used.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config (same loading convention as fetch_prs.py, kept standalone so this
# has no import-time dependency on that script or vice versa)
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
    import os

    val = _env.get(key) or os.environ.get(key)
    if not val:
        _fail(
            f"Missing required config key: {key}. "
            f"Add it to ccp_jira.env or set it as an environment variable."
        )
    return val


JIRA_BASE_URL = _require("JIRA_BASE_URL").rstrip("/")
JIRA_API_VERSION = _require("JIRA_API_VERSION")
JIRA_USER = _require("JIRA_USER")
JIRA_TOKEN = _require("JIRA_TOKEN")

CREDS = f"{JIRA_USER}:{JIRA_TOKEN}"
JIRA_BASE = f"{JIRA_BASE_URL}/rest/api/{JIRA_API_VERSION}"

# Jira's own hard cap per search page regardless of what's requested --
# mirrors the "100-result API cap" MCP was observed auto-paginating past
# in a real run; this script must do the same to stay behaviorally
# equivalent, not just fetch page 1.
MAX_PAGE_SIZE = 100


def _fail(message, status=None):
    print(json.dumps({"error": message, "status": status}), file=sys.stderr)
    sys.exit(1)


def _curl_json(method, url, body=None):
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "--max-time", "30", "-u", CREDS, "-X", method]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        _fail(f"curl failed: {r.stderr.strip()}")
    *body_lines, status_line = r.stdout.rsplit("\n", 1)
    raw_body = "\n".join(body_lines) if body_lines else ""
    try:
        status = int(status_line)
    except ValueError:
        status = None
    parsed = None
    if raw_body.strip():
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            parsed = raw_body
    return status, parsed


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_search(jql, max_results, fields):
    """Auto-paginates past Jira's MAX_PAGE_SIZE-per-request cap, matching
    the behavior a real run observed from Jira MCP's search tool
    ("fetched 112 tickets, paginating past the 100-result API cap on its
    own") -- callers should never need to think about pagination
    themselves, same as before."""
    all_issues = []
    start_at = 0
    field_param = f"&fields={fields}" if fields else ""
    while True:
        page_size = min(MAX_PAGE_SIZE, max_results - len(all_issues)) if max_results else MAX_PAGE_SIZE
        if max_results and page_size <= 0:
            break
        url = (
            f"{JIRA_BASE}/search?jql={_urlencode(jql)}"
            f"&startAt={start_at}&maxResults={page_size}{field_param}"
        )
        status, data = _curl_json("GET", url)
        if status != 200 or not isinstance(data, dict):
            _fail(f"Jira search failed (HTTP {status}): {data}", status)
        issues = data.get("issues", [])
        all_issues.extend(issues)
        total = data.get("total", len(all_issues))
        start_at += len(issues)
        if not issues or start_at >= total or (max_results and len(all_issues) >= max_results):
            break
    if max_results:
        all_issues = all_issues[:max_results]
    print(json.dumps({"issues": all_issues, "total": len(all_issues)}))


def cmd_get_issue(key, fields, expand):
    params = []
    if fields:
        params.append(f"fields={fields}")
    if expand:
        # Passes straight through to Jira's own `expand` query param, e.g.
        # `--expand changelog` for the status-history data
        # stable2-status-evaluator's RM-Approved check needs (was
        # implicit/automatic via the MCP tool before; REST needs it
        # requested explicitly).
        params.append(f"expand={expand}")
    url = f"{JIRA_BASE}/issue/{key}" + ("?" + "&".join(params) if params else "")
    status, data = _curl_json("GET", url)
    if status != 200:
        _fail(f"Could not fetch {key} (HTTP {status}): {data}", status)
    print(json.dumps(data))


def cmd_get_comments(key):
    # Dedicated comments endpoint rather than fields=comment on get-issue:
    # the inline `fields=comment` on /issue only returns a limited recent
    # window by default, this returns the full paginated comment history
    # a couple of skills need to scan for dependency language.
    url = f"{JIRA_BASE}/issue/{key}/comment"
    status, data = _curl_json("GET", url)
    if status != 200:
        _fail(f"Could not fetch comments for {key} (HTTP {status}): {data}", status)
    print(json.dumps(data))


def cmd_add_label(key, label):
    url = f"{JIRA_BASE}/issue/{key}"
    status, data = _curl_json("PUT", url, {"update": {"labels": [{"add": label}]}})
    if status not in (200, 204):
        _fail(f"Could not add label {label!r} to {key} (HTTP {status}): {data}", status)
    print(json.dumps({"ok": True}))


def cmd_remove_label(key, label):
    url = f"{JIRA_BASE}/issue/{key}"
    status, data = _curl_json("PUT", url, {"update": {"labels": [{"remove": label}]}})
    if status not in (200, 204):
        _fail(f"Could not remove label {label!r} from {key} (HTTP {status}): {data}", status)
    print(json.dumps({"ok": True}))


def cmd_add_link(from_key, to_key, link_type, direction):
    # Jira's issueLink API is directional: "outward" means from_key ->
    # to_key reads as "{link_type}" (e.g. from_key "deploys" to_key);
    # "inward" is the reverse phrasing of the same link type.
    body = {"type": {"name": link_type}}
    if direction == "outward":
        body["inwardIssue"] = {"key": from_key}
        body["outwardIssue"] = {"key": to_key}
    else:
        body["outwardIssue"] = {"key": from_key}
        body["inwardIssue"] = {"key": to_key}
    url = f"{JIRA_BASE}/issueLink"
    status, data = _curl_json("POST", url, body)
    if status not in (200, 201):
        _fail(f"Could not link {from_key} -> {to_key} ({link_type}) (HTTP {status}): {data}", status)
    print(json.dumps({"ok": True}))


def cmd_create_issue(fields_source):
    raw = sys.stdin.read() if fields_source == "-" else Path(fields_source).read_text()
    try:
        fields = json.loads(raw)
    except json.JSONDecodeError as e:
        _fail(f"Invalid JSON fields payload: {e}")
    url = f"{JIRA_BASE}/issue"
    status, data = _curl_json("POST", url, {"fields": fields})
    if status not in (200, 201) or not isinstance(data, dict) or "key" not in data:
        _fail(f"Could not create issue (HTTP {status}): {data}", status)
    print(json.dumps({"key": data["key"]}))


def cmd_update_issue(key, fields_source):
    # Plain field overwrite (PUT .../issue/{key} {"fields": {...}}), not
    # the add-label/add-link commands' targeted "update" verb syntax --
    # this is for whole-field replacement (e.g. description), added for
    # mesh-release-ticket's release-table updates (see
    # .opencode/skills/mesh-release-ticket/SKILL.md), which need to
    # overwrite a field wholesale, not add/remove list entries.
    raw = sys.stdin.read() if fields_source == "-" else Path(fields_source).read_text()
    try:
        fields = json.loads(raw)
    except json.JSONDecodeError as e:
        _fail(f"Invalid JSON fields payload: {e}")
    url = f"{JIRA_BASE}/issue/{key}"
    status, data = _curl_json("PUT", url, {"fields": fields})
    if status not in (200, 204):
        _fail(f"Could not update {key} (HTTP {status}): {data}", status)
    print(json.dumps({"ok": True}))


def _urlencode(s):
    from urllib.parse import quote

    return quote(s, safe="")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search")
    s.add_argument("jql")
    s.add_argument("--max-results", type=int, default=0, help="0 = fetch all pages (default)")
    s.add_argument("--fields", default="", help="comma-separated field list, default = all")

    g = sub.add_parser("get-issue")
    g.add_argument("key")
    g.add_argument("--fields", default="")
    g.add_argument("--expand", default="", help="e.g. 'changelog' for status-history data")

    gc = sub.add_parser("get-comments")
    gc.add_argument("key")

    al = sub.add_parser("add-label")
    al.add_argument("key")
    al.add_argument("label")

    rl = sub.add_parser("remove-label")
    rl.add_argument("key")
    rl.add_argument("label")

    lk = sub.add_parser("add-link")
    lk.add_argument("from_key")
    lk.add_argument("to_key")
    lk.add_argument("link_type")
    lk.add_argument("--direction", choices=["outward", "inward"], default="outward")

    ci = sub.add_parser("create-issue")
    ci.add_argument("fields_source", help="path to a JSON file with issue fields, or '-' for stdin")

    ui = sub.add_parser("update-issue")
    ui.add_argument("key")
    ui.add_argument("fields_source", help="path to a JSON file with issue fields, or '-' for stdin")

    args = p.parse_args()

    if args.command == "search":
        cmd_search(args.jql, args.max_results, args.fields)
    elif args.command == "get-issue":
        cmd_get_issue(args.key, args.fields, args.expand)
    elif args.command == "get-comments":
        cmd_get_comments(args.key)
    elif args.command == "add-label":
        cmd_add_label(args.key, args.label)
    elif args.command == "remove-label":
        cmd_remove_label(args.key, args.label)
    elif args.command == "add-link":
        cmd_add_link(args.from_key, args.to_key, args.link_type, args.direction)
    elif args.command == "create-issue":
        cmd_create_issue(args.fields_source)
    elif args.command == "update-issue":
        cmd_update_issue(args.key, args.fields_source)


if __name__ == "__main__":
    main()
