"""Detached, non-deploying Axiom carry-stack reconciliation worker."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

OUTPUT_LIMIT = 16_384
CHECK_TIMEOUT_SECONDS = 1_800


def _run(
    repo: Path,
    *args: str,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.longpaths=true", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=env,
    )


def _bounded(value: str) -> str:
    if len(value) <= OUTPUT_LIMIT:
        return value
    return value[:OUTPUT_LIMIT] + "\n... output truncated ...\n"


def _resolve(repo: Path, ref: str) -> str:
    result = _run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) >= 40 else ""


def _source_commit_env(repo: Path, commit: str) -> dict[str, str]:
    result = _run(repo, "show", "-s", "--format=%cI", commit)
    commit_date = result.stdout.strip()
    if result.returncode != 0 or not commit_date:
        raise RuntimeError(f"cannot read deterministic committer date for {commit}")
    return {
        **os.environ,
        "GIT_COMMITTER_NAME": "Axiom Carry Replay",
        "GIT_COMMITTER_EMAIL": "axiom-carry-replay@localhost",
        "GIT_COMMITTER_DATE": commit_date,
    }


def _load_manifest(
    repo: Path,
    *,
    manifest_path: Path | None = None,
    validator_path: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    validator_path = validator_path or (repo / "scripts" / "fork_carry_manifest.py")
    manifest_path = manifest_path or (repo / "fork-carries.json")
    spec = importlib.util.spec_from_file_location("fork_carry_manifest", validator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fork carry manifest validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.load_manifest(manifest_path)
    diagnostics = module.validate_manifest(manifest, repo)
    return manifest, diagnostics


def _owned_paths(manifest: dict[str, Any]) -> set[str]:
    owned: set[str] = set()
    for carry in manifest.get("carries", []):
        if not isinstance(carry, dict) or carry.get("status") != "active":
            continue
        for field in ("paths", "tests", "references"):
            for value in carry.get(field, []):
                if isinstance(value, str) and value.strip():
                    owned.add(value.replace("\\", "/").rstrip("/"))
        contract = carry.get("contract")
        if isinstance(contract, dict) and isinstance(contract.get("path"), str):
            owned.add(contract["path"].replace("\\", "/").rstrip("/"))
    return owned


def candidate_path_ownership_diagnostics(
    manifest: dict[str, Any], changed_paths: list[str]
) -> list[str]:
    """Reject candidate deltas that cannot be attributed to an active carry."""
    owned = _owned_paths(manifest)
    diagnostics: list[str] = []
    for raw_path in sorted(set(changed_paths)):
        path = raw_path.replace("\\", "/").strip("/")
        if not any(path == item or path.startswith(f"{item}/") for item in owned):
            diagnostics.append(f"unowned candidate path: {path}")
    return diagnostics


def _active_replay_carries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    active = [
        carry
        for carry in manifest.get("carries", [])
        if isinstance(carry, dict) and carry.get("status") == "active"
    ]
    return sorted(active, key=lambda carry: int(carry.get("order", 0)))


def _replay_digest(carries: list[dict[str, Any]]) -> str:
    specification = [
        {
            "id": str(carry.get("id") or ""),
            "commits": [str(commit) for commit in (carry.get("replay") or {}).get("commits", [])],
        }
        for carry in carries
    ]
    encoded = json.dumps(
        specification, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _deduplicated_checks(carries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    seen: set[tuple[object, ...]] = set()
    for carry in carries:
        for check in carry.get("checks", []):
            if not isinstance(check, dict):
                continue
            key = (
                check.get("cwd"),
                tuple(check.get("argv", [])),
                tuple(sorted((check.get("env") or {}).items())),
            )
            if key not in seen:
                seen.add(key)
                checks.append(check)
    return checks


def _run_checks(worktree: Path, checks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    reports: list[dict[str, Any]] = []
    for check in checks:
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (check.get("env") or {}).items()})
        try:
            result = subprocess.run(
                [str(item) for item in check["argv"]],
                cwd=worktree / str(check["cwd"]),
                env=env,
                text=True,
                capture_output=True,
                shell=False,
                check=False,
                timeout=CHECK_TIMEOUT_SECONDS,
            )
            row = {
                "id": check["id"],
                "returncode": result.returncode,
                "stdout": _bounded(result.stdout),
                "stderr": _bounded(result.stderr),
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            row = {
                "id": check["id"],
                "returncode": None,
                "stdout": _bounded(exc.stdout or ""),
                "stderr": _bounded(exc.stderr or ""),
                "timed_out": True,
            }
        except OSError as exc:
            row = {
                "id": check["id"],
                "returncode": None,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "timed_out": False,
            }
        reports.append(row)
        if row["returncode"] != 0:
            return reports, False
    return reports, True


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _update_state(state_path: Path, **updates: Any) -> dict[str, Any]:
    try:
        current = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        current = {}
    payload = current if isinstance(current, dict) else {}
    payload.update(updates)
    _write_json(state_path, payload)
    return payload


def _update_states(state_paths: list[Path], **updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for path in state_paths:
        payload = _update_state(path, **updates)
    return payload


def _remote_branch_sha(repo: Path, branch: str) -> str:
    result = _run(repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    value = result.stdout.split()[0]
    return value if len(value) >= 40 else ""


def generate_candidate(
    *,
    repo: Path,
    branch: str,
    upstream_sha: str,
    state_path: Path,
    canonical_state_path: Path | None = None,
    report_path: Path | None = None,
    manifest_path: Path | None = None,
    validator_path: Path | None = None,
    input_digest: str = "",
    run_checks: bool = True,
    publish: bool = True,
) -> dict[str, Any]:
    """Generate one carry-stack candidate from exact immutable inputs."""
    repo = repo.resolve()
    manifest_path = (manifest_path or (repo / "fork-carries.json")).resolve()
    validator_path = (validator_path or (repo / "scripts" / "fork_carry_manifest.py")).resolve()
    report_path = report_path or state_path.with_suffix(".report.json")
    state_paths = [state_path]
    if canonical_state_path is not None and canonical_state_path != state_path:
        state_paths.append(canonical_state_path)
    report: dict[str, Any] = {
        "state": "failed",
        "branch": branch,
        "candidate_branch": f"{branch}-next",
        "upstream_sha": upstream_sha,
        "input_digest": input_digest,
        "manifest_sha256": "",
        "validator_sha256": hashlib.sha256(validator_path.read_bytes()).hexdigest(),
        "replay_sha256": "",
        "candidate_sha": "",
        "changed_paths": [],
        "ownership_diagnostics": [],
        "upstream_survival": {
            "mode": "generated-from-pinned-upstream",
            "noncarry_paths_equal": False,
        },
        "checks": [],
        "checks_complete": False,
        "published": False,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "report_path": str(report_path),
    }
    _update_states(
        state_paths,
        state="running",
        pid=os.getpid(),
        started_at=report["started_at"],
        report_path=str(report_path),
    )

    container = Path(tempfile.mkdtemp(prefix=f"hermes-{branch}-candidate-"))
    worktree = container / "worktree"
    added = False
    try:
        manifest, diagnostics = _load_manifest(
            repo, manifest_path=manifest_path, validator_path=validator_path
        )
        report["manifest_sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        if diagnostics:
            raise RuntimeError("invalid carry manifest: " + "; ".join(diagnostics))
        carries = _active_replay_carries(manifest)
        report["replay_sha256"] = _replay_digest(carries)
        incomplete = [str(item.get("id")) for item in carries if not isinstance(item.get("replay"), dict)]
        if incomplete:
            raise RuntimeError("active carries are not replay-ready: " + ", ".join(incomplete))
        if _resolve(repo, upstream_sha) != upstream_sha:
            raise RuntimeError(f"pinned upstream commit is unavailable: {upstream_sha}")

        add = _run(repo, "worktree", "add", "--detach", str(worktree), upstream_sha)
        if add.returncode != 0:
            raise RuntimeError(add.stderr.strip() or "could not create candidate worktree")
        added = True

        applied: list[dict[str, str]] = []
        for carry in carries:
            for commit in carry["replay"]["commits"]:
                if _resolve(repo, commit) != commit:
                    raise RuntimeError(f"missing carry commit {commit} for {carry['id']}")
                picked = _run(
                    worktree,
                    "cherry-pick",
                    commit,
                    env=_source_commit_env(repo, commit),
                )
                if picked.returncode != 0:
                    conflicts = _run(worktree, "diff", "--name-only", "--diff-filter=U")
                    raise RuntimeError(
                        f"carry {carry['id']} failed at {commit}; conflicts: "
                        + ", ".join(conflicts.stdout.splitlines())
                    )
                applied.append({"carry": str(carry["id"]), "source_commit": commit})
        report["applied"] = applied
        report["candidate_sha"] = _resolve(worktree, "HEAD")

        changed = _run(worktree, "diff", "--name-only", f"{upstream_sha}..HEAD")
        if changed.returncode != 0:
            raise RuntimeError(changed.stderr.strip() or "could not enumerate candidate paths")
        changed_paths = sorted(line for line in changed.stdout.splitlines() if line)
        report["changed_paths"] = changed_paths
        ownership = candidate_path_ownership_diagnostics(manifest, changed_paths)
        report["ownership_diagnostics"] = ownership
        if ownership:
            raise RuntimeError("candidate contains unowned paths")
        report["upstream_survival"]["noncarry_paths_equal"] = True

        if run_checks:
            check_reports, checks_ok = _run_checks(worktree, _deduplicated_checks(carries))
            report["checks"] = check_reports
            if not checks_ok:
                raise RuntimeError("candidate verification check failed")
            report["checks_complete"] = True

        refreshed = _run(repo, "fetch", "upstream", "main", "--quiet")
        if refreshed.returncode != 0:
            raise RuntimeError(refreshed.stderr.strip() or "final upstream refresh failed")
        if _resolve(repo, "upstream/main") != upstream_sha:
            raise RuntimeError("upstream/main moved during verification; queue a fresh candidate")

        if publish:
            candidate_branch = f"{branch}-next"
            old_sha = _remote_branch_sha(repo, candidate_branch)
            lease = f"--force-with-lease=refs/heads/{candidate_branch}:{old_sha}"
            pushed = _run(
                worktree,
                "push",
                lease,
                "origin",
                f"HEAD:refs/heads/{candidate_branch}",
            )
            if pushed.returncode != 0:
                raise RuntimeError(pushed.stderr.strip() or "candidate push failed")
            read_back = _remote_branch_sha(repo, candidate_branch)
            if read_back != report["candidate_sha"]:
                raise RuntimeError("candidate ref read-back did not match generated SHA")
            report["published"] = True

        report["state"] = "ready"
        report["completed_at"] = datetime.now().isoformat(timespec="seconds")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["completed_at"] = datetime.now().isoformat(timespec="seconds")
    finally:
        if added:
            _run(worktree, "cherry-pick", "--abort")
            _run(repo, "worktree", "remove", "--force", str(worktree))
        try:
            container.rmdir()
        except OSError:
            pass
        _write_json(report_path, report)
        _update_states(
            state_paths,
            state=report["state"],
            branch=branch,
            candidate_branch=f"{branch}-next",
            upstream_sha=upstream_sha,
            input_digest=input_digest,
            manifest_sha256=report.get("manifest_sha256", ""),
            validator_sha256=report.get("validator_sha256", ""),
            replay_sha256=report.get("replay_sha256", ""),
            pid=None,
            candidate_sha=report.get("candidate_sha", ""),
            completed_at=report["completed_at"],
            report_path=str(report_path),
            error=report.get("error", ""),
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an isolated Axiom carry-stack candidate")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--upstream-sha", required=True)
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--canonical-state-path", type=Path)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--manifest-path", type=Path)
    parser.add_argument("--validator-path", type=Path)
    parser.add_argument("--input-digest", default="")
    parser.add_argument("--skip-checks", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-publish", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    report = generate_candidate(
        repo=args.repo,
        branch=args.branch,
        upstream_sha=args.upstream_sha,
        state_path=args.state_path,
        canonical_state_path=args.canonical_state_path,
        report_path=args.report_path,
        manifest_path=args.manifest_path,
        validator_path=args.validator_path,
        input_digest=args.input_digest,
        run_checks=not args.skip_checks,
        publish=not args.no_publish,
    )
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["state"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
