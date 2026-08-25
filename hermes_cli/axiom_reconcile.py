"""Detached, non-deploying Axiom carry-stack reconciliation worker."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


def _generated_manifest_env(repo: Path, upstream_sha: str) -> dict[str, str]:
    env = _source_commit_env(repo, upstream_sha)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Axiom Carry Replay",
            "GIT_AUTHOR_EMAIL": "axiom-carry-replay@localhost",
            "GIT_AUTHOR_DATE": env["GIT_COMMITTER_DATE"],
        }
    )
    return env


def _load_manifest(
    repo: Path,
    *,
    manifest_path: Path | None = None,
    validator_path: Path | None = None,
    check_path_existence: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    validator_path = validator_path or (repo / "scripts" / "fork_carry_manifest.py")
    manifest_path = manifest_path or (repo / "fork-carries.json")
    spec = importlib.util.spec_from_file_location("fork_carry_manifest", validator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fork carry manifest validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.load_manifest(manifest_path)
    diagnostics = module.validate_manifest(
        manifest, repo, check_path_existence=check_path_existence
    )
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


def _private_replay_ref(source_ref: str, run_id: str) -> str:
    suffix = hashlib.sha256(source_ref.encode()).hexdigest()[:16]
    return f"refs/axiom-reconcile/{run_id}/sources/{suffix}"


def _fetch_replay_sources(
    repo: Path,
    carries: list[dict[str, Any]],
    *,
    run_id: str,
) -> list[str]:
    private_refs: list[str] = []
    seen: set[str] = set()
    try:
        for carry in carries:
            replay = carry.get("replay")
            source_ref = replay.get("source_ref") if isinstance(replay, dict) else None
            if not isinstance(source_ref, str) or not source_ref.strip():
                continue
            source_ref = source_ref.strip()
            if source_ref in seen:
                continue
            seen.add(source_ref)
            if "/" not in source_ref:
                raise RuntimeError(f"invalid replay source_ref: {source_ref}")
            remote, branch = source_ref.split("/", 1)
            if (
                remote.startswith("-")
                or not re.fullmatch(r"[A-Za-z0-9._-]+", remote)
                or not re.fullmatch(
                    r"(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+", branch
                )
            ):
                raise RuntimeError(f"invalid replay source_ref: {source_ref}")
            private_ref = _private_replay_ref(source_ref, run_id)
            private_refs.append(private_ref)
            fetched = _run(
                repo,
                "fetch",
                "--no-tags",
                remote,
                f"+refs/heads/{branch}:{private_ref}",
            )
            if fetched.returncode != 0:
                raise RuntimeError(
                    f"cannot fetch replay source {source_ref}: "
                    + (fetched.stderr.strip() or "git fetch failed")
                )
            if not _resolve(repo, private_ref):
                raise RuntimeError(f"replay source read-back failed: {source_ref}")
    except Exception:
        _delete_private_refs(repo, private_refs)
        raise
    return private_refs


def _validate_replay_commit_sources(
    repo: Path,
    carries: list[dict[str, Any]],
    *,
    run_id: str,
) -> None:
    for carry in carries:
        replay = carry.get("replay")
        if not isinstance(replay, dict):
            continue
        source_ref = replay.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref.strip():
            continue
        private_ref = _private_replay_ref(source_ref.strip(), run_id)
        for commit in replay.get("commits", []):
            ancestry = _run(repo, "merge-base", "--is-ancestor", str(commit), private_ref)
            if ancestry.returncode != 0:
                raise RuntimeError(
                    f"carry {carry.get('id')} commit {commit} is not reachable from "
                    f"declared source {source_ref}"
                )


def _delete_private_refs(repo: Path, refs: list[str]) -> None:
    failures: list[str] = []
    for ref in refs:
        deleted = _run(repo, "update-ref", "-d", ref)
        verified = _run(repo, "show-ref", "--verify", "--quiet", ref)
        if deleted.returncode != 0:
            detail = deleted.stderr.strip() or "git update-ref failed"
            failures.append(f"could not delete private replay ref {ref}: {detail}")
        if verified.returncode == 0:
            failures.append(f"private replay ref remains after deletion: {ref}")
        elif verified.returncode != 1:
            detail = verified.stderr.strip() or f"git show-ref exited {verified.returncode}"
            failures.append(
                f"could not verify private replay ref absence {ref}: {detail}"
            )
    if failures:
        raise RuntimeError("could not clean private replay refs: " + "; ".join(failures))


def _verify_replay_sources_available(
    repo: Path,
    carries: list[dict[str, Any]],
    *,
    run_id: str,
) -> None:
    """Re-fetch, re-prove, and clean replay sources after publication."""
    private_refs = _fetch_replay_sources(repo, carries, run_id=run_id)
    try:
        _validate_replay_commit_sources(repo, carries, run_id=run_id)
    finally:
        _delete_private_refs(repo, private_refs)


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


def _windows_no_window_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run_checks(
    worktree: Path,
    checks: list[dict[str, Any]],
    *,
    log_path: Path | None = None,
    on_progress=None,
) -> tuple[list[dict[str, Any]], bool]:
    reports: list[dict[str, Any]] = []
    total = len(checks)
    for index, check in enumerate(checks, start=1):
        check_id = str(check["id"])
        if on_progress is not None:
            on_progress(check_index=index, check_total=total, check_id=check_id)
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (check.get("env") or {}).items()})
        argv = [str(item) for item in check["argv"]]
        if os.name == "nt":
            argv[0] = shutil.which(argv[0]) or argv[0]
        common = {
            "cwd": worktree / str(check["cwd"]),
            "env": env,
            "shell": False,
            "check": False,
            "timeout": CHECK_TIMEOUT_SECONDS,
            "creationflags": _windows_no_window_flags(),
        }
        output = ""
        log_file = None
        row: dict[str, Any] | None = None
        try:
            if log_path is None:
                result = subprocess.run(
                    argv,
                    text=True,
                    capture_output=True,
                    **common,
                )
                output = result.stdout
                stderr = result.stderr
            else:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_file = log_path.open("a+b")
                header = f"\n[check {index}/{total}] {check_id}\n$ {' '.join(argv)}\n"
                log_file.write(header.encode("utf-8", errors="replace"))
                log_file.flush()
                output_start = log_file.tell()
                result = subprocess.run(
                    argv,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    **common,
                )
                log_file.flush()
                output_end = log_file.tell()
                log_file.seek(output_start)
                output = log_file.read(output_end - output_start).decode(
                    "utf-8", errors="replace"
                )
                stderr = ""
            row = {
                "id": check_id,
                "returncode": result.returncode,
                "stdout": _bounded(output),
                "stderr": _bounded(stderr),
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            row = {
                "id": check_id,
                "returncode": None,
                "stdout": _bounded(output or exc.stdout or ""),
                "stderr": _bounded(exc.stderr or ""),
                "timed_out": True,
            }
        except OSError as exc:
            row = {
                "id": check_id,
                "returncode": None,
                "stdout": _bounded(output),
                "stderr": f"{type(exc).__name__}: {exc}",
                "timed_out": False,
            }
        finally:
            if log_file is not None:
                status = "passed" if row is not None and row["returncode"] == 0 else "failed"
                log_file.seek(0, os.SEEK_END)
                log_file.write(f"\n[{status}] {check_id}\n".encode("utf-8"))
                log_file.close()
        assert row is not None
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


def _pid_is_running(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00101000, False, value)
        if not handle:
            return ctypes.get_last_error() != 87
        try:
            return kernel32.WaitForSingleObject(handle, 0) != 0x00000000
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(value, 0)
    except OSError:
        return False
    return True


def _claim_state_lock(path: Path) -> int:
    for _attempt in range(100):
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            os.close(descriptor)
            time.sleep(0.01)
            continue
        payload = f"{os.getpid()}\n".encode()
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, payload)
        os.ftruncate(descriptor, len(payload))
        os.fsync(descriptor)
        return descriptor
    raise RuntimeError(f"could not acquire canonical state lock: {path}")


def _release_state_lock(path: Path, descriptor: int | None) -> None:
    del path
    if descriptor is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _update_canonical_state_if_current(
    path: Path,
    *,
    run_id: str,
    input_digest: str,
    **updates: Any,
) -> bool:
    lock_path = path.with_suffix(".lock")
    descriptor = _claim_state_lock(lock_path)
    try:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(current, dict):
            return False
        if current.get("run_id") != run_id or current.get("input_digest") != input_digest:
            return False
        current.update(updates)
        _write_json(path, current)
        return True
    finally:
        _release_state_lock(lock_path, descriptor)


def _publish_progress(
    *,
    state_path: Path,
    canonical_state_path: Path | None,
    run_id: str,
    input_digest: str,
    log_path: Path,
    phase: str,
    detail: str = "",
    **progress: Any,
) -> None:
    updates = {
        "phase": phase,
        "detail": detail,
        "progress_updated_at": datetime.now().isoformat(timespec="seconds"),
        **progress,
    }
    _update_state(state_path, **updates)
    if canonical_state_path is not None and canonical_state_path != state_path:
        _update_canonical_state_if_current(
            canonical_state_path,
            run_id=run_id,
            input_digest=input_digest,
            **updates,
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f": {detail}" if detail else ""
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[phase] {phase}{suffix}\n")
        log_file.flush()


def _remote_branch_sha(repo: Path, branch: str) -> str:
    result = _run(repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    value = result.stdout.split()[0]
    return value if len(value) >= 40 else ""


def _publish_candidate_if_current(
    *,
    repo: Path,
    worktree: Path,
    branch: str,
    candidate_sha: str,
    canonical_state_path: Path | None,
    run_id: str,
    input_digest: str,
) -> None:
    lock_path = (
        canonical_state_path.with_suffix(".lock")
        if canonical_state_path is not None
        else None
    )
    descriptor = _claim_state_lock(lock_path) if lock_path is not None else None
    try:
        if canonical_state_path is not None:
            try:
                current = json.loads(canonical_state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("cannot validate canonical reconciliation state") from exc
            if (
                not isinstance(current, dict)
                or current.get("run_id") != run_id
                or current.get("input_digest") != input_digest
            ):
                raise RuntimeError("stale reconciliation worker cannot publish candidate")
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
        observed_sha = _remote_branch_sha(repo, candidate_branch)
        if observed_sha != candidate_sha:
            detail = pushed.stderr.strip() if pushed.returncode != 0 else ""
            raise RuntimeError(
                detail
                or "candidate ref read-back did not match generated SHA"
            )
    finally:
        if lock_path is not None:
            _release_state_lock(lock_path, descriptor)


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
    worker_path: Path | None = None,
    run_checks: bool = True,
    publish: bool = True,
) -> dict[str, Any]:
    """Generate one carry-stack candidate from exact immutable inputs."""
    repo = repo.resolve()
    state_path = state_path.resolve()
    manifest_path = (manifest_path or (repo / "fork-carries.json")).resolve()
    validator_path = (validator_path or (repo / "scripts" / "fork_carry_manifest.py")).resolve()
    worker_path = (worker_path or Path(__file__)).resolve()
    report_path = (report_path or state_path.with_suffix(".report.json")).resolve()
    run_dir = state_path.parent
    log_path = run_dir / "worker.log"
    run_id = input_digest[:24]
    report: dict[str, Any] = {
        "state": "failed",
        "branch": branch,
        "candidate_branch": f"{branch}-next",
        "upstream_sha": upstream_sha,
        "input_digest": input_digest,
        "run_id": run_id,
        "worker_sha256": hashlib.sha256(worker_path.read_bytes()).hexdigest(),
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
        "run_dir": str(run_dir),
        "state_path": str(state_path),
        "report_path": str(report_path),
    }
    running_updates = dict(
        state="running",
        pid=os.getpid(),
        started_at=report["started_at"],
        report_path=str(report_path),
    )
    _update_state(state_path, **running_updates)
    if canonical_state_path is not None and canonical_state_path != state_path:
        _update_canonical_state_if_current(
            canonical_state_path,
            run_id=run_id,
            input_digest=input_digest,
            **running_updates,
        )

    def progress(phase: str, detail: str = "", **fields: Any) -> None:
        _publish_progress(
            state_path=state_path,
            canonical_state_path=canonical_state_path,
            run_id=run_id,
            input_digest=input_digest,
            log_path=log_path,
            phase=phase,
            detail=detail,
            **fields,
        )

    container = Path(tempfile.mkdtemp(prefix=f"hermes-{branch}-candidate-"))
    worktree = container / "worktree"
    added = False
    private_refs: list[str] = []
    try:
        progress("manifest", "validating immutable carry manifest")
        manifest, diagnostics = _load_manifest(
            repo,
            manifest_path=manifest_path,
            validator_path=validator_path,
            check_path_existence=False,
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
        progress("sources", "fetching and validating carry source refs")
        private_refs = _fetch_replay_sources(repo, carries, run_id=run_id)
        _validate_replay_commit_sources(repo, carries, run_id=run_id)

        progress("worktree", f"creating candidate from {upstream_sha[:12]}")
        add = _run(repo, "worktree", "add", "--detach", str(worktree), upstream_sha)
        if add.returncode != 0:
            raise RuntimeError(add.stderr.strip() or "could not create candidate worktree")
        added = True

        applied: list[dict[str, str]] = []
        replay_total = sum(len(carry["replay"]["commits"]) for carry in carries)
        replay_index = 0
        for carry in carries:
            for commit in carry["replay"]["commits"]:
                replay_index += 1
                progress(
                    "replay",
                    f"{carry['id']} {commit[:12]}",
                    replay_index=replay_index,
                    replay_total=replay_total,
                    carry_id=str(carry["id"]),
                )
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

        progress("candidate", "validating generated manifest and path ownership")
        candidate_manifest_path = worktree / "fork-carries.json"
        candidate_manifest_path.write_bytes(manifest_path.read_bytes())
        staged_manifest = _run(worktree, "add", "--", "fork-carries.json")
        if staged_manifest.returncode != 0:
            raise RuntimeError(staged_manifest.stderr.strip() or "could not stage replay manifest")
        manifest_changed = _run(worktree, "diff", "--cached", "--quiet", "--", "fork-carries.json")
        if manifest_changed.returncode == 1:
            committed_manifest = _run(
                worktree,
                "commit",
                "-m",
                "chore(fork): record generated carry manifest",
                env=_generated_manifest_env(repo, upstream_sha),
            )
            if committed_manifest.returncode != 0:
                raise RuntimeError(
                    committed_manifest.stderr.strip() or "could not commit replay manifest"
                )
        elif manifest_changed.returncode != 0:
            raise RuntimeError(manifest_changed.stderr.strip() or "could not inspect replay manifest")
        _, candidate_diagnostics = _load_manifest(
            worktree,
            manifest_path=candidate_manifest_path,
            validator_path=validator_path,
            check_path_existence=True,
        )
        if candidate_diagnostics:
            raise RuntimeError(
                "invalid generated carry manifest: " + "; ".join(candidate_diagnostics)
            )
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
            checks = _deduplicated_checks(carries)
            check_reports, checks_ok = _run_checks(
                worktree,
                checks,
                log_path=log_path,
                on_progress=lambda **row: progress(
                    "checks", str(row["check_id"]), **row
                ),
            )
            report["checks"] = check_reports
            if not checks_ok:
                raise RuntimeError("candidate verification check failed")
            report["checks_complete"] = True

        progress("upstream", "refreshing upstream after verification")
        refreshed = _run(repo, "fetch", "upstream", "main", "--quiet")
        if refreshed.returncode != 0:
            raise RuntimeError(refreshed.stderr.strip() or "final upstream refresh failed")
        observed_upstream_sha = _resolve(repo, "upstream/main")
        if not observed_upstream_sha:
            raise RuntimeError("could not resolve upstream/main after final refresh")
        report["observed_upstream_sha"] = observed_upstream_sha
        report["upstream_pending"] = observed_upstream_sha != upstream_sha

        if publish:
            progress("publish", f"publishing {report['candidate_sha'][:12]}")
            _publish_candidate_if_current(
                repo=repo,
                worktree=worktree,
                branch=branch,
                candidate_sha=report["candidate_sha"],
                canonical_state_path=canonical_state_path,
                run_id=run_id,
                input_digest=input_digest,
            )
            report["published"] = True
            _verify_replay_sources_available(
                repo,
                carries,
                run_id=f"{run_id}-post-publication",
            )
            report["source_availability_verified"] = True

        report["state"] = "ready"
        report["completed_at"] = datetime.now().isoformat(timespec="seconds")
        progress("ready", report["candidate_sha"][:12])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["completed_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            progress("failed", report["error"])
        except Exception:
            pass
    finally:
        if added:
            _run(worktree, "cherry-pick", "--abort")
            _run(repo, "worktree", "remove", "--force", str(worktree))
        try:
            _delete_private_refs(repo, private_refs)
        except Exception as cleanup_exc:
            report["state"] = "failed"
            report["error"] = f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            report["completed_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            container.rmdir()
        except OSError:
            pass
        _write_json(report_path, report)
        report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
        final_updates = dict(
            state=report["state"],
            phase=report["state"],
            detail=(
                report.get("error", "")
                if report["state"] == "failed"
                else report.get("candidate_sha", "")[:12]
            ),
            branch=branch,
            candidate_branch=f"{branch}-next",
            upstream_sha=upstream_sha,
            input_digest=input_digest,
            run_id=run_id,
            worker_sha256=report.get("worker_sha256", ""),
            manifest_sha256=report.get("manifest_sha256", ""),
            validator_sha256=report.get("validator_sha256", ""),
            replay_sha256=report.get("replay_sha256", ""),
            pid=None,
            candidate_sha=report.get("candidate_sha", ""),
            completed_at=report["completed_at"],
            report_path=str(report_path),
            report_sha256=report_sha256,
            error=report.get("error", ""),
        )
        _update_state(state_path, **final_updates)
        if canonical_state_path is not None and canonical_state_path != state_path:
            canonical_updates = {
                key: value
                for key, value in final_updates.items()
                if key not in {"run_id", "input_digest"}
            }
            _update_canonical_state_if_current(
                canonical_state_path,
                run_id=run_id,
                input_digest=input_digest,
                **canonical_updates,
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
