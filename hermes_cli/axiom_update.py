"""Axiom fork-only update / deploy-branch helpers.

EXTRACTED FROM ``hermes_cli/main.py`` to shrink the fork's footprint in that
file. ``main.py`` is upstream's most actively-refactored module (the ongoing
"god-file Phase 2" subcommand/parser extraction), so every fork-only line that
lived there collided with upstream merges on a near-daily basis.

These 15 functions implement Axiom's deploy-branch update flow
(upstream/main -> origin/axiom -> live checkout), the update handoff marker,
managed-worktree cleanup, deploy-branch stash preservation, dashboard-service
PID discovery, and Windows gateway-launcher detection. None of them exist
upstream, so they carry cleanly here with zero merge surface in main.py.

SEAM CONTRACT (see docs/axiom-fork-contract.md):
  * main.py imports these names at module load and calls them at the original
    call sites (thin seam — 8 call sites).
  * Symbols that still live in main.py (``_count_commits_between``,
    ``_hermes_exe_shims``, ``_is_windows``, ``_validate_critical_files_syntax``)
    are imported LAZILY inside the functions that need them, to avoid a circular
    import at module load (main.py imports this module at its top, before those
    helpers are defined). These are stable upstream utilities; importing rather
    than moving them keeps this module free of upstream-churning code.

When upstream lands an equivalent deploy-branch update mechanism, retire this
module per the fork contract's drop-review process rather than letting it rot.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional


logger = logging.getLogger("hermes_cli.axiom_update")

# Fork-only: relative path under HERMES_HOME for the deploy-branch update
# handoff marker. Not referenced upstream.
DEPLOY_HANDOFF_FILE = ".update_handoff.json"


def _validate_update_after_pull(git_cmd, root, pre_pull_sha: str | None) -> None:
    """Validate critical startup files after a pull and roll back on syntax failure."""
    from hermes_cli.main import _validate_critical_files_syntax  # lazy: avoid circular import at module load
    ok, failing_path, error_message = _validate_critical_files_syntax(root)
    if ok:
        return

    print("✗ Updated code failed startup syntax validation.")
    if failing_path:
        print(f"  File: {failing_path}")
    if error_message:
        first_line = str(error_message).splitlines()[0]
        print(f"  {first_line}")

    if pre_pull_sha:
        print(f"  → Rolling back to {pre_pull_sha[:12]}...")
        rollback = subprocess.run(
            git_cmd + ["reset", "--hard", pre_pull_sha],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if rollback.returncode == 0:
            print("  ✓ Rollback complete. Re-run 'hermes update' after the upstream fix lands.")
        else:
            print("  ✗ Automatic rollback failed.")
            if rollback.stderr.strip():
                print(f"    {rollback.stderr.strip().splitlines()[0]}")
            print(f"    Try manually: git reset --hard {pre_pull_sha}")
    else:
        print("  No pre-pull SHA was available for automatic rollback.")
    sys.exit(1)


def _desktop_shortcut_exists() -> bool:
    """Return True when Windows has Hermes Desktop shortcuts installed.

    Desktop shortcuts historically point directly at
    apps/desktop/release/win-unpacked/Hermes.exe. If a failed update/build
    cleanup removes that unpacked build directory, `_desktop_packaged_executable`
    returns None and `hermes update` used to conclude "Desktop is not installed",
    skipping the rebuild that would repair the shortcut target. Treat existing
    shortcut entries as install intent so update can self-heal a missing
    packaged exe.
    """
    if sys.platform != "win32":
        return False

    candidates: list[Path] = []
    userprofile = os.environ.get("USERPROFILE")
    appdata = os.environ.get("APPDATA")
    if userprofile:
        desktop = Path(userprofile) / "Desktop"
        candidates.extend([desktop / "Hermes.lnk", desktop / "Hermes Desktop.lnk"])
    if appdata:
        programs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        candidates.extend([programs / "Hermes.lnk", programs / "Hermes Desktop.lnk"])

    return any(path.exists() for path in candidates)


def _get_dashboard_service_pids() -> set:
    """Return PIDs currently managed by ``hermes-dashboard*`` systemd units.

    Mirrors ``hermes_cli.gateway._get_service_pids`` but for dashboard
    units (which the update flow restarts via the same systemd path).
    Used to exclude freshly-restarted managed processes from the
    post-update stale-dashboard sweep — without this, the sweep would
    SIGTERM the process systemd just spawned with new code, and systemd
    would have to respawn it again.
    """
    pids: set = set()

    try:
        from hermes_cli.gateway import supports_systemd_services
    except Exception:
        return pids

    if not supports_systemd_services():
        return pids

    for scope_args in [["systemctl", "--user"], ["systemctl"]]:
        try:
            result = subprocess.run(
                scope_args + ["list-units", "hermes-dashboard*",
                              "--plain", "--no-legend", "--no-pager"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if not parts or not parts[0].endswith(".service"):
                    continue
                svc = parts[0]
                try:
                    show = subprocess.run(
                        scope_args + ["show", svc,
                                      "--property=MainPID", "--value"],
                        capture_output=True, text=True, timeout=5,
                    )
                    pid = int(show.stdout.strip())
                    if pid > 0:
                        pids.add(pid)
                except (ValueError, subprocess.TimeoutExpired):
                    pass
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return pids


def _clean_managed_worktree(git_cmd: list[str], cwd: Path) -> bool:
    """Discard working-tree dirt on a managed (non-fork) clone.

    On a managed install (%LOCALAPPDATA%\\hermes\\hermes-agent or
    ~/.hermes/hermes-agent) the user never edits the source tree, so any
    "dirty" state is pure git artifact: CRLF renormalization, npm lockfile
    churn, or files left behind when a directory was deleted upstream. Stashing
    that dirt and re-applying it after a pull is actively dangerous because it
    can clobber freshly-pulled source files.

    For a managed clone the correct move is to throw the dirt away with
    ``git reset --hard HEAD`` + ``git clean -fd``. Forks keep the stash
    machinery because their local edits are intentional.

    Returns True only if the tree contained dirt and was cleaned. Returns False
    when the tree is already clean or a git failure occurs so callers can fall
    back to the normal stash path.
    """
    status = subprocess.run(
        git_cmd + ["status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        return False
    if not status.stdout.strip():
        return False

    print("→ Discarding working-tree changes on managed clone before update...")
    reset = subprocess.run(
        git_cmd + ["reset", "--hard", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if reset.returncode != 0:
        return False
    # Drop untracked files too (e.g. orphaned build artifacts), but never touch
    # ignored paths — node_modules, venv, build outputs, and the like are
    # expensive to rebuild and not git-artifact dirt.
    subprocess.run(
        git_cmd + ["clean", "-fd"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return True


def _short_git_ref(git_cmd: list[str], cwd: Path, ref: str) -> str:
    try:
        result = subprocess.run(
            git_cmd + ["rev-parse", "--short", ref],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _count_changed_from_pre_update(
    git_cmd: list[str],
    cwd: Path,
    pre_update_head: str,
    fallback: int,
) -> int:
    from hermes_cli.main import _count_commits_between  # lazy: avoid circular import at module load
    if pre_update_head:
        changed = _count_commits_between(git_cmd, cwd, pre_update_head, "HEAD")
        if changed >= 0:
            return changed
    return max(fallback, 1) if fallback > 0 else 0


def _deploy_handoff_marker_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / DEPLOY_HANDOFF_FILE


def _record_deploy_handoff(
    *,
    repo: Path,
    branch: str,
    reason: str,
    worktree_path: Optional[Path] = None,
) -> None:
    try:
        marker = _deploy_handoff_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "repo": str(repo),
            "branch": branch,
            "reason": reason,
            "worktree": str(worktree_path) if worktree_path is not None else "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        marker.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        logger.debug("Failed to write deploy handoff marker", exc_info=True)


def _completed_deploy_handoff_requires_post_update(
    git_cmd: list[str],
    repo: Path,
    branch: str,
) -> bool:
    from hermes_cli.main import _count_commits_between  # lazy: avoid circular import at module load
    marker = _deploy_handoff_marker_path()
    if not marker.exists():
        return False

    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    if payload.get("branch") != branch:
        return False

    recorded_repo = str(payload.get("repo") or "")
    if recorded_repo and Path(recorded_repo).resolve() != repo.resolve():
        return False

    remote_ref = f"origin/{branch}"
    origin_ahead = _count_commits_between(git_cmd, repo, "HEAD", remote_ref)
    local_ahead = _count_commits_between(git_cmd, repo, remote_ref, "HEAD")
    if origin_ahead != 0 or local_ahead != 0:
        return False

    upstream_merged = subprocess.run(
        git_cmd + ["merge-base", "--is-ancestor", "upstream/main", remote_ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if upstream_merged.returncode != 0:
        return False

    try:
        marker.unlink()
    except OSError:
        logger.debug("Failed to clear deploy handoff marker", exc_info=True)

    print("→ Completed deploy handoff detected; refreshing install and services.")
    return True


def _sync_deploy_main_to_upstream(git_cmd: list[str], repo: Path) -> bool:
    from hermes_cli.main import _count_commits_between  # lazy: avoid circular import at module load
    main_local = _count_commits_between(git_cmd, repo, "upstream/main", "main")
    main_behind = _count_commits_between(git_cmd, repo, "main", "upstream/main")
    if main_local < 0 or main_behind < 0:
        print("  ✗ Could not compare local main with upstream/main.")
        return False

    if main_local > 0:
        print("  ✗ local main has commits that are not on upstream/main.")
        print("    Refusing to rewrite main during deploy update; resolve main first.")
        return False

    if main_behind == 0:
        return True

    result = subprocess.run(
        git_cmd + ["branch", "-f", "main", "upstream/main"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("  ✗ Could not fast-forward local main to upstream/main.")
        if result.stderr.strip():
            print(f"    {result.stderr.strip().splitlines()[0]}")
        return False

    print(f"  ✓ Synced local main to upstream/main ({main_behind} commit(s))")
    return True


def _print_deploy_branch_handoff(
    *,
    reason: str,
    repo: Path,
    branch: str,
    upstream_ahead: int = -1,
    origin_ahead: int = -1,
    worktree_path: Optional[Path] = None,
    conflict_files: str = "",
    error: str = "",
    git_cmd: Optional[list[str]] = None,
) -> None:
    git_cmd = git_cmd or ["git"]
    print()
    print("  ── Pass this to your Hermes agent ─────────────")
    print()
    print("  ┌─ Copy below ─────────────────────────────────")
    print(f"  │ hermes update: {reason}")
    print(f"  │ Repo: {repo}")
    print(f"  │ Deploy branch: {branch}")
    print(f"  │ Live HEAD: {_short_git_ref(git_cmd, repo, 'HEAD')}")
    print(f"  │ Origin deploy: {_short_git_ref(git_cmd, repo, f'origin/{branch}')}")
    print(f"  │ Upstream main: {_short_git_ref(git_cmd, repo, 'upstream/main')}")
    if upstream_ahead >= 0:
        print(f"  │ Upstream commits not in origin/{branch}: {upstream_ahead}")
    if origin_ahead >= 0:
        print(f"  │ origin/{branch} commits not in live HEAD: {origin_ahead}")
    if worktree_path is not None:
        print(f"  │ Worktree: {worktree_path}")
    if conflict_files:
        print("  │ Conflicting files:")
        for f in conflict_files.splitlines()[:12]:
            print(f"  │   {f}")
    if error:
        print(f"  │ Error: {error.splitlines()[0]}")
    print("  │ ")
    print(f"  │ Please merge upstream/main into {branch}, resolve conflicts,")
    print(f"  │ run focused tests, push HEAD:{branch} to origin, then run")
    print(f"  │ hermes update again so the live checkout fast-forwards cleanly.")
    print("  └────────────────────────────────────────────")
    _record_deploy_handoff(
        repo=repo,
        branch=branch,
        reason=reason,
        worktree_path=worktree_path,
    )
    print()


def _fast_forward_live_deploy_checkout(
    git_cmd: list[str],
    repo: Path,
    branch: str,
    pre_update_head: str,
    fallback: int,
) -> Optional[int]:
    """Refresh ``origin/<branch>`` and fast-forward the live checkout to it."""
    remote_ref = f"origin/{branch}"
    fetch_deploy = subprocess.run(
        git_cmd + ["fetch", "origin", f"{branch}:refs/remotes/origin/{branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if fetch_deploy.returncode != 0:
        return None

    ff_result = subprocess.run(
        git_cmd + ["merge", "--ff-only", remote_ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if ff_result.returncode != 0:
        return None

    return _count_changed_from_pre_update(git_cmd, repo, pre_update_head, fallback)


def _recover_deploy_push_rejection(
    *,
    git_cmd: list[str],
    repo: Path,
    branch: str,
    worktree_path: Path,
    pre_update_head: str,
    upstream_ahead: int,
    origin_ahead: int,
) -> Optional[int]:
    """Recover common deploy-branch push races before requiring a handoff.

    Docker-Server and Axiom-Desktop often run ``hermes update`` back-to-back.
    In that flow ``origin/<branch>`` can advance after this process created its
    temp merge worktree but before it pushes. A raw push rejection is not yet a
    conflict; first fetch the new remote tip and classify whether the remote
    already contains this merge, whether the live checkout can simply
    fast-forward, or whether the temp worktree can merge the new remote tip and
    retry once.
    """
    from hermes_cli.main import _count_commits_between  # lazy: avoid circular import at module load

    remote_ref = f"origin/{branch}"
    print(f"  ⚠ origin/{branch} advanced during update; reconciling once...")

    subprocess.run(
        git_cmd + ["fetch", "origin", f"{branch}:refs/remotes/origin/{branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        git_cmd + ["fetch", "upstream", "--quiet"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    # Another host may have already pushed the same/equivalent merge. If the
    # retained temp merge is now an ancestor of origin/<branch>, do not hand off;
    # just fast-forward live and continue the install/restart phase.
    temp_in_origin = subprocess.run(
        git_cmd + ["merge-base", "--is-ancestor", "HEAD", remote_ref],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if temp_in_origin.returncode == 0:
        changed = _fast_forward_live_deploy_checkout(
            git_cmd,
            repo,
            branch,
            pre_update_head,
            max(origin_ahead, upstream_ahead),
        )
        if changed is not None:
            print(f"  ✓ origin/{branch} already contains this deploy merge; fast-forwarded live checkout.")
            return changed

    live_in_origin = subprocess.run(
        git_cmd + ["merge-base", "--is-ancestor", "HEAD", remote_ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    upstream_in_origin = subprocess.run(
        git_cmd + ["merge-base", "--is-ancestor", "upstream/main", remote_ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if live_in_origin.returncode == 0 and upstream_in_origin.returncode == 0:
        changed = _fast_forward_live_deploy_checkout(
            git_cmd,
            repo,
            branch,
            pre_update_head,
            max(origin_ahead, upstream_ahead),
        )
        if changed is not None:
            print(f"  ✓ origin/{branch} already includes upstream/main; fast-forwarded live checkout.")
            return changed

    merge_origin = subprocess.run(
        git_cmd + ["merge", "--no-edit", remote_ref],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if merge_origin.returncode != 0:
        return None

    # Upstream may have advanced too while the first temp merge was running.
    upstream_remaining = _count_commits_between(git_cmd, worktree_path, "HEAD", "upstream/main")
    if upstream_remaining > 0:
        merge_upstream = subprocess.run(
            git_cmd + ["merge", "--no-edit", "upstream/main"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if merge_upstream.returncode != 0:
            return None

    retry_push = subprocess.run(
        git_cmd + ["push", "origin", f"HEAD:{branch}"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if retry_push.returncode != 0:
        return None

    changed = _fast_forward_live_deploy_checkout(
        git_cmd,
        repo,
        branch,
        pre_update_head,
        max(origin_ahead, upstream_ahead),
    )
    if changed is None:
        return None

    print(f"  ✓ Reconciled remote-advanced origin/{branch} and pushed retry merge.")
    return changed


def _preserve_deploy_branch_stash(stash_ref: str) -> None:
    print("⚠ Local changes were stashed and left preserved.")
    print("  Deploy branch updates keep the live checkout on the tested origin branch.")
    print(f"  Stash ref: {stash_ref}")
    print("  Review with: git stash show --stat")
    print(f"  Restore manually, if needed, with: git stash apply {stash_ref}")


def _remove_update_worktree(
    git_cmd: list[str],
    repo: Path,
    worktree_path: Path,
    parent: Path,
) -> None:
    subprocess.run(
        git_cmd + ["worktree", "remove", str(worktree_path), "--force"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    shutil.rmtree(parent, ignore_errors=True)


def _run_deploy_branch_update(
    git_cmd: list[str],
    repo: Path,
    branch: str,
    pre_update_head: str,
) -> Optional[int]:
    """Update a merge-based deploy branch without mutating live code on conflicts.

    The live checkout only fast-forwards to ``origin/<branch>`` after any
    upstream merge has succeeded and been pushed.  Merge conflicts happen in a
    temporary worktree so production source files are not left conflicted.
    Returns the number of commits that changed the live checkout, ``0`` when no
    code changed, or ``None`` when a handoff was printed and update should stop.
    """
    from hermes_cli.main import _count_commits_between  # lazy: avoid circular import at module load
    from hermes_cli.update_ui import Pipeline

    remote_ref = f"origin/{branch}"
    _pipe = Pipeline(["fetch upstream", "merge upstream", f"sync {branch}"])
    _pipe.start("fetch upstream")

    fetch_upstream = subprocess.run(
        git_cmd + ["fetch", "upstream", "--quiet"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if fetch_upstream.returncode != 0:
        _pipe.fail(note="cannot fetch upstream")
        _print_deploy_branch_handoff(
            reason="cannot fetch upstream.",
            repo=repo,
            branch=branch,
            error=(fetch_upstream.stderr or "").strip(),
            git_cmd=git_cmd,
        )
        return None

    if not _sync_deploy_main_to_upstream(git_cmd, repo):
        _pipe.fail(note="cannot sync local main")
        _print_deploy_branch_handoff(
            reason="local main cannot be synchronized with upstream/main.",
            repo=repo,
            branch=branch,
            git_cmd=git_cmd,
        )
        return None

    origin_ahead = _count_commits_between(git_cmd, repo, "HEAD", remote_ref)
    local_ahead = _count_commits_between(git_cmd, repo, remote_ref, "HEAD")
    upstream_ahead = _count_commits_between(git_cmd, repo, remote_ref, "upstream/main")
    if origin_ahead < 0 or local_ahead < 0 or upstream_ahead < 0:
        _pipe.fail(note="cannot compare deploy refs")
        _print_deploy_branch_handoff(
            reason="cannot compare deploy branch refs.",
            repo=repo,
            branch=branch,
            upstream_ahead=upstream_ahead,
            origin_ahead=origin_ahead,
            git_cmd=git_cmd,
        )
        return None
    if upstream_ahead == 0 and local_ahead == 0:
        if origin_ahead == 0:
            _pipe.finish(note="already up to date")
            return 0

        _pipe.advance(f"sync {branch}")
        ff_result = subprocess.run(
            git_cmd + ["merge", "--ff-only", remote_ref],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if ff_result.returncode != 0:
            _pipe.fail(note=f"cannot fast-forward to {remote_ref}")
            _print_deploy_branch_handoff(
                reason=f"fast-forward to {remote_ref} failed.",
                repo=repo,
                branch=branch,
                upstream_ahead=upstream_ahead,
                origin_ahead=origin_ahead,
                error=(ff_result.stderr or "").strip(),
                git_cmd=git_cmd,
            )
            return None
        _pipe.finish(note=f"fast-forwarded {origin_ahead} commit(s)")
        return _count_changed_from_pre_update(git_cmd, repo, pre_update_head, origin_ahead)

    parent = Path(tempfile.mkdtemp(prefix=f"hermes-update-{branch}-"))
    worktree_path = parent / "worktree"
    worktree_created = False

    _pipe.advance("merge upstream")
    worktree_base = "HEAD" if local_ahead > 0 else remote_ref
    add_result = subprocess.run(
        git_cmd + ["worktree", "add", "--detach", str(worktree_path), worktree_base],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if add_result.returncode != 0:
        shutil.rmtree(parent, ignore_errors=True)
        _pipe.fail(note="cannot create update worktree")
        _print_deploy_branch_handoff(
            reason="cannot create deploy update worktree.",
            repo=repo,
            branch=branch,
            upstream_ahead=upstream_ahead,
            origin_ahead=origin_ahead,
            error=(add_result.stderr or "").strip(),
            git_cmd=git_cmd,
        )
        return None
    worktree_created = True

    if local_ahead > 0 and origin_ahead > 0:
        merge_origin = subprocess.run(
            git_cmd + ["merge", "--no-edit", remote_ref],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if merge_origin.returncode != 0:
            conflict_result = subprocess.run(
                git_cmd + ["diff", "--name-only", "--diff-filter=U"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )
            _pipe.fail(note=f"merge {remote_ref} into live {branch} failed")
            _print_deploy_branch_handoff(
                reason=f"merge {remote_ref} into live {branch} failed.",
                repo=repo,
                branch=branch,
                upstream_ahead=upstream_ahead,
                origin_ahead=origin_ahead,
                worktree_path=worktree_path,
                conflict_files=conflict_result.stdout.strip(),
                error=(merge_origin.stderr or merge_origin.stdout or "").strip(),
                git_cmd=git_cmd,
            )
            print("  The live checkout was left unchanged; resolve the retained worktree above.")
            return None

    if upstream_ahead > 0:
        merge_result = subprocess.run(
            git_cmd + ["merge", "--no-edit", "upstream/main"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if merge_result.returncode != 0:
            conflict_result = subprocess.run(
                git_cmd + ["diff", "--name-only", "--diff-filter=U"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )
            _pipe.fail(note=f"merge into {branch} failed")
            _print_deploy_branch_handoff(
                reason=f"merge into {branch} failed.",
                repo=repo,
                branch=branch,
                upstream_ahead=upstream_ahead,
                origin_ahead=origin_ahead,
                worktree_path=worktree_path,
                conflict_files=conflict_result.stdout.strip(),
                error=(merge_result.stderr or merge_result.stdout or "").strip(),
                git_cmd=git_cmd,
            )
            print("  The live checkout was left unchanged; resolve the retained worktree above.")
            return None

    push_result = subprocess.run(
        git_cmd + ["push", "origin", f"HEAD:{branch}"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if push_result.returncode != 0:
        recovered = _recover_deploy_push_rejection(
            git_cmd=git_cmd,
            repo=repo,
            branch=branch,
            worktree_path=worktree_path,
            pre_update_head=pre_update_head,
            upstream_ahead=upstream_ahead,
            origin_ahead=origin_ahead,
        )
        if recovered is not None:
            _pipe.advance(f"sync {branch}")
            _pipe.finish(note="recovered remote-advanced push")
            if worktree_created:
                _remove_update_worktree(git_cmd, repo, worktree_path, parent)
            return recovered

        _pipe.fail(note=f"cannot push {branch}")
        _print_deploy_branch_handoff(
            reason=f"push to origin/{branch} failed.",
            repo=repo,
            branch=branch,
            upstream_ahead=upstream_ahead,
            origin_ahead=origin_ahead,
            worktree_path=worktree_path,
            error=(push_result.stderr or "").strip(),
            git_cmd=git_cmd,
        )
        print("  The live checkout was left unchanged; the merged worktree was retained.")
        return None

    _pipe.advance(f"sync {branch}")
    fetch_deploy = subprocess.run(
        git_cmd + ["fetch", "origin", f"{branch}:refs/remotes/origin/{branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if fetch_deploy.returncode != 0:
        _pipe.fail(note=f"cannot refresh origin/{branch}")
        _print_deploy_branch_handoff(
            reason=f"fetch origin/{branch} after push failed.",
            repo=repo,
            branch=branch,
            upstream_ahead=upstream_ahead,
            origin_ahead=origin_ahead,
            worktree_path=worktree_path,
            error=(fetch_deploy.stderr or "").strip(),
            git_cmd=git_cmd,
        )
        return None

    ff_result = subprocess.run(
        git_cmd + ["merge", "--ff-only", remote_ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if ff_result.returncode != 0:
        _pipe.fail(note=f"cannot fast-forward to {remote_ref}")
        _print_deploy_branch_handoff(
            reason=f"fast-forward to pushed {remote_ref} failed.",
            repo=repo,
            branch=branch,
            upstream_ahead=upstream_ahead,
            origin_ahead=origin_ahead,
            worktree_path=worktree_path,
            error=(ff_result.stderr or "").strip(),
            git_cmd=git_cmd,
        )
        return None

    if local_ahead > 0:
        note = (
            f"reconciled {local_ahead} live + {origin_ahead} origin + "
            f"{upstream_ahead} upstream commit(s)"
        )
        _pipe.finish(note=note)
    else:
        _pipe.finish(note=f"merged {upstream_ahead} upstream commit(s)")
    if worktree_created:
        _remove_update_worktree(git_cmd, repo, worktree_path, parent)
    return _count_changed_from_pre_update(
        git_cmd,
        repo,
        pre_update_head,
        max(origin_ahead, upstream_ahead),
    )


def _detect_windows_gateway_launcher_instances(
    scripts_dir: Path, *, exclude_pid: int | None = None
) -> list[tuple[int, str, str]]:
    """Find venv shim launchers that are specifically running gateways.

    ``find_gateway_pids()`` reports the Python process that owns the gateway
    socket. On Windows, a manual gateway started as ``hermes.exe gateway run``
    also leaves a parent setuptools launcher process mapped against
    ``venv\\Scripts\\hermes.exe``. Stopping only the socket-owning Python PID
    is not enough: the launcher can still make the updater's generic shim guard
    abort. This helper finds only those gateway launcher shims so the pause path
    can stop them without ignoring unrelated REPL/Desktop backend shims.
    """
    from hermes_cli.main import _hermes_exe_shims, _is_windows  # lazy: avoid circular import at module load
    if not _is_windows():
        return []

    try:
        import psutil
    except Exception:
        return []

    shim_paths: set[str] = set()
    for shim in _hermes_exe_shims(scripts_dir):
        try:
            shim_paths.add(str(shim.resolve()).lower())
        except OSError:
            shim_paths.add(str(shim).lower())
    if not shim_paths:
        return []

    if exclude_pid is not None:
        exclude_pids: set[int] = {int(exclude_pid)}
    else:
        exclude_pids = {os.getpid()}
    try:
        seed = next(iter(exclude_pids))
        try:
            ancestors = psutil.Process(seed).parents()
        except Exception:
            ancestors = []
        for ancestor in ancestors:
            try:
                anc_exe = ancestor.exe()
            except Exception:
                continue
            if not anc_exe:
                continue
            try:
                anc_norm = str(Path(anc_exe).resolve()).lower()
            except (OSError, ValueError):
                anc_norm = str(anc_exe).lower()
            if anc_norm in shim_paths:
                exclude_pids.add(int(ancestor.pid))
    except Exception:
        pass

    try:
        proc_iter = psutil.process_iter(["pid", "exe", "name", "cmdline"])
    except Exception:
        return []

    def _profile_from_cmdline(cmdline: list[str] | str) -> str:
        if isinstance(cmdline, str):
            parts = cmdline.split()
        else:
            parts = [str(part) for part in cmdline]
        for index, part in enumerate(parts):
            if part in {"--profile", "-p"} and index + 1 < len(parts):
                value = parts[index + 1].strip()
                return value or "default"
            if part.startswith("--profile="):
                value = part.split("=", 1)[1].strip()
                return value or "default"
        return "default"

    matches: list[tuple[int, str, str]] = []
    for proc in proc_iter:
        try:
            info = proc.info
        except Exception:
            continue
        pid = info.get("pid")
        exe = info.get("exe")
        if not exe or pid is None or pid in exclude_pids:
            continue
        try:
            exe_norm = str(Path(exe).resolve()).lower()
        except (OSError, ValueError):
            exe_norm = str(exe).lower()
        if exe_norm not in shim_paths:
            continue

        cmdline = info.get("cmdline") or []
        if isinstance(cmdline, str):
            cmd_text = cmdline.lower()
        else:
            cmd_text = " ".join(str(part) for part in cmdline).lower()
        if "gateway" not in cmd_text or "run" not in cmd_text:
            continue
        name = info.get("name") or Path(exe).name
        matches.append((int(pid), str(name), _profile_from_cmdline(cmdline)))

    return matches
