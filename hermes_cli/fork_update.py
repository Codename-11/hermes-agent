"""Fork-only update / deploy-branch helpers.

EXTRACTED FROM ``hermes_cli/main.py`` to shrink the fork's footprint in that
file. ``main.py`` is upstream's most actively-refactored module (the ongoing
"god-file Phase 2" subcommand/parser extraction), so every fork-only line that
lived there collided with upstream merges on a near-daily basis.

These helpers implement the deploy-branch update flow
(upstream/main -> origin/<deploy> -> live checkout), the update handoff marker,
managed-worktree cleanup, deploy-branch stash preservation, dashboard-service
PID discovery, and Windows gateway-launcher detection. None of them exist
upstream, so they carry cleanly here with zero merge surface in main.py.

SEAM CONTRACT (see FORK.md):
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
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional


logger = logging.getLogger("hermes_cli.fork_update")

# Fork-only: relative path under HERMES_HOME for the deploy-branch update
# handoff marker. Not referenced upstream.
DEPLOY_HANDOFF_FILE = ".update_handoff.json"
UPDATE_REVIEW_DIR = "update-reports"
DEPLOY_BRANCHES = {"axiom", "tgi"}


FORK_WATCH_AREAS: tuple[dict[str, object], ...] = (
    {
        "name": "Deploy-branch-safe updater",
        "paths": (
            "hermes_cli/fork_update.py",
            "hermes_cli/main.py",
            "tests/hermes_cli/test_update_autostash.py",
            "tests/hermes_cli/test_cmd_update.py",
        ),
        "checks": (
            "python -m py_compile hermes_cli/main.py hermes_cli/fork_update.py",
            "python -m pytest -o addopts= -q tests/hermes_cli/test_update_autostash.py tests/hermes_cli/test_cmd_update.py",
        ),
    },
    {
        "name": "Desktop OAuth remote artifact opening",
        "paths": (
            "apps/desktop/electron/main.ts",
            "apps/desktop/electron/preload.ts",
            "apps/desktop/src/global.d.ts",
            "apps/desktop/src/app/artifacts/",
            "apps/desktop/src/lib/media",
        ),
        "checks": (
            "cd apps/desktop && npx vitest run --environment jsdom src/lib/media.remote.test.ts src/lib/desktop-fs.test.ts src/app/artifacts/index.test.ts",
            "cd apps/desktop && npm run typecheck",
        ),
    },
    {
        "name": "Desktop remote profile handles / remote routing",
        "paths": (
            "apps/desktop/electron/connection-config.ts",
            "apps/desktop/electron/main.ts",
            "apps/desktop/src/store/profile.ts",
            "apps/desktop/src/app/settings/gateway-settings.tsx",
        ),
        "checks": (
            "cd apps/desktop && npx vitest run --project electron electron/connection-config.test.ts",
            "cd apps/desktop && npm run typecheck",
        ),
    },
    {
        "name": "Slack channel/session behavior",
        "paths": (
            "gateway/platforms/slack.py",
            "gateway/platforms/base.py",
            "gateway/run.py",
            "gateway/session.py",
            "gateway/config.py",
            "tests/gateway/test_slack",
        ),
        "checks": (
            "python -m pytest -o addopts= -q tests/gateway/test_slack.py tests/gateway/test_slack_mention.py tests/gateway/test_slack_channel_session_scope.py tests/gateway/test_slack_session_model.py",
        ),
    },
    {
        "name": "Anthropic Claude OAuth billing-lane fixes",
        "paths": (
            "agent/anthropic_adapter.py",
            "agent/transports/anthropic.py",
            "tests/agent/test_anthropic_adapter.py",
            "tests/agent/test_anthropic_oauth_system_relocation.py",
        ),
        "checks": (
            "python -m py_compile agent/anthropic_adapter.py agent/transports/anthropic.py",
            "python -m pytest -o addopts= -q tests/agent/test_anthropic_adapter.py tests/agent/test_anthropic_oauth_system_relocation.py",
        ),
    },
    {
        "name": "Live MCP/tool-schema refresh",
        "paths": (
            "agent/agent_init.py",
            "agent/chat_completion_helpers.py",
            "tools/mcp_tool.py",
            "tests/agent/test_live_tool_schema_refresh.py",
            "tests/tools/test_mcp_tool.py",
        ),
        "checks": (
            "python -m pytest -o addopts= -q tests/agent/test_live_tool_schema_refresh.py tests/tools/test_mcp_tool.py::TestMCPServerTask::test_refresh_tools_replaces_schema_for_unchanged_tool_name",
        ),
    },
    {
        "name": "Webhook route-level toolsets",
        "paths": (
            "gateway/platforms/webhook.py",
            "gateway/run.py",
            "hermes_cli/webhook.py",
            "tests/gateway/test_webhook_adapter.py",
            "tests/hermes_cli/test_webhook_cli.py",
        ),
        "checks": (
            "python -m pytest -o addopts= -q tests/gateway/test_webhook_adapter.py tests/hermes_cli/test_webhook_cli.py",
        ),
    },
    {
        "name": "A2A inter-agent communication",
        "paths": (
            "plugins/platforms/a2a/",
            "tests/plugins/test_a2a_plugin.py",
            "hermes_cli/tools_config.py",
        ),
        "checks": (
            "python -m py_compile plugins/platforms/a2a/adapter.py plugins/platforms/a2a/tools.py plugins/platforms/a2a/protocol.py",
            "python -m pytest -o addopts= -q tests/plugins/test_a2a_plugin.py",
        ),
    },
)


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

    shortcut_dirs: list[Path] = []
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell:
        try:
            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-Command",
                    "$encoding = [System.Text.UTF8Encoding]::new($false); "
                    "[Console]::OutputEncoding = $encoding; "
                    "$OutputEncoding = $encoding; "
                    "[Environment]::GetFolderPath('Programs'); "
                    "[Environment]::GetFolderPath('Desktop')",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                shortcut_dirs.extend(
                    Path(line.strip())
                    for line in (result.stdout or "").splitlines()
                    if line.strip()
                )
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass

    # Fall back to conventional locations when Known Folder lookup is not
    # available. Keep these candidates even after a successful lookup to
    # detect shortcuts left behind by older installers.
    userprofile = os.environ.get("USERPROFILE")
    appdata = os.environ.get("APPDATA")
    if userprofile:
        shortcut_dirs.append(Path(userprofile) / "Desktop")
    if appdata:
        shortcut_dirs.append(
            Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        )

    shortcut_names = ("Hermes.lnk", "Hermes Desktop.lnk")
    return any(
        (shortcut_dir / shortcut_name).exists()
        for shortcut_dir in shortcut_dirs
        for shortcut_name in shortcut_names
    )


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


def _git_output(git_cmd: list[str], cwd: Path, args: list[str], *, limit: int = 8000) -> str:
    """Best-effort git output helper for update reports."""
    try:
        result = subprocess.run(
            git_cmd + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    text = (result.stdout or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "\n…(truncated)…"
    return text


def _matched_fork_watch_areas(paths: list[str]) -> list[dict[str, object]]:
    normalized = [p.replace("\\", "/") for p in paths]
    matched: list[dict[str, object]] = []
    for area in FORK_WATCH_AREAS:
        prefixes = tuple(str(p).replace("\\", "/") for p in area.get("paths", ()))
        if any(path.startswith(prefix) for path in normalized for prefix in prefixes):
            matched.append(area)
    return matched


def _review_reports_dir() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / UPDATE_REVIEW_DIR


def _build_update_review_prompt(review: dict[str, object]) -> str:
    conflict_files = "\n".join(f"- {f}" for f in review.get("conflict_files", []) or []) or "- none reported"
    watch_areas = review.get("watch_areas", []) or []
    watch_text = "\n".join(
        f"- {area.get('name')}\n  checks: " + "; ".join(str(c) for c in area.get("checks", ())[:4])
        for area in watch_areas
    ) or "- none matched"
    incoming = str(review.get("incoming_commits") or "").strip() or "(not available)"
    status = str(review.get("worktree_status") or "").strip() or "(not available)"
    error = str(review.get("error") or "").strip() or "(none)"

    return f"""You are reviewing a Hermes Agent TGI deploy-branch update conflict.

