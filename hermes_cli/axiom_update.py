"""Axiom fork-only update / deploy-branch helpers.

EXTRACTED FROM ``hermes_cli/main.py`` to shrink the fork's footprint in that
file. ``main.py`` is upstream's most actively-refactored module (the ongoing
"god-file Phase 2" subcommand/parser extraction), so every fork-only line that
lived there collided with upstream merges on a near-daily basis.

These helpers implement Axiom/TGI-style deploy-branch update flows
(upstream/main -> origin/<deploy> -> live checkout), the update handoff marker,
managed-worktree cleanup, deploy-branch stash preservation, dashboard-service
PID discovery, Windows gateway-launcher detection, and autonomous handoff
resolution. None of them exist upstream, so they carry cleanly here with zero
merge surface in main.py.

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

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Final, Optional, TypedDict


logger = logging.getLogger("hermes_cli.axiom_update")

# Fork-only: relative path under HERMES_HOME for the deploy-branch update
# handoff marker. Not referenced upstream.
DEPLOY_HANDOFF_FILE = ".update_handoff.json"
UPDATE_REVIEW_DIR = "update-reports"
DEPLOY_BRANCHES = {"axiom", "tgi"}


class CheckSpec(TypedDict):
    id: str
    kind: str
    command: str
    timeout_seconds: int


class ForkWatchAreaSpec(TypedDict):
    id: str
    name: str
    paths: tuple[str, ...]
    invariants: tuple[str, ...]
    prefer_upstream: str
    drop_when: str
    references: tuple[str, ...]
    checks: tuple[CheckSpec, ...]


def _check(id: str, kind: str, command: str, timeout_seconds: int = 900) -> CheckSpec:
    return {"id": id, "kind": kind, "command": command, "timeout_seconds": timeout_seconds}


FORK_WATCH_AREAS: Final[tuple[ForkWatchAreaSpec, ...]] = (
    {
        "id": "deploy-updater",
        "name": "Deploy-branch-safe updater",
        "invariants": ("Child resolves structure only; parent checkpoints, validates, and publishes the exact commit.",),
        "prefer_upstream": "Keep upstream structure when it preserves deploy-branch safety and resumability.",
        "drop_when": "Drop when upstream provides equivalent deploy-branch reconciliation and durable validation handoffs.",
        "references": ("FORK.md#staged-update-lifecycle", "docs/axiom-fork-contract.md"),
        "paths": (
            "hermes_cli/axiom_update.py",
            "hermes_cli/main.py",
            "tests/hermes_cli/test_update_autostash.py",
            "tests/hermes_cli/test_cmd_update.py",
        ),
        "checks": (
            _check("deploy-updater-compile", "py_compile", "python -m py_compile hermes_cli/main.py hermes_cli/axiom_update.py", 120),
            _check("deploy-updater-tests", "pytest", "python -m pytest -o addopts= -q tests/hermes_cli/test_update_autostash.py tests/hermes_cli/test_cmd_update.py"),
        ),
    },
    {
        "id": "desktop-remote-artifacts",
        "name": "Desktop OAuth remote artifact opening",
        "invariants": ("Remote OAuth artifacts open through authenticated Desktop routing.",),
        "prefer_upstream": "Prefer upstream media and filesystem seams when authentication and routing remain equivalent.",
        "drop_when": "Drop when upstream covers authenticated remote artifact opening end to end.",
        "references": ("FORK.md", "docs/axiom-fork-contract.md"),
        "paths": (
            "apps/desktop/electron/main.ts",
            "apps/desktop/electron/preload.ts",
            "apps/desktop/src/global.d.ts",
            "apps/desktop/src/app/artifacts/",
            "apps/desktop/src/lib/media",
        ),
        "checks": (
            _check("desktop-remote-artifacts-tests", "vitest", "cd apps/desktop && npx vitest run --environment jsdom src/lib/media.remote.test.ts src/lib/desktop-fs.test.ts src/app/artifacts/index.test.ts"),
            _check("desktop-typecheck", "typecheck", "cd apps/desktop && NODE_ENV=test npm run typecheck"),
        ),
    },
    {
        "id": "desktop-remote-profiles",
        "name": "Desktop remote profile handles / remote routing",
        "invariants": ("Remote profile handles stay bound to the selected gateway.",),
        "prefer_upstream": "Prefer upstream connection configuration when profile identity and routing remain explicit.",
        "drop_when": "Drop when upstream supplies equivalent remote profile routing.",
        "references": ("FORK.md", "docs/axiom-fork-contract.md"),
        "paths": (
            "apps/desktop/electron/connection-config.ts",
            "apps/desktop/electron/main.ts",
            "apps/desktop/src/store/profile.ts",
            "apps/desktop/src/app/settings/gateway-settings.tsx",
        ),
        "checks": (
            _check("desktop-remote-profiles-tests", "vitest", "cd apps/desktop && NODE_ENV=test npx vitest run --project electron electron/connection-config.test.ts"),
            _check("desktop-typecheck", "typecheck", "cd apps/desktop && NODE_ENV=test npm run typecheck"),
        ),
    },
    {
        "id": "slack-channel-session",
        "name": "Slack channel/session behavior",
        "invariants": ("Slack mentions and channel-scoped sessions retain their established routing semantics.",),
        "prefer_upstream": "Prefer upstream Slack adapter structure while preserving channel/session scope.",
        "drop_when": "Drop when upstream tests prove equivalent Slack mention and session behavior.",
        "references": ("FORK.md",),
        "paths": (
            "gateway/platforms/slack.py",
            "gateway/platforms/base.py",
            "gateway/run.py",
            "gateway/session.py",
            "gateway/config.py",
            "tests/gateway/test_slack",
        ),
        "checks": (
            _check("slack-channel-session-tests", "pytest", "python -m pytest -o addopts= -q tests/gateway/test_slack.py tests/gateway/test_slack_mention.py tests/gateway/test_slack_channel_session_scope.py"),
        ),
    },
    {
        "id": "anthropic-oauth-billing",
        "name": "Anthropic Claude OAuth billing-lane fixes",
        "invariants": ("Claude OAuth requests retain billing-lane and system relocation behavior.",),
        "prefer_upstream": "Prefer upstream transport changes when OAuth behavior remains covered.",
        "drop_when": "Drop when upstream has equivalent OAuth billing behavior and tests.",
        "references": ("FORK.md",),
        "paths": (
            "agent/anthropic_adapter.py",
            "agent/transports/anthropic.py",
            "tests/agent/test_anthropic_adapter.py",
            "tests/agent/test_anthropic_oauth_system_relocation.py",
        ),
        "checks": (
            _check("anthropic-oauth-compile", "py_compile", "python -m py_compile agent/anthropic_adapter.py agent/transports/anthropic.py", 120),
            _check("anthropic-oauth-tests", "pytest", "python -m pytest -o addopts= -q tests/agent/test_anthropic_adapter.py tests/agent/test_anthropic_oauth_system_relocation.py"),
        ),
    },
    {
        "id": "live-mcp-refresh",
        "name": "Live MCP/tool-schema refresh",
        "invariants": ("MCP tool schemas refresh without rebuilding conversation context.",),
        "prefer_upstream": "Prefer upstream agent initialization seams that preserve live refresh.",
        "drop_when": "Drop when upstream provides equivalent cache-safe refresh.",
        "references": ("FORK.md",),
        "paths": (
            "agent/agent_init.py",
            "agent/chat_completion_helpers.py",
            "tools/mcp_tool.py",
            "tests/tools/test_refresh_agent_mcp_tools.py",
            "tests/tools/test_mcp_tool.py",
        ),
        "checks": (
            _check("live-mcp-refresh-tests", "pytest", "python -m pytest -o addopts= -q tests/tools/test_refresh_agent_mcp_tools.py"),
        ),
    },
    {
        "id": "forge-runtime-policy",
        "name": "Forge integration / runtime tool policy",
        "invariants": ("Forge tools obey runtime tool policy and platform boundaries.",),
        "prefer_upstream": "Prefer upstream policy seams while preserving Forge capability gating.",
        "drop_when": "Drop when upstream supports equivalent Forge policy integration.",
        "references": ("FORK.md",),
        "paths": (
            "plugins/platforms/forge/",
            "agent/runtime_tool_policy.py",
            "model_tools.py",
            "tests/gateway/test_forge_plugin.py",
            "tests/agent/test_runtime_tool_policy.py",
        ),
        "checks": (
            _check("forge-runtime-policy-tests", "pytest", "python -m pytest -o addopts= -q tests/gateway/test_forge_plugin.py tests/agent/test_runtime_tool_policy.py tests/test_model_tools.py"),
        ),
    },
    {
        "id": "webhook-route-toolsets",
        "name": "Webhook route-level toolsets",
        "invariants": ("Webhook routes retain explicit per-route toolsets.",),
        "prefer_upstream": "Prefer upstream webhook routing when route-level toolsets remain explicit.",
        "drop_when": "Drop when upstream provides equivalent route-level toolset policy.",
        "references": ("FORK.md",),
        "paths": (
            "gateway/platforms/webhook.py",
            "gateway/run.py",
            "hermes_cli/webhook.py",
            "tests/gateway/test_webhook_adapter.py",
            "tests/hermes_cli/test_webhook_cli.py",
        ),
        "checks": (
            _check("webhook-route-toolsets-tests", "pytest", "python -m pytest -o addopts= -q tests/gateway/test_webhook_adapter.py tests/hermes_cli/test_webhook_cli.py"),
        ),
    },
    {
        "id": "a2a-communication",
        "name": "A2A inter-agent communication",
        "invariants": ("A2A protocol, adapter, and tools remain mutually compatible.",),
        "prefer_upstream": "Prefer upstream tool configuration seams while preserving A2A protocol behavior.",
        "drop_when": "Drop when upstream provides equivalent inter-agent communication.",
        "references": ("FORK.md",),
        "paths": (
            "plugins/platforms/a2a/",
            "tests/plugins/test_a2a_plugin.py",
            "hermes_cli/tools_config.py",
        ),
        "checks": (
            _check("a2a-compile", "py_compile", "python -m py_compile plugins/platforms/a2a/adapter.py plugins/platforms/a2a/tools.py plugins/platforms/a2a/protocol.py", 120),
            _check("a2a-tests", "pytest", "python -m pytest -o addopts= -q tests/plugins/test_a2a_plugin.py"),
        ),
    },
)


def _check_fingerprint(check: CheckSpec) -> str:
    canonical = json.dumps(
        {key: check[key] for key in ("id", "kind", "command", "timeout_seconds")},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_check_specs(checks: object) -> list[CheckSpec]:
    """Normalize old command strings and reject ambiguous stable check IDs."""
    if not isinstance(checks, (list, tuple)):
        return []
    by_id: dict[str, tuple[str, CheckSpec]] = {}
    for raw in checks:
        if isinstance(raw, str):
            command = raw.strip()
            if not command:
                continue
            digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
            spec = _check(f"legacy-{digest[:16]}", "legacy", command)
        elif isinstance(raw, dict):
            try:
                spec = _check(str(raw["id"]), str(raw["kind"]), str(raw["command"]), int(raw["timeout_seconds"]))
            except (KeyError, TypeError, ValueError):
                continue
        else:
            continue
        fingerprint = _check_fingerprint(spec)
        previous = by_id.get(spec["id"])
        if previous and previous[0] != fingerprint:
            raise ValueError(f"Conflicting check id: {spec['id']}")
        by_id.setdefault(spec["id"], (fingerprint, spec))
    return [item[1] for item in by_id.values()]


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


def _full_git_ref(git_cmd: list[str], cwd: Path, ref: str) -> str:
    """Return a full commit object id or an empty string when unavailable."""
    value = _git_output(
        git_cmd,
        cwd,
        ["rev-parse", "--verify", f"{ref}^{{commit}}"],
        limit=128,
    ).strip()
    return value if re.fullmatch(r"[0-9a-fA-F]{40,64}", value) else ""


def _matched_fork_watch_areas(paths: list[str]) -> list[ForkWatchAreaSpec]:
    normalized = [p.replace("\\", "/") for p in paths]
    matched: list[ForkWatchAreaSpec] = []
    for area in FORK_WATCH_AREAS:
        prefixes = tuple(p.replace("\\", "/") for p in area["paths"])
        if any(path.startswith(prefix) for path in normalized for prefix in prefixes):
            matched.append(area)
    return matched


def _bounded_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n…(truncated)…"


def _render_resolver_brief(review: dict[str, object]) -> str:
    files = sorted({str(item) for item in review.get("conflict_files", []) or []})[:50]
    areas = sorted(
        (area for area in review.get("watch_areas", []) or [] if isinstance(area, dict)),
        key=lambda area: str(area.get("id") or ""),
    )
    lines = [
        "# Hermes deploy conflict resolver brief", "",
        f"Deploy branch: `{review.get('branch') or ''}`",
        f"Retained worktree: `{review.get('worktree') or ''}`", "",
        "## Conflicting files", *([f"- `{path}`" for path in files] or ["- none reported"]),
    ]
    for area in areas:
        lines.extend(["", f"## {area.get('name')} (`{area.get('id')}`)", "", "Protected invariants:"])
        lines.extend(f"- {item}" for item in area.get("invariants", ()))
        lines.extend(["", f"Prefer upstream: {area.get('prefer_upstream')}", f"Drop when: {area.get('drop_when')}", "", "References:"])
        lines.extend(f"- `{item}`" for item in area.get("references", ()))
        checks = _normalize_check_specs(area.get("checks", ()))
        lines.extend(["", "Parent-owned check IDs:", *[f"- `{item['id']}`" for item in checks]])
    incoming = _bounded_text(review.get("incoming_commits"), 2500)
    error = _bounded_text(review.get("error"), 1500)
    if incoming:
        lines.extend(["", "## Bounded incoming summary", "", "```text", incoming, "```"])
    if error:
        lines.extend(["", "## Bounded merge output", "", "```text", error, "```"])
    return "\n".join(lines).rstrip() + "\n"


def _write_resolver_brief(review: dict[str, object]) -> Optional[Path]:
    try:
        reports_dir = _review_reports_dir()
        reports_dir.mkdir(parents=True, exist_ok=True)
        branch = str(review.get("branch") or "deploy").replace("/", "-")
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = reports_dir / f"{run_id}-{branch}-resolver-brief.md"
        path.write_text(_render_resolver_brief(review), encoding="utf-8")
        return path
    except Exception:
        logger.debug("Failed to write resolver brief", exc_info=True)
        return None


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

    return f"""You are reviewing a Hermes Agent Axiom deploy-branch update conflict.

