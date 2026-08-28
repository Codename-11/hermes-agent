"""Behavior contract for Desktop deploy-aware update handoff arguments."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
POSIX_HANDOFF = REPO_ROOT / "scripts" / "desktop-update" / "posix.sh"
WINDOWS_HANDOFF = REPO_ROOT / "scripts" / "desktop-update" / "windows.ps1"


def _git_bash() -> str:
    git = shutil.which("git")
    candidates: list[Path] = []
    if git:
        git_path = Path(git).resolve()
        candidates.extend([git_path.parent.parent / "bin" / "bash.exe", git_path.parent / "bash.exe"])
    candidates.append(Path("C:/Program Files/Git/bin/bash.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    pytest.skip("Git Bash is unavailable")


def _posix_branch_args(branch: str, *, bare: bool = False) -> list[str]:
    command = [_git_bash(), str(POSIX_HANDOFF), "--self-test-update-args", "--branch", branch]
    if bare:
        command.append("--bare-update")
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip().split()


def test_posix_deploy_branches_are_bare_and_generic_branches_stay_pinned() -> None:
    assert _posix_branch_args("tgi") == []
    assert _posix_branch_args("axiom") == []
    assert _posix_branch_args("release/1.2") == ["--branch", "release/1.2"]
    assert _posix_branch_args("release/1.2", bare=True) == []


def _powershell_update_args(branch: str, *, bare: bool = False) -> list[str]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(WINDOWS_HANDOFF),
        "-Branch",
        branch,
        "-SelfTestUpdateArgs",
    ]
    if bare:
        command.append("-BareUpdate")
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout.strip())
    return payload if isinstance(payload, list) else [payload]


def test_windows_deploy_branches_are_bare_and_generic_branches_stay_pinned() -> None:
    base = ["-m", "hermes_cli.main", "update", "--yes", "--gateway", "--force"]
    assert _powershell_update_args("tgi") == base
    assert _powershell_update_args("axiom") == base
    assert _powershell_update_args("release/1.2") == [*base, "--branch", "release/1.2"]
    assert _powershell_update_args("release/1.2", bare=True) == base