Return a concise human-readable operator brief only. Do not propose automatic
mutation, do not ask the updater to continue unattended, and do not include
secrets. Prefer upstream behavior when it satisfies the same TGI requirement,
but preserve documented TGI operational outcomes until tests prove upstream is
equivalent.

Required output shape:
1. What happened — one or two sentences.
2. Likely fork areas involved — bullets.
3. Safest next move — concrete commands/checks.
4. What not to do — one bullet if relevant.

Context:
- Repo: {review.get('repo')}
- Branch: {review.get('branch')}
- Reason: {review.get('reason')}
- Live HEAD: {review.get('live_head')}
- origin/{review.get('branch')}: {review.get('origin_head')}
- upstream/main: {review.get('upstream_head')}
- Upstream commits not in origin: {review.get('upstream_ahead')}
- Origin commits not in live HEAD: {review.get('origin_ahead')}
- Worktree: {review.get('worktree') or '(none)'}

Conflicting files:
{conflict_files}

Matched TGI fork watch areas:
{watch_text}

Merge error excerpt:
{error[:1200]}

Worktree status:
{status[:2000]}

Incoming upstream commits:
{incoming[:3000]}
"""


class _UpdateStatus:
    """Deploy-update progress wrapper with TTY status line + log-safe fallback."""

    def __init__(self, phases: list[str], *, label: str = ""):
        self._phases = phases
        self._label = label
        self._active = ""
        self._done = False
        self._completed: set[str] = set()
        try:
            from hermes_cli.update_ui import StatusLine

            self._line = StatusLine()
        except Exception:
            logger.debug("Update status line unavailable", exc_info=True)
            self._line = None

    @property
    def _interactive(self) -> bool:
        return bool(self._line is not None and self._line.is_interactive)

    def _format(self, phase: str) -> str:
        prefix = f"{self._label}: " if self._label else ""
        return f"{prefix}{phase}"

    def start(self, phase: str) -> None:
        if phase not in self._phases:
            return
        self._active = phase
        if self._line is not None:
            self._line.start(self._format(phase))
        else:
            print(f"→ {self._format(phase)}", flush=True)

    def advance(self, phase: str) -> None:
        if self._done or phase not in self._phases:
            return
        if self._interactive:
            self._active = phase
            if self._line is not None:
                self._line.update(self._format(phase))
            return
        if self._active and self._active not in self._completed:
            self._completed.add(self._active)
            print(f"  ✓ {self._format(self._active)}", flush=True)
        self._active = phase
        print(f"→ {self._format(phase)}", flush=True)

    def finish(self, *, note: str = "") -> None:
        if self._done:
            return
        self._done = True
        if self._interactive:
            if self._line is not None:
                self._line.success(note=note)
            return
        if self._active and self._active not in self._completed:
            self._completed.add(self._active)
            print(f"  ✓ {self._format(self._active)}", flush=True)
        if note:
            print(f"  {note}", flush=True)

    def fail(self, *, note: str = "") -> None:
        if self._done:
            return
        self._done = True
        if self._interactive:
            if self._line is not None:
                self._line.fail(note=note or self._format(self._active))
            return
        if self._active:
            suffix = f" — {note}" if note else ""
            print(f"  ✗ {self._format(self._active)}{suffix}", flush=True)
        elif note:
            print(f"  ✗ {note}", flush=True)


def _run_conflict_review_status(label: str, fn):
    """Show a clean update status/spinner while expensive handoff prep runs."""
    status = _UpdateStatus([label])
    status.start(label)
    try:
        return fn()
    finally:
        status.finish(note="handoff ready")


def _call_llm_update_review(review: dict[str, object]) -> tuple[str, str]:
    """Return (summary, error) for the best-effort LLM conflict review."""
    try:
        from agent.auxiliary_client import call_llm

        response = call_llm(
            task="update_review",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write concise, production-safe git conflict review briefs. "
                        "You never approve code changes or recommend unattended mutation."
                    ),
                },
                {"role": "user", "content": _build_update_review_prompt(review)},
            ],
            max_tokens=900,
            temperature=0.2,
            timeout=45,
        )
        summary = (response.choices[0].message.content or "").strip()
        return (summary[:6000], "") if summary else ("", "empty LLM response")
    except Exception as exc:
        logger.debug("Update conflict LLM review failed", exc_info=True)
        return "", str(exc).splitlines()[0]


def _deterministic_update_review_summary(review: dict[str, object]) -> str:
    files = review.get("conflict_files", []) or []
    watch_areas = review.get("watch_areas", []) or []
    lines = [
        "What happened: the deploy-branch update hit a conflict in the retained update worktree; the live checkout was not changed.",
    ]
    if files:
        lines.append("Conflicting files: " + ", ".join(str(f) for f in files[:8]))
    if watch_areas:
        lines.append("Likely TGI fork areas involved:")
        for area in watch_areas[:6]:
            lines.append(f"- {area.get('name')}")
    else:
        lines.append("No documented TGI fork watch area matched the conflict files; treat this as a normal upstream merge conflict.")
    checks: list[str] = []
    for area in watch_areas:
        for check in area.get("checks", ()):
            if str(check) not in checks:
                checks.append(str(check))
    lines.append("Safest next move: resolve in the retained worktree, prefer upstream when it preserves the documented TGI outcome, then run focused tests before pushing HEAD back to the deploy branch.")
    if checks:
        lines.append("Focused checks to consider:")
        for check in checks[:6]:
            lines.append(f"- {check}")
    lines.append("Do not auto-approve or continue unattended from this state.")
    return "\n".join(lines)


def _write_update_review_report(review: dict[str, object]) -> Optional[Path]:
    try:
        reports_dir = _review_reports_dir()
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        branch = str(review.get("branch") or "deploy").replace("/", "-")
        report_path = reports_dir / f"{stamp}-{branch}-conflict-review.md"
        files = review.get("conflict_files", []) or []
        watch_areas = review.get("watch_areas", []) or []
        llm_error = str(review.get("llm_error") or "").strip()
        content = [
            f"# Hermes update conflict review — {branch}",
            "",
            f"Created: {datetime.now().isoformat(timespec='seconds')}",
            f"Repo: `{review.get('repo')}`",
            f"Worktree: `{review.get('worktree') or ''}`",
            f"Reason: {review.get('reason')}",
            "",
            "## Refs",
            "",
            f"- Live HEAD: `{review.get('live_head')}`",
            f"- Origin deploy: `{review.get('origin_head')}`",
            f"- Upstream main: `{review.get('upstream_head')}`",
            f"- Upstream commits not in origin: `{review.get('upstream_ahead')}`",
            f"- Origin commits not in live HEAD: `{review.get('origin_ahead')}`",
            "",
            "## LLM / operator brief",
            "",
            str(review.get("llm_summary") or review.get("deterministic_summary") or "").strip(),
            "",
        ]
        if llm_error:
            content.extend(["> LLM review unavailable: " + llm_error, ""])
        content.extend(["## Conflicting files", ""])
        content.extend([f"- `{f}`" for f in files] or ["- none reported"])
        content.extend(["", "## Matched TGI fork watch areas", ""])
        if watch_areas:
            for area in watch_areas:
                content.append(f"### {area.get('name')}")
                content.append("")
                content.append("Focused checks:")
                for check in area.get("checks", ()):
                    content.append(f"- `{check}`")
                content.append("")
        else:
            content.append("No documented watch area matched these files.")
            content.append("")
        for title, key in (
            ("Worktree status", "worktree_status"),
            ("Incoming upstream commits", "incoming_commits"),
            ("Merge error excerpt", "error"),
        ):
            value = str(review.get(key) or "").strip()
            if value:
                content.extend([f"## {title}", "", "```text", value, "```", ""])
        report_path.write_text("\n".join(content).rstrip() + "\n", encoding="utf-8")
        return report_path
    except Exception:
        logger.debug("Failed to write update conflict review report", exc_info=True)
        return None


def _generate_update_conflict_review(
    *,
    reason: str,
    repo: Path,
    branch: str,
    upstream_ahead: int,
    origin_ahead: int,
    worktree_path: Optional[Path],
    conflict_files: str,
    error: str,
    git_cmd: list[str],
) -> dict[str, object]:
    files = [line.strip() for line in (conflict_files or "").splitlines() if line.strip()]
    watch_areas = _matched_fork_watch_areas(files)
    worktree_status = _git_output(git_cmd, worktree_path, ["status", "--short", "--branch"], limit=4000) if worktree_path else ""
    incoming_commits = _git_output(
        git_cmd,
        repo,
        ["log", "--oneline", "--no-merges", f"origin/{branch}..upstream/main", "--", *(files or [])],
        limit=5000,
    )
    review: dict[str, object] = {
        "repo": str(repo),
        "branch": branch,
        "reason": reason,
        "worktree": str(worktree_path) if worktree_path is not None else "",
        "conflict_files": files,
        "error": error,
        "upstream_ahead": upstream_ahead,
        "origin_ahead": origin_ahead,
        "live_head": _short_git_ref(git_cmd, repo, "HEAD"),
        "origin_head": _short_git_ref(git_cmd, repo, f"origin/{branch}"),
        "upstream_head": _short_git_ref(git_cmd, repo, "upstream/main"),
        "watch_areas": watch_areas,
        "worktree_status": worktree_status,
        "incoming_commits": incoming_commits,
    }
    review["deterministic_summary"] = _deterministic_update_review_summary(review)
    llm_summary, llm_error = _run_conflict_review_status(
        "review conflict handoff",
        lambda: _call_llm_update_review(review),
    )
    review["llm_summary"] = llm_summary
    review["llm_error"] = llm_error
    report_path = _write_update_review_report(review)
    review["report_path"] = str(report_path) if report_path is not None else ""
    return review


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
    conflict_files: str | list[str] = "",
    review: dict[str, object] | None = None,
) -> None:
    # Only merge failures with a retained worktree are resumable. Recording
    # fetch/ref-classification failures creates a marker with ``worktree: ""``;
    # the next update then enters the resolver and can never make progress.
    if worktree_path is None or not worktree_path.is_dir():
        return
    try:
        marker = _deploy_handoff_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(conflict_files, str):
            conflict_list = [line.strip() for line in conflict_files.splitlines() if line.strip()]
        else:
            conflict_list = [str(line).strip() for line in conflict_files if str(line).strip()]
        watch_areas = []
        focused_checks: list[str] = []
        if review:
            review_areas = review.get("watch_areas", [])
            if isinstance(review_areas, (list, tuple)):
                iterable_areas = review_areas
            else:
                iterable_areas = []
            for area in iterable_areas:
                if not isinstance(area, dict):
                    continue
                area_checks = area.get("checks", ())
                checks = [str(c) for c in area_checks] if isinstance(area_checks, (list, tuple)) else []
                area_paths = area.get("paths", ())
                paths = [str(p) for p in area_paths] if isinstance(area_paths, (list, tuple)) else []
                watch_areas.append({
                    "name": str(area.get("name") or ""),
                    "paths": paths,
                    "checks": checks,
                })
                for check in checks:
                    if check not in focused_checks:
                        focused_checks.append(check)
        elif conflict_list:
            for area in _matched_fork_watch_areas(conflict_list):
                area_checks = area.get("checks", ())
                checks = [str(c) for c in area_checks] if isinstance(area_checks, (list, tuple)) else []
                area_paths = area.get("paths", ())
                paths = [str(p) for p in area_paths] if isinstance(area_paths, (list, tuple)) else []
                watch_areas.append({
                    "name": str(area.get("name") or ""),
                    "paths": paths,
                    "checks": checks,
                })
                for check in checks:
                    if check not in focused_checks:
                        focused_checks.append(check)
        payload = {
            "schema": 2,
            "repo": str(repo),
            "branch": branch,
            "reason": reason,
            "worktree": str(worktree_path) if worktree_path is not None else "",
            "conflict_files": conflict_list,
            "report_path": str(review.get("report_path") or "") if review else "",
            "watch_areas": watch_areas,
            "focused_checks": focused_checks,
            "live_head": _short_git_ref(["git"], repo, "HEAD"),
            "origin_head": _short_git_ref(["git"], repo, f"origin/{branch}"),
            "upstream_head": _short_git_ref(["git"], repo, "upstream/main"),
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


def _read_deploy_handoff_payload(repo: Path, branch: str) -> dict[str, object] | None:
    marker = _deploy_handoff_marker_path()
    if not marker.exists():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        print("✗ Deploy handoff marker exists but could not be read.")
        return None
    if not isinstance(payload, dict):
        print("✗ Deploy handoff marker is malformed.")
        return None
    if payload.get("branch") != branch:
        print(
            f"✗ Deploy handoff is for branch {payload.get('branch')!r}, "
            f"not {branch!r}."
        )
        return None
    recorded_repo = str(payload.get("repo") or "")
    if recorded_repo:
        try:
            if Path(recorded_repo).resolve() != repo.resolve():
                print(f"✗ Deploy handoff is for a different repo: {recorded_repo}")
                return None
        except OSError:
            print(f"✗ Deploy handoff repo is not accessible: {recorded_repo}")
            return None
    return payload


def _deploy_handoff_exists_for(repo: Path, branch: str) -> bool:
    return _read_deploy_handoff_payload(repo, branch) is not None


def _handoff_conflict_files(git_cmd: list[str], worktree: Path, payload: dict[str, object]) -> list[str]:
    marker_files = payload.get("conflict_files")
    files: list[str] = []
    if isinstance(marker_files, list):
        files = [str(item).strip() for item in marker_files if str(item).strip()]
    if files:
        return files
    unmerged = _git_output(git_cmd, worktree, ["diff", "--name-only", "--diff-filter=U"], limit=4000)
    return [line.strip() for line in unmerged.splitlines() if line.strip()]


def _egregious_handoff_paths(paths: list[str]) -> list[str]:
    blocked: list[str] = []
    sensitive_names = {
        ".env", "auth.json", "credentials.json", "id_rsa", "id_ed25519",
        "known_hosts", "authorized_keys",
    }
    sensitive_suffixes = (".pem", ".key", ".p12", ".pfx")
    sensitive_fragments = ("/secrets/", "/.ssh/", "private_key")
    for raw_path in paths:
        norm = raw_path.replace("\\", "/")
        name = Path(norm).name.lower()
        lower = norm.lower()
        if name in sensitive_names or name.endswith(sensitive_suffixes) or any(fragment in lower for fragment in sensitive_fragments):
            blocked.append(raw_path)
    return blocked


def _has_git_state(git_cmd: list[str], cwd: Path, state_name: str) -> bool:
    path = _git_output(git_cmd, cwd, ["rev-parse", "--git-path", state_name], limit=1000)
    return bool(path and (cwd / path).exists())


def _scan_conflict_markers(worktree: Path, paths: list[str]) -> list[str]:
    offenders: list[str] = []
    for rel in paths:
        candidate = worktree / rel
        try:
            if not candidate.is_file() or candidate.stat().st_size > 2_000_000:
                continue
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(line.startswith(("<<<<<<< ", "=======", ">>>>>>> ")) for line in text.splitlines()):
            offenders.append(rel)
    return offenders


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _focused_checks_for_paths(paths: list[str], payload: dict[str, object]) -> list[str]:
    checks: list[str] = []
    matched_areas = _matched_fork_watch_areas(paths)
    for area in matched_areas:
        area_checks = area.get("checks", ())
        if isinstance(area_checks, (list, tuple)):
            checks.extend(str(check) for check in area_checks if str(check).strip())
    # Handoff markers snapshot the suggested checks at conflict time. Prefer
    # the live watch-area contract when one still matches so a retained
    # handoff does not keep invoking checks for files that were renamed or
    # retired while the deploy branch advanced. Unmatched/custom handoffs keep
    # their marker-provided checks as a fallback.
    if not matched_areas:
        marker_checks = payload.get("focused_checks")
        if isinstance(marker_checks, list):
            checks.extend(str(check) for check in marker_checks if str(check).strip())
    unique: list[str] = []
    for check in checks:
        if check not in unique:
            unique.append(check)
    if unique:
        return unique
    py_files = [path for path in paths if path.endswith(".py")]
    if py_files:
        quoted = " ".join(shlex_quote(path) for path in py_files[:20])
        return [f"python -m py_compile {quoted}"]
    return []


def _focused_check_env() -> dict[str, str]:
    """Return an environment where ``python`` is this Hermes interpreter.

    Resolver checks are shell snippets retained in handoff metadata. Minimal
    Linux hosts commonly install only ``python3``; putting the active venv's
    bin directory first keeps existing cross-platform check strings working
    without relying on a host-level ``python`` shim.
    """
    env = os.environ.copy()
    # Keep the launcher path intact. Virtualenv interpreters are commonly
    # symlinks into uv/pyenv/base-Python installs; resolving that symlink would
    # put the base interpreter (without this environment's pytest/deps) on PATH.
    interpreter_dir = str(Path(sys.executable).parent)
    current_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(part for part in (interpreter_dir, current_path) if part)
    return env


def _focused_pytest_requirements() -> list[str]:
    """Return the pytest packages declared by the checkout's ``dev`` extra."""
    fallback = ["pytest", "pytest-asyncio"]
    try:
        import importlib

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with pyproject.open("rb") as handle:
            data = importlib.import_module("tomllib").load(handle)
        dev = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
        requirements = [
            str(item)
            for item in dev
            if str(item).lower().split(";", 1)[0].strip().startswith(
                ("pytest=", "pytest<", "pytest>", "pytest-asyncio")
            )
        ]
        return requirements or fallback
    except Exception:
        return fallback