Return a concise human-readable operator brief only. Do not propose automatic
mutation, do not ask the updater to continue unattended, and do not include
secrets. Prefer upstream behavior when it satisfies the same Axiom requirement,
but preserve documented Axiom operational outcomes until tests prove upstream is
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

Matched Axiom fork watch areas:
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
        lines.append("Likely Axiom fork areas involved:")
        for area in watch_areas[:6]:
            lines.append(f"- {area.get('name')}")
    else:
        lines.append("No documented Axiom fork watch area matched the conflict files; treat this as a normal upstream merge conflict.")
    checks: list[str] = []
    for area in watch_areas:
        for check in area.get("checks", ()):
            if str(check) not in checks:
                checks.append(str(check))
    lines.append("Safest next move: resolve in the retained worktree, prefer upstream when it preserves the documented Axiom outcome, then run focused tests before pushing HEAD back to the deploy branch.")
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
        content.extend(["", "## Matched Axiom fork watch areas", ""])
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
    resolver_brief = _write_resolver_brief(review)
    review["resolver_brief_path"] = str(resolver_brief) if resolver_brief else ""
    report_path = _write_update_review_report(review)
    review["report_path"] = str(report_path) if report_path is not None else ""
    return review


def _count_commits_between(git_cmd: list[str], cwd: Path, older: str, newer: str) -> int:
    result = subprocess.run(
        git_cmd + ["rev-list", "--count", f"{older}..{newer}"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return -1
    try:
        return int(result.stdout.strip())
    except ValueError:
        return -1


def _count_changed_from_pre_update(
    git_cmd: list[str],
    cwd: Path,
    pre_update_head: str,
    fallback: int,
) -> int:
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
    phase: str = "resolve_pending",
    resolved_head: str = "",
    error: str = "",
) -> None:
    try:
        marker = _deploy_handoff_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(conflict_files, str):
            conflict_list = [line.strip() for line in conflict_files.splitlines() if line.strip()]
        else:
            conflict_list = [str(line).strip() for line in conflict_files if str(line).strip()]
        watch_areas = []
        focused_checks: list[CheckSpec] = []
        iterable_areas = review.get("watch_areas", []) if review else _matched_fork_watch_areas(conflict_list)
        if not isinstance(iterable_areas, (list, tuple)):
            iterable_areas = []
        for area in iterable_areas:
            if not isinstance(area, dict):
                continue
            checks = _normalize_check_specs(area.get("checks", ()))
            paths = [str(p) for p in area.get("paths", ())]
            watch_areas.append({
                "id": str(area.get("id") or ""),
                "name": str(area.get("name") or ""),
                "paths": paths,
                "checks": checks,
            })
            focused_checks.extend(checks)
        focused_checks = _normalize_check_specs(focused_checks)
        payload = {
            "schema": 3,
            "repo": str(repo),
            "branch": branch,
            "reason": reason,
            "worktree": str(worktree_path) if worktree_path is not None else "",
            "conflict_files": conflict_list,
            "report_path": str(review.get("report_path") or "") if review else "",
            "resolver_brief_path": str(review.get("resolver_brief_path") or "") if review else "",
            "watch_areas": watch_areas,
            "focused_checks": focused_checks,
            "phase": phase,
            "resolved_head": resolved_head,
            "error": error[-4000:],
            "live_head": _short_git_ref(["git"], repo, "HEAD"),
            "origin_head": _short_git_ref(["git"], repo, f"origin/{branch}"),
            "upstream_head": _short_git_ref(["git"], repo, "upstream/main"),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        marker.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        logger.debug("Failed to write deploy handoff marker", exc_info=True)


def _print_push_recovery_handoff(
    *, repo: Path, branch: str, worktree: Path, resolved_head: str, error: str
) -> None:
    """Persist and print a focused recovery handoff after resolution succeeded."""
    try:
        redact_module = __import__("agent.redact", fromlist=["redact_sensitive_text"])
        safe_error = redact_module.redact_sensitive_text(error, force=True)
    except Exception:
        safe_error = re.sub(r"(https?://)[^/@\s]+@", r"\1[REDACTED]@", error)
    details = "\n".join(_resolver_output_tail(safe_error, max_lines=30, max_chars=4000))
    _record_deploy_handoff(
        repo=repo,
        branch=branch,
        reason=f"push resolved deploy branch origin/{branch} failed.",
        worktree_path=worktree,
        phase="push_pending",
        resolved_head=resolved_head,
        error=details,
    )
    marker = _deploy_handoff_marker_path()
    print("  Resolution and validation succeeded; the committed worktree was retained.")
    if details:
        print("  Git push diagnostics:")
        for line in details.splitlines():
            print(f"    {line}")
    print("  Rerun `hermes update` to retry this exact commit without rerunning resolution.")
    print("  Or start a focused recovery chat:")
    print(
        f'    hermes chat -q "Read {marker} and recover the pending origin/{branch} push. '
        'Do not rerun conflict resolution or mutate the live checkout."'
    )


def _completed_deploy_handoff_requires_post_update(
    git_cmd: list[str],
    repo: Path,
    branch: str,
) -> bool:
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


def _handoff_snapshot_is_published(
    git_cmd: list[str],
    repo: Path,
    branch: str,
    payload: dict[str, object],
) -> bool:
    """Return whether the exact refs captured by a handoff reached origin."""
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


def _focused_checks_for_paths(paths: list[str], payload: dict[str, object]) -> list[CheckSpec]:
    checks: list[object] = []
    marker_checks = payload.get("focused_checks")
    if isinstance(marker_checks, list):
        checks.extend(marker_checks)
    for area in _matched_fork_watch_areas(paths):
        checks.extend(area["checks"])
    unique = _normalize_check_specs(checks)
    if unique:
        return unique
    py_files = [path for path in paths if path.endswith(".py")]
    if py_files:
        quoted = " ".join(shlex_quote(path) for path in py_files[:20])
        return [_check("fallback-py-compile", "py_compile", f"python -m py_compile {quoted}", 120)]
    return []


def _build_deploy_resolver_prompt(payload: dict[str, object], checks: object) -> str:
    conflict_files = payload.get("conflict_files")
    files = "\n".join(f"- {item}" for item in conflict_files) if isinstance(conflict_files, list) else "- inspect git status"
    brief_path = str(payload.get("resolver_brief_path") or payload.get("report_path") or "").strip()
    failed_results = _failed_validation_results(payload)
    repair_section = ""
    if failed_results:
        diagnostics = []
        for check_id, result in failed_results:
            output = _bounded_text(result.get("output_tail") or "(no diagnostic output)", 4000)
            diagnostics.append(f"[{check_id}]\n{output}")
        repair_section = f"""

Parent validation repair:
The structural merge checkpoint compiled or tested unsuccessfully. Repair the
tracked source against these authoritative parent diagnostics. Trace missing
symbols to their surviving fork consumers and preserve upstream behavior plus
still-required fork contracts; do not merely silence types or delete consumers.

{chr(10).join(diagnostics)}

Do not rerun parent-owned checks. The parent updater will checkpoint your tracked
repair and rerun the failed checks after you exit.
"""
    return f"""Resolve the retained Hermes deploy-branch update handoff to completion.

Repo: {payload.get('repo')}
Deploy branch: {payload.get('branch')}
Retained worktree: {payload.get('worktree')}
Reason: {payload.get('reason')}
Resolver brief: {brief_path or '(none)'}

Read the resolver brief first. It is the bounded, conflict-scoped authority for
protected invariants, upstream/drop guidance, precise references, and the IDs
of checks owned by the parent updater.

Conflicting files from the updater marker:
{files}
{repair_section}

Resolver contract:
1. Work only inside the retained worktree above.
2. Resolve the git merge conflict using the resolver brief and prefer upstream code when it provides equivalent or better behavior.
3. Do not touch secrets, auth tokens, .env files, or unrelated generated churn.
4. Perform only cheap structural validation: confirm no unmerged paths, scan the reconciled files for conflict markers, and run `git diff --check`.
5. Leave all compilation, package installation, and parent-owned checks to the parent updater.
6. Leave the worktree ready for the updater to checkpoint: only justified tracked changes and no unexpected untracked files.

Do not commit, push, or run `hermes update` yourself; the parent updater will checkpoint, validate, push, fast-forward the live checkout, and run the normal install/restart phase after you exit.
"""


def _run_update_resolver_agent(prompt: str, worktree: Path) -> subprocess.CompletedProcess:
    """Run a non-interactive Hermes resolver session in the retained worktree.

    Stream the child transcript so a long conflict resolution is observable,
    while retaining a bounded tail for failure diagnostics. The caller frames
    this output as advisory because the parent updater still owns validation,
    publication, and the final user-facing result.
    """
    timeout = int(os.environ.get("HERMES_UPDATE_RESOLVE_TIMEOUT", "3600") or "3600")
    resolver_source = str(Path(__file__).resolve().parents[1])
    cmd = [
        sys.executable,
        "-P",
        "-m",
        "hermes_cli.main",
        "chat",
        "-q",
        prompt,
        "-t",
        "terminal,file,search,skills",
        "--source",
        "update-resolver",
        "--yolo",
    ]
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(
        part for part in (resolver_source, existing_pythonpath) if part
    )
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "HERMES_UPDATE_RESOLVE": "1",
        "PYTHONPATH": pythonpath,
    }
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        cmd,
        cwd=worktree,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **popen_kwargs,
    )
    if process.stdout is None:
        process.kill()
        raise RuntimeError("Resolver subprocess did not expose an output stream")

    transcript_tail: deque[str] = deque(maxlen=200)

    def _pump_output() -> None:
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                transcript_tail.append(raw_line)
                line = raw_line.rstrip("\r\n")
                print(f"  │ {line}" if line else "  │", flush=True)
        finally:
            process.stdout.close()

    pump = threading.Thread(
        target=_pump_output,
        name="hermes-update-resolver-output",
        daemon=True,
    )
    pump.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process_tree_terminated = _terminate_resolver_process_tree(process)
        process.wait()
        pump.join(timeout=None if process_tree_terminated else 1)
        timeout_error = subprocess.TimeoutExpired(
            cmd,
            timeout,
            output="".join(transcript_tail),
        )
        timeout_error.process_tree_terminated = process_tree_terminated
        raise timeout_error from exc
    pump.join()
    return subprocess.CompletedProcess(
        cmd,
        returncode,
        stdout="".join(transcript_tail),
        stderr="",
    )


