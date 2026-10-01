#!/usr/bin/env python3
"""
Resolve Yocto SRCREV values for a selected repo subset using git ls-remote.

This intentionally keeps the same core resolution method as update_srcrev.py:
- no cloning of component repos
- use git ls-remote against exact refs
- resolve tags first, then exact branch ref
- no silent fallback to main/master
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

DEFAULT_REPOS: Dict[str, str] = {
    "SRCREV_ccsp_adv_security": "https://github.com/rdkcentral/advanced-security.git",
    "SRCREV_ccsp_cm_agent": "https://github.com/rdkcentral/cable-modem-agent.git",
    "SRCREV_ccsp_common_library": "https://github.com/rdkcentral/common-library.git",
    "SRCREV_ccsp_cr": "https://github.com/rdkcentral/component-registry.git",
    "SRCREV_ccsp_dmcli": "https://github.com/rdkcentral/data-model-cli.git",
    "SRCREV_ccsp_eth_agent": "https://github.com/rdkcentral/ethernet-agent.git",
    "SRCREV_ccsp_gwprovapp": "https://github.com/rdkcentral/gw-provisioning-application.git",
    "SRCREV_ccsp_gwprovapp_ethwan": "https://github.com/rdkcentral/gw-provisioning-ethernet-wan.git",
    "SRCREV_ccsp_home_security": "https://github.com/rdkcentral/home-security.git",
    "SRCREV_ccsp_hotspot": "https://github.com/rdkcentral/hotspot.git",
    "SRCREV_ccsp_hotspot_kmod": "https://github.com/rdkcentral/mtu-modifier.git",
    "SRCREV_ccsp_lm_lite": "https://github.com/rdkcentral/lan-manager-lite.git",
    "SRCREV_ccsp_misc": "https://github.com/rdkcentral/miscellaneous-broadband.git",
    "SRCREV_ccsp_moca": "https://github.com/rdkcentral/moca-agent.git",
    "SRCREV_ccsp_mta_agent": "https://github.com/rdkcentral/media-terminal-adapter-agent.git",
    "SRCREV_ccsp_p_and_m": "https://github.com/rdkcentral/provisioning-and-management.git",
    "SRCREV_ccsp_psm": "https://github.com/rdkcentral/persistent-storage-manager.git",
    "SRCREV_ccsp_snmp_pa": "https://github.com/rdkcentral/snmp-protocol-agent.git",
    "SRCREV_ccsp_tr069_pa": "https://github.com/rdkcentral/tr069-protocol-agent.git",
    "SRCREV_ccsp_webui_bci_jst": "https://github.com/rdkcentral/webui-bwg.git",
    "SRCREV_ccsp_webui_bci_php": "https://github.com/rdkcentral/webui-bwg.git",
    "SRCREV_ccsp_webui_jst": "https://github.com/rdkcentral/webui.git",
    "SRCREV_ccsp_webui_php": "https://github.com/rdkcentral/webui.git",
    "SRCREV_ccsp_xconf": "https://github.com/rdkcentral/xconf-client.git",
    "SRCREV_ccsp_xdns": "https://github.com/rdkcentral/xdns.git",
    "SRCREV_core_net_lib": "https://github.com/rdkcentral/core-net-library.git",
    "SRCREV_halinterface": "https://github.com/rdkcentral/halinterface.git",
    "SRCREV_hardware_abstraction_layer": "https://github.com/rdkcentral/hardware-abstraction-layer.git",
    "SRCREV_harvester": "https://github.com/rdkcentral/harvester.git",
    "SRCREV_json_hal_lib": "https://github.com/rdkcentral/json-hal-library.git",
    "SRCREV_jst": "https://github.com/rdkcentral/javascript-templates.git",
    "SRCREV_lanmanager": "https://github.com/rdkcentral/lan-manager.git",
    "SRCREV_notify_comp": "https://github.com/rdkcentral/notify-component.git",
    "SRCREV_ovs_agent": "https://github.com/rdkcentral/open-virtual-switch-agent.git",
    "SRCREV_powermgr": "https://github.com/rdkcentral/power-manager.git",
    "SRCREV_rdk_fwupgrade_manager": "https://github.com/rdkcentral/platform-manager.git",
    "SRCREV_rdk_cellularmanager": "https://github.com/rdkcentral/cellular-manager.git",
    "SRCREV_start_parodus": "https://github.com/rdkcentral/start-parodus.git",
    "SRCREV_test_and_diagnostic": "https://github.com/rdkcentral/test-and-diagnostic.git",
    "SRCREV_utopia": "https://github.com/rdkcentral/utopia.git",
    "SRCREV_sysint-broadband": "https://github.com/rdkcentral/sysint-broadband.git",
    "SRCREV_ccsp-dhcp-mgr": "https://github.com/rdkcentral/dhcp-manager.git",
}


def which_git() -> str:
    path = shutil.which("git")
    if not path:
        print("ERROR: `git` not found in PATH.", file=sys.stderr)
        sys.exit(2)
    return path


def run_cmd(cmd: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out"


def normalize_repo(value: str) -> str:
    entry = value.strip()
    if entry.startswith("https://github.com/"):
        return entry.removesuffix(".git").rstrip("/")
    if entry.startswith("git@github.com:"):
        return "https://github.com/" + entry.split(":", 1)[1].removesuffix(".git")
    return "https://github.com/" + entry.removesuffix(".git").strip("/")


def repo_basename(value: str) -> str:
    normalized = normalize_repo(value)
    return normalized.rstrip("/").split("/")[-1]


def ls_remote_single(repo_url: str, pattern: str, timeout: int) -> Optional[str]:
    git = which_git()
    code, out, _ = run_cmd([git, "ls-remote", "--exit-code", "-q", repo_url, pattern], timeout=timeout)
    if code == 0 and out:
        return out.split("\t", 1)[0]
    return None


def resolve_commit_for_ref(repo_url: str, ref: str, timeout: int = 20) -> Tuple[Optional[str], str]:
    for pattern in (f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}"):
        sha = ls_remote_single(repo_url, pattern, timeout)
        if sha:
            return sha, "tag"

    sha = ls_remote_single(repo_url, f"refs/heads/{ref}", timeout)
    if sha:
        return sha, "branch"

    return None, "missing"


def component_from_var(var_name: str) -> str:
    component = var_name.replace("SRCREV_", "")
    component = component.replace("_", "-")
    return component


def format_as_yocto(mapping: Dict[str, str]) -> str:
    lines: List[str] = []
    for var in DEFAULT_REPOS.keys():
        if var in mapping:
            lines.append(f'{var} = "{mapping[var]}"')
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve SRCREV SHAs for selected repos using git ls-remote.")
    parser.add_argument("--ref", required=True, help="Exact branch or tag to resolve.")
    parser.add_argument("--repo", action="append", required=True, help="Repo scope entry: owner/repo or full GitHub URL. Repeatable.")
    parser.add_argument("--format", choices=["json", "yocto"], default="json")
    parser.add_argument("--summary-json", help="Optional path to write structured JSON summary.")
    args = parser.parse_args()

    requested = {normalize_repo(value) for value in args.repo}

    selected: Dict[str, str] = {}
    missing_scope: List[str] = []
    for normalized in sorted(requested):
        matches = [(var, url) for var, url in DEFAULT_REPOS.items() if normalize_repo(url) == normalized]
        if not matches:
            requested_base = repo_basename(normalized)
            matches = [
                (var, url)
                for var, url in DEFAULT_REPOS.items()
                if repo_basename(url) == requested_base
            ]
        if not matches:
            missing_scope.append(normalized)
            continue
        for var, url in matches:
            selected[var] = url

    if missing_scope:
        print("Repo(s) not found in built-in SRCREV mapping:", file=sys.stderr)
        for repo in missing_scope:
            print(f"  - {repo}", file=sys.stderr)

    if not selected:
        print("ERROR: No requested repos matched the built-in SRCREV mapping.", file=sys.stderr)
        sys.exit(1)

    resolved: Dict[str, str] = {}
    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 5)) as executor:
        future_map = {
            executor.submit(resolve_commit_for_ref, url, args.ref, 20): (var, url)
            for var, url in selected.items()
        }
        for future in as_completed(future_map):
            var, url = future_map[future]
            try:
                sha, resolved_as = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                errors.append({"var": var, "repo_url": url, "error": str(exc)})
                continue

            result = {
                "var": var,
                "component": component_from_var(var),
                "repo_url": normalize_repo(url),
                "requested_ref": args.ref,
                "resolved_as": resolved_as,
                "sha": sha,
            }
            results.append(result)
            if sha:
                resolved[var] = sha
            else:
                errors.append({
                    "var": var,
                    "repo_url": normalize_repo(url),
                    "error": f"ref '{args.ref}' not found",
                })

    payload = {
        "requested_ref": args.ref,
        "resolved_count": len(resolved),
        "results": sorted(results, key=lambda item: item["var"]),
        "errors": errors,
    }

    if args.summary_json:
        with open(args.summary_json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    if args.format == "yocto":
        sys.stdout.write(format_as_yocto(resolved))
    else:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")

    if errors:
        sys.exit(3)


if __name__ == "__main__":
    main()