def _ensure_focused_pytest(checks: list[str], env: dict[str, str]) -> bool:
    """Install optional pytest tooling only when resolver checks require it."""
    if not any("pytest" in check and "-m" in check for check in checks):
        return True

    probe = subprocess.run(
        [sys.executable, "-c", "import pytest, pytest_asyncio"],
        capture_output=True,
        text=True,
        env=env,
    )
    if probe.returncode == 0:
        return True

    requirements = _focused_pytest_requirements()
    uv = shutil.which("uv", path=env.get("PATH"))
    if uv:
        command = [uv, "pip", "install", "--python", sys.executable, *requirements]
    else:
        command = [sys.executable, "-m", "pip", "install", *requirements]

    print("→ Installing optional resolver test tooling: " + ", ".join(requirements))
    installed = subprocess.run(command, text=True, timeout=300, env=env)
    if installed.returncode != 0:
        print("✗ Could not install pytest tooling required by focused checks.")
        return False

    verified = subprocess.run(
        [sys.executable, "-c", "import pytest, pytest_asyncio"],
        capture_output=True,
        text=True,
        env=env,
    )
    return verified.returncode == 0


@contextmanager
def _focused_node_modules(worktree: Path, checks: list[str]) -> Iterator[None]:
    """Expose the live install's Node dependencies to a resolver worktree.

    Update worktrees intentionally do not install dependencies.  Desktop
    focused checks otherwise invoke ``npx``, create a partial local
    ``node_modules/.vite-temp``, and fail before collecting tests because
    ``vitest/config`` cannot resolve.  Reuse the already-installed live root
    dependencies for the duration of validation, then remove only our symlink.
    """
    needs_node = any(
        token in check
        for check in checks
        for token in ("npm ", "npx ", "node ")
    )
    live_root = Path(__file__).resolve().parents[1]
    candidates = [(worktree / "node_modules", live_root / "node_modules")]
    if any("apps/desktop" in check for check in checks):
        candidates.append(
            (
                worktree / "apps" / "desktop" / "node_modules",
                live_root / "apps" / "desktop" / "node_modules",
            )
        )
    created: list[Path] = []
    for link, live_modules in candidates:
        if not needs_node or link.exists() or not live_modules.is_dir():
            continue
        try:
            link.symlink_to(live_modules, target_is_directory=True)
            created.append(link)
        except OSError:
            logger.debug("Could not link resolver Node dependencies", exc_info=True)
    if created:
        print("  ✓ Reusing installed Node dependencies for focused checks")
    try:
        yield
    finally:
        for link in reversed(created):
            try:
                link.unlink()
            except OSError:
                logger.debug("Could not remove resolver Node dependency link", exc_info=True)


