#!/usr/bin/env python3
"""Plan and locally probe immutable fork carry commit series."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "fork-carries.json"
CHECK_TIMEOUT_SECONDS = 900
OUTPUT_LIMIT = 16_384


def _load_manifest_module() -> Any:
    path = Path(__file__).with_name("fork_carry_manifest.py")
    spec = importlib.util.spec_from_file_location("fork_carry_manifest", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fork carry manifest validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def build_plan(manifest: Any, diagnostics: list[str]) -> dict[str, Any]:
    """Build a deterministic report without touching Git or running checks."""
    carries = manifest.get("carries", []) if isinstance(manifest, dict) else []
    ready: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for carry in carries if isinstance(carries, list) else []:
        if not isinstance(carry, dict) or carry.get("status") != "active":
            continue
        replay = carry.get("replay")
        if isinstance(replay, dict):
            ready.append(
                {
                    "order": carry.get("order"),
                    "id": carry.get("id"),
                    "source_ref": replay.get("source_ref"),
                    "base_commit": replay.get("base_commit"),
                    "commits": list(replay.get("commits", []))
                    if isinstance(replay.get("commits"), list)
                    else [],
                }
            )
        else:
            incomplete.append(carry.get("id"))
    return {
        "valid": not diagnostics,
        "diagnostics": list(diagnostics),
        "replay_ready_count": len(ready),
        "incomplete_active_count": len(incomplete),
        "replay_ready": ready,
        "incomplete_active": incomplete,
    }


def render_plan(report: dict[str, Any]) -> str:
    lines = [
        "# Fork Carry Replay Plan",
        "",
        f"- Valid manifest: `{'yes' if report['valid'] else 'no'}`",
        f"- Replay-ready active carries: `{report['replay_ready_count']}`",
        f"- Declaration-only active carries: `{report['incomplete_active_count']}`",
        "",
    ]
    for row in report["replay_ready"]:
        lines.extend(
            [
                f"## {row['order']}. `{row['id']}`",
                f"- Discovery ref: `{row['source_ref']}`",
                f"- Immutable base: `{row['base_commit']}`",
                f"- Immutable commits: {', '.join(f'`{item}`' for item in row['commits'])}",
                "",
            ]
        )
    if report["incomplete_active"]:
        lines.append("## Declaration-only / incomplete active carries")
        lines.extend(f"- `{item}`" for item in report["incomplete_active"])
        lines.append("")
    if report["diagnostics"]:
        lines.append("## Diagnostics")
        lines.extend(f"- {item}" for item in report["diagnostics"])
        lines.append("")
    return "\n".join(lines)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.longpaths=true", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def _bounded(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT] + "\n... output truncated ...\n"


def _resolve_commit(repo: Path, value: str) -> str | None:
    result = _run_git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def _conflict_paths(worktree: Path) -> list[str]:
    result = _run_git(worktree, "diff", "--name-only", "--diff-filter=U")
    return sorted(line for line in result.stdout.splitlines() if line)


def _run_checks(worktree: Path, checks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    reports: list[dict[str, Any]] = []
    all_ok = True
    for check in checks:
        env = os.environ.copy()
        env.update(check["env"])
        try:
            result = subprocess.run(
                check["argv"],
                cwd=worktree / check["cwd"],
                env=env,
                text=True,
                capture_output=True,
                shell=False,
                timeout=CHECK_TIMEOUT_SECONDS,
                check=False,
            )
            row = {
                "id": check["id"],
                "argv": list(check["argv"]),
                "cwd": check["cwd"],
                "returncode": result.returncode,
                "stdout": _bounded(result.stdout),
                "stderr": _bounded(result.stderr),
                "timed_out": False,
            }
            all_ok = all_ok and result.returncode == 0
        except subprocess.TimeoutExpired as exc:
            row = {
                "id": check["id"],
                "argv": list(check["argv"]),
                "cwd": check["cwd"],
                "returncode": None,
                "stdout": _bounded(exc.stdout or ""),
                "stderr": _bounded(exc.stderr or ""),
                "timed_out": True,
            }
            all_ok = False
        except OSError as exc:
            row = {
                "id": check["id"],
                "argv": list(check["argv"]),
                "cwd": check["cwd"],
                "returncode": None,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "timed_out": False,
            }
            all_ok = False
        reports.append(row)
        if not all_ok:
            break
    return reports, all_ok


def probe(
    repo: Path,
    manifest: dict[str, Any],
    carry_id: str,
    *,
    base_ref: str | None,
    scratch_root: Path | None,
    keep: bool,
    run_checks: bool,
) -> tuple[dict[str, Any], int]:
    carries = manifest["carries"]
    carry = next((item for item in carries if item.get("id") == carry_id), None)
    if carry is None:
        return {"status": "unknown_carry", "carry": carry_id}, 1
    replay = carry.get("replay")
    if not isinstance(replay, dict):
        return {"status": "not_replay_ready", "carry": carry_id}, 1

    explicit = base_ref is not None
    requested_base = base_ref if explicit else replay["base_commit"]
    resolved_base = _resolve_commit(repo, requested_base)
    if resolved_base is None:
        return {"status": "missing_object", "carry": carry_id, "object": requested_base}, 1
    for commit in replay["commits"]:
        if _resolve_commit(repo, commit) != commit:
            return {"status": "missing_object", "carry": carry_id, "object": commit}, 1
    if not explicit:
        parents = _run_git(repo, "rev-list", "--parents", "-n", "1", replay["commits"][0])
        fields = parents.stdout.split()
        first_parent = fields[1] if len(fields) > 1 else None
        if first_parent != replay["base_commit"]:
            return {
                "status": "base_mismatch",
                "carry": carry_id,
                "declared_base": replay["base_commit"],
                "first_parent": first_parent,
            }, 1

    root = scratch_root or Path(tempfile.gettempdir()) / "hermes-fork-carry-probes"
    root.mkdir(parents=True, exist_ok=True)
    container = Path(tempfile.mkdtemp(prefix=f"{carry_id}-", dir=root))
    worktree = container / "worktree"
    report: dict[str, Any] = {
        "status": "environment_error",
        "carry": carry_id,
        "base": resolved_base,
        "base_mode": "explicit" if explicit else "declared",
        "source_ref": replay["source_ref"],
        "commits": list(replay["commits"]),
        "applied_commits": [],
        "checks": [],
        "conflicts": [],
        "worktree": str(worktree),
        "worktree_removed": False,
    }
    added = False
    exit_code = 2
    try:
        add = _run_git(repo, "worktree", "add", "--detach", str(worktree), resolved_base)
        if add.returncode != 0:
            _run_git(repo, "worktree", "remove", "--force", str(worktree))
            report.update(status="environment_error", stderr=_bounded(add.stderr))
            return report, 2
        added = True
        for commit in replay["commits"]:
            picked = _run_git(worktree, "cherry-pick", commit)
            if picked.returncode != 0:
                report.update(
                    status="apply_failed",
                    failed_commit=commit,
                    conflicts=_conflict_paths(worktree),
                    stdout=_bounded(picked.stdout),
                    stderr=_bounded(picked.stderr),
                )
                exit_code = 1
                break
            report["applied_commits"].append(_run_git(worktree, "rev-parse", "HEAD").stdout.strip())
        else:
            if run_checks:
                check_reports, checks_ok = _run_checks(worktree, carry["checks"])
                report["checks"] = check_reports
                report["status"] = "success" if checks_ok else "check_failed"
                exit_code = 0 if checks_ok else 1
            else:
                report["status"] = "success"
                exit_code = 0
    finally:
        if not keep:
            if added:
                _run_git(worktree, "cherry-pick", "--abort")
                removed = _run_git(repo, "worktree", "remove", "--force", str(worktree))
                report["worktree_removed"] = removed.returncode == 0
            try:
                container.rmdir()
            except OSError:
                pass
    return report, exit_code


def _repo_root() -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local fork carry replay planning and probes")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    plan_parser.add_argument("--json", action="store_true", dest="json_output")
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    probe_parser.add_argument("--carry", required=True)
    probe_parser.add_argument("--base")
    probe_parser.add_argument("--scratch-root", type=Path)
    probe_parser.add_argument("--keep", action="store_true")
    probe_parser.add_argument("--run-checks", action="store_true")
    probe_parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    try:
        manifest_module = _load_manifest_module()
        manifest = manifest_module.load_manifest(args.manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"manifest unreadable or malformed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    repo = _repo_root()
    if repo is None:
        print("not inside a Git worktree", file=sys.stderr)
        return 2
    diagnostics = manifest_module.validate_manifest(manifest, repo)
    if args.command == "plan":
        report = build_plan(manifest, diagnostics)
        sys.stdout.write(_json_text(report) if args.json_output else render_plan(report))
        return 0 if not diagnostics else 1
    if diagnostics:
        report = {"status": "invalid_manifest", "valid": False, "diagnostics": diagnostics}
        sys.stdout.write(_json_text(report) if args.json_output else render_plan(build_plan(manifest, diagnostics)))
        return 1
    report, code = probe(
        repo,
        manifest,
        args.carry,
        base_ref=args.base,
        scratch_root=args.scratch_root,
        keep=args.keep,
        run_checks=args.run_checks,
    )
    if args.json_output:
        sys.stdout.write(_json_text(report))
    else:
        print(f"{report['carry']}: {report['status']}")
        if report.get("worktree"):
            print(f"worktree: {report['worktree']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
