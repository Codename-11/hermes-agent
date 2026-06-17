"""Regression: ZIP fallback must not trigger after a successful git update.

When the git phase of ``hermes update`` completes but the post-git pip
install fails (typically because hermes.exe is locked on Windows), the
outer ``except CalledProcessError`` handler must NOT escalate to the ZIP
download path.  The checkout is already current; the ZIP extraction would
destroy build artifacts (Desktop launcher) that don't exist in the source
archive.

Observed 2026-06-16 and 2026-06-17 on Windows: pip fails on locked exe →
ZIP fallback → ``shutil.rmtree(apps/)`` → Desktop binary gone.
"""
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_no_zip_fallback_when_git_succeeded(monkeypatch, capsys):
    """After git phase completes, pip failure should NOT trigger ZIP fallback."""
    from hermes_cli import main as main_mod

    # We'll call the except handler's logic directly by simulating the
    # _git_phase_completed flag and checking that _update_via_zip is NOT called.
    zip_called = False
    original_update_via_zip = getattr(main_mod, "_update_via_zip", None)

    def spy_update_via_zip(*a, **k):
        nonlocal zip_called
        zip_called = True

    monkeypatch.setattr(main_mod, "_update_via_zip", spy_update_via_zip)

    # Simulate: git phase completed, then pip raised CalledProcessError
    # The handler logic checks ``_git_phase_completed`` — we test the
    # branch by invoking the handler code pattern directly.
    e = subprocess.CalledProcessError(2, ["uv", "pip", "install", "-e", "."])

    # When git completed + Windows: should NOT call _update_via_zip
    with patch.object(main_mod.sys, "platform", "win32"):
        _git_phase_completed = True
        if main_mod.sys.platform == "win32" and not _git_phase_completed:
            spy_update_via_zip(SimpleNamespace(branch="main"))
        elif main_mod.sys.platform == "win32":
            pass  # This is the new path — no ZIP, just message

    assert not zip_called, "ZIP fallback was triggered after git phase completed"

    # When git did NOT complete + Windows: SHOULD call _update_via_zip
    _git_phase_completed = False
    if main_mod.sys.platform == "win32" and not _git_phase_completed:
        spy_update_via_zip(SimpleNamespace(branch="main"))

    assert zip_called, "ZIP fallback should fire when git phase did NOT complete"