def _handoff_snapshot_is_published(
    git_cmd: list[str],
    repo: Path,
    branch: str,
    payload: dict[str, object],
) -> bool:
    """Return whether both refs captured by a handoff reached origin."""
    recorded_refs = [
        str(payload.get("origin_head") or "").strip(),
        str(payload.get("upstream_head") or "").strip(),
    ]
    if not all(recorded_refs):
        return False

    remote_ref = f"origin/{branch}"
    for recorded_ref in recorded_refs:
        published = subprocess.run(
            git_cmd + ["merge-base", "--is-ancestor", recorded_ref, remote_ref],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if published.returncode != 0:
            return False
    return True


def _handoff_origin_is_behind(
    git_cmd: list[str],
    repo: Path,
    branch: str,
    payload: dict[str, object],
) -> bool:
    """Return whether a handoff's origin base is an ancestor of the newer tip."""
    recorded_origin = str(payload.get("origin_head") or "").strip()
    if not recorded_origin:
        return False
    remote_ref = f"origin/{branch}"
    base_in_origin = subprocess.run(
        git_cmd + ["merge-base", "--is-ancestor", recorded_origin, remote_ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if base_in_origin.returncode != 0:
        return False
    origin_in_base = subprocess.run(
        git_cmd + ["merge-base", "--is-ancestor", remote_ref, recorded_origin],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return origin_in_base.returncode != 0


def _remove_managed_update_worktree(
    git_cmd: list[str], repo: Path, worktree: Path
) -> bool:
    """Ask Git to remove an updater-owned temp worktree; never recurse-delete it."""
    parent = worktree.parent
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        parent_in_temp = parent.resolve().is_relative_to(temp_root)
    except OSError:
        parent_in_temp = False
    updater_owned = (
        parent_in_temp
        and worktree.name == "worktree"
        and parent.name.startswith("hermes-update-")
    )
    if not updater_owned:
        return False

    removed = subprocess.run(
        git_cmd + ["worktree", "remove", str(worktree), "--force"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if removed.returncode != 0:
        return False
    try:
        parent.rmdir()
    except OSError:
        pass
    return True


def _discard_published_handoff(
    git_cmd: list[str], repo: Path, worktree: Path
) -> bool:
    marker = _deploy_handoff_marker_path()
    try:
        marker.unlink()
    except OSError:
        logger.debug("Failed to clear published deploy handoff marker", exc_info=True)
        return False

    _remove_managed_update_worktree(git_cmd, repo, worktree)
    return True


def _build_deploy_resolver_prompt(payload: dict[str, object], checks: list[str]) -> str:
    conflict_files = payload.get("conflict_files")
    files = "\n".join(f"- {item}" for item in conflict_files) if isinstance(conflict_files, list) else "- inspect git status"
    checks_text = "\n".join(f"- {check}" for check in checks) or "- run the narrowest relevant compile/test checks for touched files"
    report_path = str(payload.get("report_path") or "").strip()
    return f"""Resolve the retained Hermes deploy-branch update handoff to completion.

Repo: {payload.get('repo')}
Deploy branch: {payload.get('branch')}
Retained worktree: {payload.get('worktree')}
Reason: {payload.get('reason')}
Conflict review report: {report_path or '(none)'}

Conflicting files from the updater marker:
{files}

Required local references to read before editing when present:
- FORK.md
- ~/obsidian-vault/3. System/Projects/TGI/
- skill_view(name="hermes-update") when the skills tool is available

Resolver contract:
1. Work only inside the retained worktree above.
2. Resolve the git merge conflict, preserving documented deploy-branch/TGI behavior and preferring upstream code when it provides equivalent or better behavior.
3. Do not touch secrets, auth tokens, .env files, or unrelated generated churn.
4. Run focused verification. Suggested checks:
{checks_text}
5. Leave the worktree ready for the updater to commit/push: no unmerged paths, no conflict markers, and only justified changes.

Do not push or run `hermes update` yourself; the parent updater will validate, commit, push, fast-forward the live checkout, and run the normal install/restart phase after you exit.
"""


def _resolver_cli_bootstrap(worktree: Path) -> str:
    """Import the clean live CLI while keeping *worktree* as process cwd.

    A merge conflict may leave ``hermes_cli/main.py`` unparsable inside the
    retained worktree. ``python -m hermes_cli.main`` would import that broken
    file before the resolver can start. Remove cwd from ``sys.path`` and pin
    imports to the live checkout that launched the parent updater; the child
    process still inherits the worktree cwd for terminal/file tools.
    """
    live_root = str(Path(__file__).resolve().parents[1])
    worktree_root = str(worktree.resolve())
    return (
        "import sys; "
        f"sys.path[:] = [{live_root!r}] + "
        f"[p for p in sys.path if p not in ('', {live_root!r}, {worktree_root!r})]; "
        "from hermes_cli.main import main; main()"
    )


def _run_update_resolver_agent(prompt: str, worktree: Path) -> subprocess.CompletedProcess:
    """Run a non-interactive Hermes resolver session in the retained worktree.

    The parent updater owns user-facing progress and validation. Capture the
    child agent's transcript so optimistic final self-reports do not appear as
    authoritative status before the parent has verified the worktree.
    """
    timeout = int(os.environ.get("HERMES_UPDATE_RESOLVE_TIMEOUT", "3600") or "3600")
    cmd = [
        sys.executable,
        "-c",
        _resolver_cli_bootstrap(worktree),
        "-z",
        prompt,
        "-t",
        "terminal,file,search,skills",
    ]
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "HERMES_UPDATE_RESOLVE": "1"}
    return subprocess.run(
        cmd,
        cwd=worktree,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        capture_output=True,
    )


def _resolve_deploy_handoff(
    *,
    git_cmd: list[str],
    repo: Path,
    branch: str,
    pre_update_head: str,
) -> Optional[int]:
    """Autonomously resolve a retained deploy update handoff.

    Bare ``hermes update`` resumes the retained conflict worktree, runs focused
    checks, pushes the deploy branch, and lets the parent updater continue with
    install/restart. Hard safety gates still stop before ambiguous mutations.
    """
    payload = _read_deploy_handoff_payload(repo, branch)
    if payload is None:
        return None

    worktree_raw = str(payload.get("worktree") or "").strip()
    worktree = Path(worktree_raw) if worktree_raw else Path()
    status = _UpdateStatus(
        [
            "prepare resolve",
            "agent resolve",
            "validate",
            "focused checks",
            "commit",
            "push",
            "sync live",
            "cleanup",
        ],
        label="resolve",
    )
    print("→ Resolving retained deploy handoff with Hermes agent...")
    if worktree_raw:
        print(f"  Worktree: {worktree}")
    status.start("prepare resolve")
    fetches = [
        (
            "origin",
            subprocess.run(
                git_cmd
                + ["fetch", "origin", f"{branch}:refs/remotes/origin/{branch}"],
                cwd=repo,
                capture_output=True,
                text=True,
            ),
        ),
        (
            "upstream",
            subprocess.run(
                git_cmd + ["fetch", "upstream", "main", "--quiet"],
                cwd=repo,
                capture_output=True,
                text=True,
            ),
        ),
    ]
    failed_fetches = [(name, result) for name, result in fetches if result.returncode != 0]
    if failed_fetches:
        status.fail(note="fetch failed")
        print("✗ Could not refresh deploy refs before resolver classification.")
        for name, result in failed_fetches:
            print(f"  {name} fetch exit code: {result.returncode}")
            details = str(result.stderr or result.stdout or "").strip().splitlines()[-20:]
            for line in details:
                print(f"    {line}")
        return None

    if _handoff_snapshot_is_published(git_cmd, repo, branch, payload):
        if not _discard_published_handoff(git_cmd, repo, worktree):
            status.fail(note="stale marker cleanup failed")
            print("✗ Could not clear published deploy handoff marker; stopping before fresh update.")
            return None
        status.finish(note="published snapshot cleared")
        print(
            f"→ Retained handoff snapshot is already published on origin/{branch}; "
            "starting a fresh deploy update."
        )
        return _run_deploy_branch_update(
            git_cmd, repo, branch, pre_update_head
        )

    if _handoff_origin_is_behind(git_cmd, repo, branch, payload):
        if not _discard_published_handoff(git_cmd, repo, worktree):
            status.fail(note="superseded marker cleanup failed")
            print("✗ Could not clear superseded deploy handoff marker; stopping.")
            return None
        status.finish(note="superseded base cleared")
        print(
            f"→ origin/{branch} advanced after this handoff was created; "
            "rebuilding once from the current deploy tip."
        )
        return _run_deploy_branch_update(
            git_cmd, repo, branch, pre_update_head
        )

    if not worktree_raw or not worktree.exists():
        print("⚠ Retained deploy handoff worktree is missing.")
        if _completed_deploy_handoff_requires_post_update(git_cmd, repo, branch):
            status.finish(note="published handoff cleared")
            return 1
        if not _discard_published_handoff(git_cmd, repo, worktree):
            status.fail(note="stale marker cleanup failed")
            print("✗ Could not clear the non-resumable deploy handoff marker.")
            return None
        status.finish(note="missing worktree cleared")
        print("→ Discarded non-resumable handoff; rebuilding once from current refs.")
        return _run_deploy_branch_update(
            git_cmd, repo, branch, pre_update_head
        )

    conflict_files = _handoff_conflict_files(git_cmd, worktree, payload)
    blocked = _egregious_handoff_paths(conflict_files)
    if blocked:
        status.fail(note="sensitive path gate")
        print("✗ Refusing unattended resolve: conflict touches sensitive paths:")
        for item in blocked:
            print(f"  - {item}")
        print("  Resolve manually or rerun with a future explicit override once reviewed.")
        return None

    checks = _focused_checks_for_paths(conflict_files, payload)
    status.advance("agent resolve")
    result = _run_update_resolver_agent(_build_deploy_resolver_prompt({**payload, "conflict_files": conflict_files}, checks), worktree)
    if result.returncode != 0:
        status.fail(note="resolver agent failed")
        print("✗ Resolver agent failed; retained worktree was left untouched for manual review.")
        return None

    status.advance("validate")
    unmerged = _git_output(git_cmd, worktree, ["diff", "--name-only", "--diff-filter=U"], limit=4000)
    remaining = [line.strip() for line in unmerged.splitlines() if line.strip()]
    if remaining:
        status.fail(note="unmerged files remain")
        print("✗ Resolver exited but unmerged files remain:")
        for item in remaining[:20]:
            print(f"  - {item}")
        return None

    marker_files = conflict_files or _git_output(git_cmd, worktree, ["diff", "--name-only", "HEAD"], limit=4000).splitlines()
    marker_hits = _scan_conflict_markers(worktree, [line.strip() for line in marker_files if line.strip()])
    if marker_hits:
        status.fail(note="conflict markers remain")
        print("✗ Resolver left conflict markers in files:")
        for item in marker_hits[:20]:
            print(f"  - {item}")
        return None

    status.advance("focused checks")
    check_env = _focused_check_env()
    if not _ensure_focused_pytest(checks, check_env):
        status.fail(note="pytest tooling unavailable")
        return None
    with _focused_node_modules(worktree, checks):
        for check in checks:
            print(f"→ Focused check: {check}")
            check_result = subprocess.run(
                check,
                cwd=worktree,
                shell=True,
                text=True,
                timeout=900,
                env=check_env,
            )
            if check_result.returncode != 0:
                status.fail(note="focused check failed")
                print(f"✗ Focused check failed: {check}")
                return None

    status.advance("commit")
    subprocess.run(git_cmd + ["add", "-A"], cwd=worktree, capture_output=True, text=True)
    commit_needed = subprocess.run(git_cmd + ["diff", "--cached", "--quiet"], cwd=worktree)
    if commit_needed.returncode != 0:
        if _has_git_state(git_cmd, worktree, "MERGE_HEAD"):
            commit = subprocess.run(git_cmd + ["commit", "--no-edit"], cwd=worktree, text=True)
        else:
            commit = subprocess.run(
                git_cmd + ["commit", "-m", f"merge: resolve {branch} deploy update"],
                cwd=worktree,
                text=True,
            )
        if commit.returncode != 0:
            status.fail(note="commit failed")
            print("✗ Could not commit resolver changes.")
            return None

    status.advance("push")
    push = subprocess.run(
        git_cmd + ["push", "origin", f"HEAD:{branch}"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        status.fail(note="push failed")
        print(f"✗ Could not push resolved deploy branch origin/{branch}.")
        if push.stderr.strip():
            print(f"  {push.stderr.strip().splitlines()[0]}")
        return None

    status.advance("sync live")
    changed = _fast_forward_live_deploy_checkout(git_cmd, repo, branch, pre_update_head, 1)
    if changed is None:
        status.fail(note="live fast-forward failed")
        print(f"✗ Resolved branch pushed, but live checkout could not fast-forward to origin/{branch}.")
        return None

    status.advance("cleanup")
    marker_cleared = False
    try:
        _deploy_handoff_marker_path().unlink()
        marker_cleared = True
    except OSError:
        logger.debug("Failed to clear deploy handoff marker", exc_info=True)
    if marker_cleared:
        _remove_managed_update_worktree(git_cmd, repo, worktree)

    status.finish(note="resolved handoff")
    print(f"✓ Resolved deploy handoff, pushed origin/{branch}, and fast-forwarded live checkout.")
    return changed or 1


def _sync_deploy_main_to_upstream(git_cmd: list[str], repo: Path) -> bool:
    from hermes_cli.main import _count_commits_between  # lazy: avoid circular import at module load
    main_local = _count_commits_between(git_cmd, repo, "upstream/main", "main")
    main_behind = _count_commits_between(git_cmd, repo, "main", "upstream/main")
    if main_local < 0 or main_behind < 0:
        print("  ✗ Could not compare local main with upstream/main.")
        return False

    if main_local > 0:
        shallow = subprocess.run(
            git_cmd + ["rev-parse", "--is-shallow-repository"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if shallow.returncode == 0 and shallow.stdout.strip() == "true":
            # A depth-1 update/check fetch makes an old local main look like it
            # has hundreds of unpublished commits because the merge base is no
            # longer visible. Deepen the one ref we need, in bounded chunks,
            # and reclassify before refusing to move main.
            for _ in range(16):
                deepen = subprocess.run(
                    git_cmd
                    + [
                        "fetch",
                        "--deepen=1024",
                        "upstream",
                        "main:refs/remotes/upstream/main",
                    ],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                )
                if deepen.returncode != 0:
                    break
                main_local = _count_commits_between(
                    git_cmd, repo, "upstream/main", "main"
                )
                main_behind = _count_commits_between(
                    git_cmd, repo, "main", "upstream/main"
                )
                if main_local < 0 or main_behind < 0 or main_local == 0:
                    break

    if main_local < 0 or main_behind < 0:
        print("  ✗ Could not compare local main with upstream/main after deepening history.")
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
    review: dict[str, object] | None = None
    if conflict_files or (worktree_path is not None and "merge" in reason.lower()):
        review = _generate_update_conflict_review(
            reason=reason,
            repo=repo,
            branch=branch,
            upstream_ahead=upstream_ahead,
            origin_ahead=origin_ahead,
            worktree_path=worktree_path,
            conflict_files=conflict_files,
            error=error,
            git_cmd=git_cmd,
        )
    print()
    if review:
        print("  ── Update conflict review ─────────────────────")
        print()
        summary = str(review.get("llm_summary") or review.get("deterministic_summary") or "").strip()
        for line in summary.splitlines()[:18]:
            print(f"  {line}" if line else "")
        if review.get("llm_error"):
            print(f"  LLM review unavailable; deterministic brief shown. ({review.get('llm_error')})")
        if review.get("report_path"):
            print(f"  Full report: {review.get('report_path')}")
        print()
    print("  ── Update recovery context ────────────────────")
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
    print("  │ Hermes will attempt automatic resolution next. If a safety")
    print("  │ gate stops it, review this retained worktree, resolve the")
    print(f"  │ listed issue, and rerun hermes update to publish {branch}.")
    print("  └────────────────────────────────────────────")
    _record_deploy_handoff(
        repo=repo,
        branch=branch,
        reason=reason,
        worktree_path=worktree_path,
        conflict_files=conflict_files,
        review=review,
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

    TGI Docker, Desktop/client installs, and other Hermes hosts can run
    ``hermes update`` back-to-back. In that flow ``origin/<branch>`` can
    advance after this process created its temp merge worktree but before it
    pushes. A raw push rejection is not yet a
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
    try:
        from hermes_cli.update_ui import Pipeline
    except ModuleNotFoundError:
        class Pipeline:  # minimal fallback for deploy-branch update progress
            def __init__(self, stages: list[str]):
                self.stages = stages

            def start(self, stage: str) -> None:
                print(f"→ {stage}...")

            def advance(self, stage: str) -> None:
                print(f"→ {stage}...")

            def fail(self, note: str = "") -> None:
                print(f"✗ {note or 'update failed'}")

            def finish(self, note: str = "") -> None:
                print(f"✓ {note or 'update complete'}")

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

    # Refresh the tested deploy artifact before comparing refs. Otherwise a
    # client with a stale remote-tracking ref can falsely report origin current
    # and strand itself on an old deploy commit after another host resolved and
    # pushed the integration.
    fetch_deploy = subprocess.run(
        git_cmd
        + [
            "fetch",
            "origin",
            f"{branch}:refs/remotes/origin/{branch}",
            "--quiet",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if fetch_deploy.returncode != 0:
        _pipe.fail(note=f"cannot refresh origin/{branch}")
        _print_deploy_branch_handoff(
            reason=f"cannot fetch origin/{branch}.",
            repo=repo,
            branch=branch,
            error=(fetch_deploy.stderr or "").strip(),
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
            print("  The live checkout is unchanged; starting automatic resolution.")
            resolved = _resolve_deploy_handoff(
                git_cmd=git_cmd,
                repo=repo,
                branch=branch,
                pre_update_head=pre_update_head,
            )
            if resolved is not None:
                return resolved
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
            print("  The live checkout is unchanged; starting automatic resolution.")
            resolved = _resolve_deploy_handoff(
                git_cmd=git_cmd,
                repo=repo,
                branch=branch,
                pre_update_head=pre_update_head,
            )
            if resolved is not None:
                return resolved
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
        print("  The live checkout was left unchanged; starting automatic recovery.")
        resolved = _resolve_deploy_handoff(
            git_cmd=git_cmd,
            repo=repo,
            branch=branch,
            pre_update_head=pre_update_head,
        )
        if resolved is not None:
            return resolved
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
