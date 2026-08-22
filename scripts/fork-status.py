#!/usr/bin/env python3
"""Read-only status report for the Axiom Hermes fork.

This helper intentionally does not update, merge, checkout, reset, install, restart,
or push anything. Use it before touching the deploy branch or when reconciling docs.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[1]
SHARED_CRON = Path.home() / ".hermes" / "cron" / "jobs.json"
SENTINEL_CRON = Path.home() / ".hermes" / "profiles" / "sentinel" / "cron" / "jobs.json"
DESKTOP_ALIAS = "AXIOM-DESKTOP"
DESKTOP_REPO_PS = "$env:LOCALAPPDATA\\hermes\\hermes-agent"


def run(cmd: list[str], *, cwd: Path = REPO, timeout: int = 30) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip()


def git(*args: str, timeout: int = 30) -> str:
    code, out = run(["git", *args], timeout=timeout)
    return out if code == 0 else f"ERROR[{code}]: {out}"


def git_ok(*args: str, timeout: int = 30) -> bool:
    code, _ = run(["git", *args], timeout=timeout)
    return code == 0


def count_lr(spec: str) -> str:
    return git("rev-list", "--left-right", "--count", spec)


def short_ref(ref: str) -> str:
    return git("rev-parse", "--short=12", ref)


def branch() -> str:
    return git("branch", "--show-current")


def dirty_files() -> list[str]:
    out = git("status", "--short")
    return [line for line in out.splitlines() if line.strip()]


def remote_urls() -> dict[str, str]:
    out = git("remote", "-v")
    remotes: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "(fetch)":
            remotes[parts[0]] = parts[1]
    return remotes


def cron_job_state(*, names: set[str], scripts: set[str]) -> dict[str, object]:
    errors: list[str] = []
    stores = (SHARED_CRON, SENTINEL_CRON)

    for store in stores:
        if not store.exists():
            continue
        try:
            data = json.loads(store.read_text())
        except Exception as exc:  # pragma: no cover - defensive helper
            errors.append(f"{store}: {type(exc).__name__}: {exc}")

            continue

        for job in data.get("jobs", []):
            if job.get("name") not in names and job.get("script") not in scripts:
                continue

            return {
                "found": True,
                "path": str(store),
                "id": job.get("id"),
                "name": job.get("name"),
                "owner_profile": job.get("owner_profile"),
                "enabled": job.get("enabled"),
                "state": job.get("state"),
                "paused_at": job.get("paused_at"),
                "last_run_at": job.get("last_run_at"),
                "last_status": job.get("last_status"),
                "schedule": job.get("schedule_display") or job.get("schedule"),
                "deliver": job.get("deliver"),
            }

    result: dict[str, object] = {"found": False, "paths": [str(store) for store in stores]}
    if errors:
        result["errors"] = errors

    return result


def sentinel_sync_state() -> dict[str, object]:
    """Compatibility key for the optional mutation-capable sync job."""
    return cron_job_state(names={"Hermes Axiom Sync"}, scripts={"sentinel-hermes-axiom-sync.py"})


def drift_watch_state() -> dict[str, object]:
    return cron_job_state(names={"Hermes Daily Check"}, scripts=set())


def desktop_status(timeout: int) -> dict[str, object]:
    ssh = shutil.which("ssh")
    if not ssh:
        return {"checked": False, "reason": "ssh not installed"}
    ps = (
        "$ErrorActionPreference = 'Stop'; "
        f"$repo = {DESKTOP_REPO_PS}; "
        "Write-Output ('host=' + $env:COMPUTERNAME); "
        "Write-Output ('repo=' + $repo); "
        "if (-not (Test-Path $repo)) { Write-Output 'status=missing'; exit 0 }; "
        "Set-Location $repo; "
        "Write-Output ('status=' + ((git status --short --branch) -join ' | ')); "
        "Write-Output ('branch=' + (git branch --show-current)); "
        "Write-Output ('head=' + (git rev-parse --short=12 HEAD)); "
        "Write-Output ('origin_axiom=' + (git rev-parse --short=12 origin/axiom)); "
        "Write-Output ('upstream_main=' + (git rev-parse --short=12 upstream/main)); "
        "Write-Output ('head_vs_origin=' + (git rev-list --left-right --count HEAD...origin/axiom)); "
        "Write-Output ('origin_vs_upstream=' + (git rev-list --left-right --count origin/axiom...upstream/main));"
    )
    code, out = run(
        [ssh, "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", DESKTOP_ALIAS, "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        cwd=REPO,
        timeout=timeout + 5,
    )
    if code != 0:
        return {"checked": True, "reachable": False, "alias": DESKTOP_ALIAS, "error": out}
    parsed: dict[str, object] = {"checked": True, "reachable": True, "alias": DESKTOP_ALIAS}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key.strip()] = value.strip()
    return parsed


def print_markdown(report: dict[str, object]) -> None:
    print("# Axiom Hermes Fork Status")
    print()
    print(f"- Host: `{report['host']}`")
    print(f"- Repo: `{report['repo']}`")
    print(f"- Branch: `{report['branch']}`")
    print(f"- HEAD: `{report['head']}`")
    print(f"- `origin/axiom`: `{report['origin_axiom']}`")
    print(f"- `upstream/main`: `{report['upstream_main']}`")
    print(f"- `main`: `{report['main']}`")
    print(f"- `axiom...origin/axiom`: `{report['axiom_vs_origin']}`")
    print(f"- `origin/axiom...upstream/main`: `{report['origin_vs_upstream']}`")
    print(f"- `main...upstream/main`: `{report['main_vs_upstream']}`")
    print(f"- `origin/axiom` contains `upstream/main`: `{report['origin_contains_upstream']}`")
    print(f"- local `HEAD` contains `upstream/main`: `{report['head_contains_upstream']}`")
    dirty = report["dirty"]
    if dirty:
        print("- Dirty working tree:")
        for line in dirty:  # type: ignore[assignment]
            print(f"  - `{line}`")
    else:
        print("- Dirty working tree: `no`")
    print()
    print("## Remotes")
    for name, url in sorted(report["remotes"].items()):  # type: ignore[union-attr]
        print(f"- `{name}` → `{url}`")
    print()
    for heading, key, capability in (
        ("Upstream drift detection", "drift_watch", "read-only detection/reporting"),
        ("Automatic upstream reconciliation", "sentinel_sync", "mutation-capable sync"),
    ):
        print(f"## {heading}")
        job = report[key]
        if isinstance(job, dict) and job.get("found"):
            print(f"- Capability: `{capability}`")
            print(f"- Job: `{job.get('name')}` / `{job.get('id')}`")
            print(f"- Owner: `{job.get('owner_profile')}`")
            print(f"- Enabled: `{job.get('enabled')}`")
            print(f"- State: `{job.get('state')}`")
            print(f"- Schedule: `{job.get('schedule')}`")
            print(f"- Paused at: `{job.get('paused_at')}`")
            print(f"- Last run: `{job.get('last_run_at')}` / `{job.get('last_status')}`")
        else:
            print(f"- Capability: `{capability}`")
            print("- Job: `not configured`")
        print()
    print("## Axiom-Desktop")
    desktop = report.get("desktop")
    if not desktop:
        print("- Not checked. Pass `--desktop` to attempt read-only SSH verification.")
    elif isinstance(desktop, dict):
        for key, value in desktop.items():
            print(f"- `{key}`: `{value}`")


def build_report(*, include_desktop: bool, desktop_timeout: int) -> dict[str, object]:
    return {
        "host": socket.gethostname(),
        "repo": str(REPO),
        "branch": branch(),
        "head": short_ref("HEAD"),
        "origin_axiom": short_ref("origin/axiom"),
        "upstream_main": short_ref("upstream/main"),
        "main": short_ref("main"),
        "axiom_vs_origin": count_lr("axiom...origin/axiom"),
        "origin_vs_upstream": count_lr("origin/axiom...upstream/main"),
        "main_vs_upstream": count_lr("main...upstream/main"),
        "origin_contains_upstream": git_ok("merge-base", "--is-ancestor", "upstream/main", "origin/axiom"),
        "head_contains_upstream": git_ok("merge-base", "--is-ancestor", "upstream/main", "HEAD"),
        "dirty": dirty_files(),
        "remotes": remote_urls(),
        "drift_watch": drift_watch_state(),
        "sentinel_sync": sentinel_sync_state(),
        "desktop": desktop_status(desktop_timeout) if include_desktop else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Axiom Hermes fork status report")
    parser.add_argument("--fetch", action="store_true", help="Fetch origin/upstream before reporting; still no checkout/merge/push")
    parser.add_argument("--desktop", action="store_true", help="Attempt read-only SSH status check of Axiom-Desktop")
    parser.add_argument("--desktop-timeout", type=int, default=8, help="SSH connect timeout for --desktop")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown")
    args = parser.parse_args()

    if args.fetch:
        for remote in ("origin", "upstream"):
            code, out = run(["git", "fetch", remote, "--prune", "--tags"], timeout=180)
            if code != 0:
                print(f"fetch {remote} failed:\n{out}")
                return code

    report = build_report(include_desktop=args.desktop, desktop_timeout=args.desktop_timeout)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_markdown(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