def _terminate_resolver_process_tree(process: subprocess.Popen) -> bool:
    """Terminate the resolver and descendants before inspecting its worktree."""
    if process.poll() is not None:
        return True
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        if process.poll() is None:
            process.kill()
        return False
    try:
        os.killpg(process.pid, signal.SIGKILL)  # windows-footgun: ok - POSIX-only branch
        return True
    except OSError:
        process.kill()
        return False


def _resolver_timeout_is_safe_to_salvage(exc: subprocess.TimeoutExpired) -> bool:
    """Only trust the worktree after the resolver's whole process tree stopped."""
    return getattr(exc, "process_tree_terminated", False) is True


def _resolver_output_tail(value: object, *, max_lines: int = 40, max_chars: int = 6000) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    lines = text.splitlines()[-max_lines:]
    bounded = "\n".join(lines)[-max_chars:]
    return bounded.splitlines()


def _print_resolver_failure_diagnostics(result: subprocess.CompletedProcess) -> None:
    print(f"  Resolver exit code: {result.returncode}")
    for label, value in (("stderr", result.stderr), ("stdout", result.stdout)):
        lines = _resolver_output_tail(value)
        if not lines:
            continue
        print(f"  Resolver {label} (tail):")
        for line in lines:
            print(f"    {line}")


def _focused_check_shell_command(check: str, *, windows: bool) -> str:
    """Translate POSIX env-prefix syntax for the active platform shell."""
    if not windows:
        return check

    segments = check.split("&&")
    normalized: list[str] = []
    assignment = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)\s+")
    for raw_segment in segments:
        segment = raw_segment.strip()
        prefixes: list[str] = []
        while match := assignment.match(segment):
            prefixes.append(f'set "{match.group(1)}={match.group(2)}"')
            segment = segment[match.end():]
        normalized.extend((*prefixes, segment))
    return " && ".join(normalized)


def _run_focused_check(
    check: str, worktree: Path, *, timeout_seconds: int = 900
) -> Optional[subprocess.CompletedProcess]:
    """Run one retained-handoff check.

    ``None`` means the check requires pytest but the active updater
    interpreter is a production environment without that dev dependency.
    Missing test tooling is not a failed merge, and the updater must not
    install pytest into its live runtime venv just to validate a handoff.
    """
    if "-m pytest" in check:
        pytest_probe = subprocess.run(
            [sys.executable, "-c", "import pytest"],
            capture_output=True,
            text=True,
        )
        if pytest_probe.returncode != 0:
            return None

    result = subprocess.run(
        _focused_check_shell_command(check, windows=os.name == "nt"),
        cwd=worktree,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    return result


def _prepare_isolated_worktree_dependencies(worktree: Path) -> tuple[bool, str]:
    """Install a private dev dependency tree for retained Desktop validation."""
    npm = shutil.which("npm")
    if not npm:
        return False, "npm is unavailable for retained Desktop validation"
    if not (worktree / "package-lock.json").is_file():
        return False, "retained worktree has no package-lock.json"
    result = subprocess.run(
        [
            npm,
            "ci",
            "--include=dev",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or "npm ci failed").strip()[-4000:]


def _run_parent_handoff_validation(
    worktree: Path,
    resolved_head: str,
    checks: object,
    prior_status: object,
) -> bool:
    """Run checks serially and persist results bound to SHA plus canonical spec."""
    import time

    specs = _normalize_check_specs(checks)
    desktop = [spec for spec in specs if "apps/desktop" in spec["command"]]
    ordered = [spec for spec in specs if spec not in desktop] + desktop
    prior_results: dict[str, object] = {}
    if isinstance(prior_status, dict) and prior_status.get("resolved_sha") == resolved_head:
        candidate = prior_status.get("results")
        if isinstance(candidate, dict):
            prior_results = candidate
    ledger: dict[str, object] = {"resolved_sha": resolved_head, "results": dict(prior_results)}
    results = ledger["results"]
    assert isinstance(results, dict)
    legacy_status: dict[str, str] = {}
    dependencies_prepared = False

    for spec in ordered:
        check_id = spec["id"]
        fingerprint = _check_fingerprint(spec)
        previous = results.get(check_id)
        if (
            isinstance(previous, dict)
            and previous.get("status") in {"passed", "skipped"}
            and previous.get("fingerprint") == fingerprint
        ):
            legacy_status[spec["command"]] = str(previous["status"])
            continue
        if spec in desktop and not dependencies_prepared:
            prepared, error = _prepare_isolated_worktree_dependencies(worktree)
            if not prepared:
                _update_deploy_handoff_state(
                    phase="validation_failed", resolved_head=resolved_head,
                    validation_sha=resolved_head, check_ledger=ledger,
                    check_status=legacy_status, error=_bounded_text(error, 4000),
                )
                return False
            dependencies_prepared = True
        command = spec["command"]
        print(f"→ Focused check [{check_id}]: {command}")
        started = time.monotonic()
        outcome = _run_focused_check(
            command, worktree, timeout_seconds=spec["timeout_seconds"]
        )
        duration = round(time.monotonic() - started, 3)
        if isinstance(outcome, subprocess.CompletedProcess):
            returncode = outcome.returncode
            status_value = "passed" if returncode == 0 else "failed"
            raw_output = "\n".join(part for part in (outcome.stderr, outcome.stdout) if part)
        else:
            returncode = None if outcome is None else 0 if outcome else 1
            status_value = "skipped" if outcome is None else "passed" if outcome else "failed"
            raw_output = ""
        try:
            redact_module = __import__("agent.redact", fromlist=["redact_sensitive_text"])
            raw_output = redact_module.redact_sensitive_text(str(raw_output), force=True)
        except Exception:
            raw_output = re.sub(r"(https?://)[^/@\s]+@", r"\1[REDACTED]@", str(raw_output))
        results[check_id] = {
            "check_id": check_id,
            "fingerprint": fingerprint,
            "status": status_value,
            "returncode": returncode,
            "output_tail": "\n".join(_resolver_output_tail(raw_output, max_lines=30, max_chars=4000)),
            "duration_seconds": duration,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
        }
        legacy_status[command] = status_value
        failed = status_value == "failed"
        _update_deploy_handoff_state(
            phase="validation_failed" if failed else "validation_pending",
            resolved_head=resolved_head, validation_sha=resolved_head,
            check_ledger=ledger, check_status=legacy_status,
            error=f"Focused check failed: {check_id}" if failed else "",
        )
        if failed:
            return False

    _update_deploy_handoff_state(
        phase="commit_push_pending", resolved_head=resolved_head,
        validation_sha=resolved_head, check_ledger=ledger,
        check_status=legacy_status, error="",
    )
    return True


def _update_deploy_handoff_state(**updates: object) -> None:
    """Durably merge state into the retained handoff marker."""
    marker = _deploy_handoff_marker_path()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("deploy handoff marker is malformed")
    payload.update(updates)
    temporary = marker.with_name(f"{marker.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, marker)


_MAX_VALIDATION_REPAIR_ATTEMPTS = 2


def _failed_validation_results(
    payload: dict[str, object],
) -> list[tuple[str, dict[str, object]]]:
    ledger = payload.get("check_ledger")
    if not isinstance(ledger, dict):
        return []
    results = ledger.get("results")
    if not isinstance(results, dict):
        return []
    return [
        (str(check_id), result)
        for check_id, result in results.items()
        if isinstance(result, dict) and result.get("status") == "failed"
    ]


def _retry_validation_with_resolver(
    *,
    git_cmd: list[str],
    repo: Path,
    branch: str,
    pre_update_head: str,
    publish_only: bool = False,
) -> Optional[int]:
    """Feed real parent diagnostics back to the resolver, with a hard cap."""
    payload = _read_deploy_handoff_payload(repo, branch)
    if payload is None or not _failed_validation_results(payload):
        return None

    try:
        attempts = int(payload.get("validation_repair_attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    if attempts >= _MAX_VALIDATION_REPAIR_ATTEMPTS:
        print(
            "✗ Parent validation still fails after "
            f"{_MAX_VALIDATION_REPAIR_ATTEMPTS} automatic repair pass(es); "
            "checkpoint retained for review."
        )
        return None

    attempts += 1
    _update_deploy_handoff_state(
        phase="repair_pending",
        validation_repair_attempts=attempts,
        error=f"Parent validation repair pass {attempts} pending.",
    )
    print(
        f"→ Feeding parent validation diagnostics back to the resolver "
        f"(repair {attempts}/{_MAX_VALIDATION_REPAIR_ATTEMPTS})."
    )
    return _resolve_deploy_handoff(
        git_cmd=git_cmd,
        repo=repo,
        branch=branch,
        pre_update_head=pre_update_head,
        publish_only=publish_only,
    )


def _checkpoint_resolved_handoff(
    git_cmd: list[str], worktree: Path, branch: str
) -> tuple[str, str]:
    """Commit a structurally resolved worktree without capturing new files."""
    status = subprocess.run(
        git_cmd + ["status", "--porcelain", "--untracked-files=all"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        return "", "Could not inspect retained worktree status."
    untracked = [
        line[3:].strip()
        for line in status.stdout.splitlines()
        if line.startswith("?? ")
    ]
    if untracked:
        return "", "Unexpected untracked files: " + ", ".join(untracked[:20])

    diff_check = subprocess.run(
        git_cmd + ["diff", "--check"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if diff_check.returncode != 0:
        return "", (diff_check.stderr or diff_check.stdout or "git diff --check failed").strip()

    staged = subprocess.run(
        git_cmd + ["add", "--update"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if staged.returncode != 0:
        return "", (staged.stderr or staged.stdout or "Could not stage tracked resolution").strip()
    staged_diff_check = subprocess.run(
        git_cmd + ["diff", "--cached", "--check"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if staged_diff_check.returncode != 0:
        return "", (
            staged_diff_check.stderr
            or staged_diff_check.stdout
            or "git diff --cached --check failed"
        ).strip()

    # The resolver is advisory.  A conflict can be marker-free and whitespace-clean
    # while still swallowing a Python statement into a comment or otherwise leaving
    # a syntactically invalid file.  Fail before creating the durable checkpoint so
    # the retained handoff remains safely repairable rather than committing known
    # broken structure and only discovering it in the heavier parent checks.
    staged_python = subprocess.run(
        git_cmd + ["diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "*.py"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if staged_python.returncode != 0:
        return "", "Could not enumerate staged Python files for syntax validation."
    python_paths = [
        str(worktree / relative)
        for relative in staged_python.stdout.splitlines()
        if relative.strip() and (worktree / relative).is_file()
    ]
    if python_paths:
        syntax_check = subprocess.run(
            [sys.executable, "-m", "py_compile", *python_paths],
            cwd=worktree,
            capture_output=True,
            text=True,
        )
        if syntax_check.returncode != 0:
            return "", (
                syntax_check.stderr
                or syntax_check.stdout
                or "Staged Python syntax validation failed"
            ).strip()

    commit_needed = subprocess.run(
        git_cmd + ["diff", "--cached", "--quiet"], cwd=worktree
    )
    if commit_needed.returncode != 0:
        commit_cmd = (
            git_cmd + ["commit", "--no-edit"]
            if _has_git_state(git_cmd, worktree, "MERGE_HEAD")
            else git_cmd
            + ["commit", "-m", f"merge: resolve {branch} deploy update"]
        )
        commit = subprocess.run(
            commit_cmd, cwd=worktree, capture_output=True, text=True
        )
        if commit.returncode != 0:
            return "", (commit.stderr or commit.stdout or "Could not commit resolution").strip()

    resolved_head = _full_git_ref(git_cmd, worktree, "HEAD")
    if not resolved_head:
        return "", "Could not read resolved checkpoint commit."
    _update_deploy_handoff_state(
        phase="validation_pending",
        resolved_head=resolved_head,
        validation_sha=resolved_head,
        check_ledger={"resolved_sha": resolved_head, "results": {}},
        check_status={},
        error="",
    )
    return resolved_head, ""


def _resolve_deploy_handoff(
    *,
    git_cmd: list[str],
    repo: Path,
    branch: str,
    pre_update_head: str,
    publish_only: bool = False,
) -> Optional[int]:
    """Autonomously resolve a retained deploy update handoff.

    A deploy-branch ``hermes update`` resolves the retained conflict worktree,
    runs focused checks, pushes the deploy branch, and lets the parent updater
    continue with install/restart. It still stops on hard safety gates rather
    than mutating ambiguous state.
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
            details = _resolver_output_tail(result.stderr or result.stdout, max_lines=20, max_chars=3000)
            for line in details:
                print(f"    {line}")
        return None

    phase = str(payload.get("phase") or "resolve_pending")
    if phase in {"push_pending", "commit_push_pending"}:
        resolved_head = str(payload.get("resolved_head") or "").strip()
        actual_head = _full_git_ref(git_cmd, worktree, "HEAD") if worktree.exists() else ""
        if not resolved_head or actual_head != resolved_head:
            status.fail(note="retained commit changed")
            print("✗ Pending push worktree no longer matches its validated commit; stopping safely.")
            return None
        status.advance("push")
        push = subprocess.run(
            git_cmd + ["push", "origin", f"{resolved_head}:{branch}"],
            cwd=worktree,
            capture_output=True,
            text=True,
        )
        if push.returncode != 0:
            status.fail(note="push retry failed")
            print(f"✗ Could not publish retained commit {resolved_head[:12]} to origin/{branch}.")
            _print_push_recovery_handoff(
                repo=repo,
                branch=branch,
                worktree=worktree,
                resolved_head=resolved_head,
                error=push.stderr or push.stdout or "",
            )
            return None
        status.advance("sync live")
        if publish_only:
            _discard_published_handoff(git_cmd, repo, worktree)
            status.finish(note="published retained commit")
            print(f"✓ Published retained commit to origin/{branch}; live checkout was not changed.")
            return 1
        changed = _fast_forward_live_deploy_checkout(git_cmd, repo, branch, pre_update_head, 1)
        if changed is None:
            status.fail(note="live fast-forward failed")
            print(f"✗ Commit was pushed, but live checkout could not fast-forward to origin/{branch}.")
            return None
        status.advance("cleanup")
        _discard_published_handoff(git_cmd, repo, worktree)
        status.finish(note="published retained commit")
        print(f"✓ Published retained commit and fast-forwarded live checkout to origin/{branch}.")
        return changed or 1

    if phase == "resolve_pending" and _handoff_snapshot_is_published(
        git_cmd, repo, branch, payload
    ):
        if not _discard_published_handoff(git_cmd, repo, worktree):
            status.fail(note="stale marker cleanup failed")
            print("✗ Could not clear stale deploy handoff marker; stopping before fresh update.")
            return None
        status.finish(note="published snapshot cleared")
        print(
            f"→ Retained handoff snapshot is already published on origin/{branch}; "
            "starting a fresh deploy update."
        )
        if publish_only:
            return _run_deploy_branch_update(
                git_cmd,
                repo,
                branch,
                pre_update_head,
                publish_only=True,
            )
        return _run_deploy_branch_update(git_cmd, repo, branch, pre_update_head)

    if not worktree_raw or not worktree.exists():
        status.fail(note="worktree missing")
        print("✗ Deploy handoff worktree is missing; cannot auto-resolve.")
        if _completed_deploy_handoff_requires_post_update(git_cmd, repo, branch):
            return 1
        return None

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
    if phase in {"validation_pending", "validation_failed"}:
        if phase == "validation_failed":
            # A failed parent check leaves the retained worktree editable so a
            # resolver can repair the exact checkpoint. Re-checkpoint tracked
            # repairs before consulting the old validation ledger; otherwise every
            # retry reruns checks against the stale commit forever.
            retained_status = subprocess.run(
                git_cmd + ["status", "--porcelain", "--untracked-files=all"],
                cwd=worktree,
                capture_output=True,
                text=True,
            )
            if retained_status.returncode != 0:
                status.fail(note="retained status unavailable")
                print("✗ Could not inspect retained worktree before retrying validation.")
                return None
            if retained_status.stdout.strip():
                status.advance("commit")
                repaired_head, checkpoint_error = _checkpoint_resolved_handoff(
                    git_cmd, worktree, branch
                )
                if not repaired_head:
                    status.fail(note="repair checkpoint failed")
                    print(f"✗ Could not checkpoint retained validation repair: {checkpoint_error}")
                    return None
                payload = _read_deploy_handoff_payload(repo, branch) or payload
                phase = "validation_pending"

        resolved_head = str(payload.get("resolved_head") or "").strip()
        actual_head = _full_git_ref(git_cmd, worktree, "HEAD")
        if not resolved_head or actual_head != resolved_head:
            status.fail(note="retained checkpoint changed")
            print("✗ Retained checkpoint no longer matches its resolved commit; stopping safely.")
            return None
        prior_status = payload.get("check_ledger")
        if payload.get("validation_sha") != resolved_head or not isinstance(prior_status, dict):
            prior_status = {}
        status.advance("focused checks")
        if not _run_parent_handoff_validation(
            worktree, resolved_head, checks, prior_status
        ):
            status.fail(note="validation failed")
            print("✗ Parent validation failed; checkpoint retained for a safe retry.")
            return _retry_validation_with_resolver(
                git_cmd=git_cmd,
                repo=repo,
                branch=branch,
                pre_update_head=pre_update_head,
                publish_only=publish_only,
            )
        phase = "commit_push_pending"

    if phase == "commit_push_pending":
        # A same-process validation completion reaches publication below. A
        # resumed marker was handled by the exact-commit branch above.
        pass
    else:
        status.advance("agent resolve")
        print("  ┌─ Live Hermes resolver session (advisory)", flush=True)
        print("  │ Parent structural validation, checkpoint, and heavyweight checks follow.", flush=True)
        timed_out = False
        try:
            result = _run_update_resolver_agent(
                _build_deploy_resolver_prompt(
                    {**payload, "conflict_files": conflict_files}, checks
                ),
                worktree,
            )
        except subprocess.TimeoutExpired as exc:
            if not _resolver_timeout_is_safe_to_salvage(exc):
                status.fail(note="resolver process tree may still be running")
                _update_deploy_handoff_state(
                    phase="resolve_pending",
                    resolved_head="",
                    error=(
                        "Resolver timed out and full process-tree termination could not "
                        "be confirmed; retained worktree was not inspected or checkpointed."
                    ),
                )
                print(
                    "✗ Resolver timed out, but its full process tree could not be confirmed stopped."
                )
                print("  Retained worktree was left untouched for manual review.")
                return None
            timed_out = True
            result = subprocess.CompletedProcess(
                exc.cmd, 124, stdout=exc.output or "", stderr="resolver timed out"
            )
        print("  └─ Resolver exited; starting authoritative parent validation.", flush=True)
        if result.returncode != 0 and not timed_out:
            status.fail(note="resolver agent failed")
            print("✗ Resolver agent failed; retained worktree was left untouched for manual review.")
            _print_resolver_failure_diagnostics(result)
            return None

    if phase != "commit_push_pending":
        status.advance("validate")
        unmerged = _git_output(
            git_cmd,
            worktree,
            ["diff", "--name-only", "--diff-filter=U"],
            limit=4000,
        )
        remaining = [line.strip() for line in unmerged.splitlines() if line.strip()]
        marker_files = conflict_files or _git_output(
            git_cmd, worktree, ["diff", "--name-only", "HEAD"], limit=4000
        ).splitlines()
        marker_hits = _scan_conflict_markers(
            worktree, [line.strip() for line in marker_files if line.strip()]
        )
        if remaining or marker_hits:
            note = "unmerged files remain" if remaining else "conflict markers remain"
            status.fail(note=note)
            reason = (
                "Resolver timed out before structural resolution completed."
                if timed_out
                else "Resolver did not complete structural resolution."
            )
            _update_deploy_handoff_state(
                phase="resolve_pending", resolved_head="", error=f"{reason} {note}"
            )
            print(f"✗ {reason}")
            if marker_hits and not timed_out:
                print("✗ Resolver left conflict markers in files:")
            for item in (remaining or marker_hits)[:20]:
                print(f"  - {item}")
            print("  Rerun `hermes update` to resume the retained resolution.")
            return None

        status.advance("commit")
        resolved_head, checkpoint_error = _checkpoint_resolved_handoff(
            git_cmd, worktree, branch
        )
        if not resolved_head:
            status.fail(note="checkpoint failed")
            _update_deploy_handoff_state(
                phase="resolve_pending", resolved_head="", error=checkpoint_error
            )
            print(f"✗ Could not checkpoint structurally resolved handoff: {checkpoint_error}")
            return None
        if timed_out:
            print("→ Resolver timed out, but structural resolution was salvaged and checkpointed.")

        status.advance("focused checks")
        if not _run_parent_handoff_validation(worktree, resolved_head, checks, {}):
            status.fail(note="validation failed")
            print("✗ Parent validation failed; checkpoint retained for a safe retry.")
            return _retry_validation_with_resolver(
                git_cmd=git_cmd,
                repo=repo,
                branch=branch,
                pre_update_head=pre_update_head,
                publish_only=publish_only,
            )

    status.advance("push")
    push = subprocess.run(
        git_cmd + ["push", "origin", f"{resolved_head}:{branch}"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        status.fail(note="push failed")
        print(f"✗ Could not push resolved deploy branch origin/{branch}.")
        resolved_head = _full_git_ref(git_cmd, worktree, "HEAD")
        _print_push_recovery_handoff(
            repo=repo,
            branch=branch,
            worktree=worktree,
            resolved_head=resolved_head,
            error=push.stderr or push.stdout or "",
        )
        return None

    status.advance("sync live")
    if publish_only:
        marker_cleared = False
        try:
            _deploy_handoff_marker_path().unlink()
            marker_cleared = True
        except OSError:
            logger.debug("Failed to clear deploy handoff marker", exc_info=True)
        if marker_cleared:
            _remove_managed_update_worktree(git_cmd, repo, worktree)
        status.finish(note="resolved and published handoff")
        print(f"✓ Resolved deploy handoff and pushed origin/{branch}; live checkout was not changed.")
        return 1

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

    Docker-Server and Axiom-Desktop often run ``hermes update`` back-to-back.
    In that flow ``origin/<branch>`` can advance after this process created its
    temp merge worktree but before it pushes. A raw push rejection is not yet a
    conflict; first fetch the new remote tip and classify whether the remote
    already contains this merge, whether the live checkout can simply
    fast-forward, or whether the temp worktree can merge the new remote tip and
    retry once.
    """

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
    *,
    target_sha: str | None = None,
    publish_only: bool = False,
) -> Optional[int]:
    """Update a merge-based deploy branch without mutating live code on conflicts.

    The live checkout only fast-forwards to ``origin/<branch>`` after any
    upstream merge has succeeded and been pushed.  Merge conflicts happen in a
    temporary worktree so production source files are not left conflicted.
    By default, returns the number of commits that changed the live checkout.
    With ``publish_only=True``, publishes the reconciled deploy branch but leaves
    the live checkout untouched and returns the number of upstream commits
    reconciled. ``None`` means a handoff was printed and update should stop.
    """
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

    if not target_sha:
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

    # The deploy artifact is published on origin/<branch>. Fetch that ref
    # explicitly before computing origin_ahead/local_ahead. A checkout can have
    # a perfectly valid origin fetch refspec while its remote-tracking ref is
    # stale (for example, an older Windows updater fetched only upstream).
    # Comparing first would incorrectly report "origin current" and strand the
    # client on an old deploy commit forever.
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

    if target_sha:
        if publish_only:
            raise ValueError("publish_only cannot be combined with target_sha")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", target_sha):
            raise ValueError("target_sha must be a full Git commit SHA")
        resolved = subprocess.run(
            git_cmd + ["rev-parse", remote_ref],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if resolved.returncode != 0 or resolved.stdout.strip().lower() != target_sha.lower():
            _pipe.fail(note=f"{remote_ref} moved after staging")
            print(f"✗ {remote_ref} no longer matches the staged target. Prepare the update again.")
            return None
        ff_result = subprocess.run(
            git_cmd + ["merge", "--ff-only", target_sha],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if ff_result.returncode != 0:
            _pipe.fail(note=f"cannot fast-forward to staged target {target_sha[:12]}")
            print("✗ The live checkout cannot fast-forward to the staged target. Nothing was merged.")
            return None
        changed = _count_changed_from_pre_update(git_cmd, repo, pre_update_head, 0)
        _pipe.finish(note=f"fast-forwarded to staged target {target_sha[:12]}")
        return changed

    if not publish_only and not _sync_deploy_main_to_upstream(git_cmd, repo):
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

        if publish_only:
            _pipe.finish(note=f"origin/{branch} already published")
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
                publish_only=publish_only,
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
                publish_only=publish_only,
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
        if publish_only:
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

    if publish_only:
        _pipe.finish(note=f"published {upstream_ahead} upstream commit(s) to origin/{branch}")
        if worktree_created:
            _remove_update_worktree(git_cmd, repo, worktree_path, parent)
        return upstream_ahead

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


def sync_upstream_to_deploy(repo: Path, branch: str | None = None) -> dict[str, object]:
    """Publish upstream/main into a fork deploy branch without changing live HEAD."""
    repo = repo.resolve()
    git_cmd = ["git"]
    if sys.platform == "win32":
        git_cmd = ["git", "-c", "windows.appendAtomically=false"]

    current = subprocess.run(
        git_cmd + ["rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    current_branch = current.stdout.strip() if current.returncode == 0 else ""
    deploy_branch = (branch or current_branch).strip()
    if deploy_branch not in DEPLOY_BRANCHES:
        return {
            "ok": False,
            "state": "failed",
            "error": "unsupported-branch",
            "message": "Upstream sync requires an Axiom or TGI deploy branch checkout.",
        }
    if current_branch != deploy_branch:
        return {
            "ok": False,
            "state": "failed",
            "error": "wrong-live-branch",
            "message": f"Live checkout is on {current_branch or 'an unknown branch'}, not {deploy_branch}.",
        }

    for remote in ("origin", "upstream"):
        probe = subprocess.run(
            git_cmd + ["remote", "get-url", remote],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            return {
                "ok": False,
                "state": "failed",
                "error": f"missing-{remote}",
                "message": f"Required Git remote {remote!r} is unavailable.",
            }

    if _deploy_handoff_exists_for(repo, deploy_branch):
        pre_update_head = _short_git_ref(git_cmd, repo, "HEAD")
        reconciled = _resolve_deploy_handoff(
            git_cmd=git_cmd,
            repo=repo,
            branch=deploy_branch,
            pre_update_head=pre_update_head,
            publish_only=True,
        )
        if reconciled is None:
            payload = _read_deploy_handoff_payload(repo, deploy_branch) or {}
            return {
                "ok": False,
                "state": "handoff",
                "error": "reconciliation-stopped",
                "message": "Upstream reconciliation stopped safely; the live checkout was not changed.",
                "worktree": str(payload.get("worktree") or ""),
                "reportPath": str(payload.get("report_path") or ""),
            }

        target = _short_git_ref(git_cmd, repo, f"origin/{deploy_branch}")
        return {
            "ok": True,
            "state": "completed",
            "branch": deploy_branch,
            "reconciled": reconciled,
            "targetSha": target,
            "message": f"Resolved the retained handoff and published origin/{deploy_branch}.",
        }

    local_compare = subprocess.run(
        git_cmd + ["rev-list", "--count", f"origin/{deploy_branch}..HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    try:
        local_ahead = int(local_compare.stdout.strip()) if local_compare.returncode == 0 else -1
    except ValueError:
        local_ahead = -1
    if local_ahead < 0:
        return {
            "ok": False,
            "state": "failed",
            "error": "compare-failed",
            "message": "Could not compare the live checkout with the Axiom deploy branch.",
        }
    if local_ahead > 0:
        return {
            "ok": False,
            "state": "failed",
            "error": "unpublished-local-commits",
            "message": (
                f"Live checkout has {local_ahead} unpublished deploy commit(s). "
                "Publish or discard them before syncing Hermes upstream."
            ),
        }

    pre_update_head = _short_git_ref(git_cmd, repo, "HEAD")
    reconciled = _run_deploy_branch_update(
        git_cmd,
        repo,
        deploy_branch,
        pre_update_head,
        publish_only=True,
    )
    if reconciled is None:
        payload = _read_deploy_handoff_payload(repo, deploy_branch) or {}
        return {
            "ok": False,
            "state": "handoff",
            "error": "reconciliation-stopped",
            "message": "Upstream reconciliation stopped safely; the live checkout was not changed.",
            "worktree": str(payload.get("worktree") or ""),
            "reportPath": str(payload.get("report_path") or ""),
        }

    target = _short_git_ref(git_cmd, repo, f"origin/{deploy_branch}")
    return {
        "ok": True,
        "state": "completed",
        "branch": deploy_branch,
        "reconciled": reconciled,
        "targetSha": target,
        "message": (
            f"Published {reconciled} Hermes upstream commit(s) to origin/{deploy_branch}."
            if reconciled
            else f"origin/{deploy_branch} already contains upstream/main."
        ),
    }


def _sync_upstream_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish Hermes upstream into a deploy branch")
    parser.add_argument("command", choices=("sync-upstream",))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--branch", default=None)
    args = parser.parse_args(argv)
    result = sync_upstream_to_deploy(args.repo, args.branch)
    print("HERMES_UPSTREAM_SYNC_RESULT=" + json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result.get("ok") else 1

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


if __name__ == "__main__":
    raise SystemExit(_sync_upstream_cli())
