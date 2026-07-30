"""Regression tests for Windows ZIP fallback boundaries."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli import update_cmd


def test_no_zip_fallback_when_git_succeeded(monkeypatch, capsys):
    """Dependency failure after git success must preserve the fork checkout."""
    zip_calls = []
    monkeypatch.setattr(update_cmd, "_update_via_zip", lambda args: zip_calls.append(args))
    error = subprocess.CalledProcessError(
        2, ["uv", "pip", "install", "-e", "."]
    )

    with patch.object(update_cmd.sys, "platform", "win32"):
        handled = update_cmd._handle_update_called_process_error(
            error,
            SimpleNamespace(branch=None),
            git_phase_completed=True,
        )

    assert handled is False
    assert zip_calls == []
    output = capsys.readouterr().out
    assert "ZIP fallback disabled" in output


def test_zip_fallback_remains_available_before_git_mutation(monkeypatch):
    """A genuine pre-git Windows failure may still use canonical ZIP recovery."""
    args = SimpleNamespace(branch=None)
    zip_calls = []
    monkeypatch.setattr(update_cmd, "_update_via_zip", lambda value: zip_calls.append(value))
    error = subprocess.CalledProcessError(1, ["git", "fetch"])

    with patch.object(update_cmd.sys, "platform", "win32"):
        handled = update_cmd._handle_update_called_process_error(
            error,
            args,
            git_phase_completed=False,
        )

    assert handled is True
    assert zip_calls == [args]


def test_zip_fallback_is_disabled_for_fork_before_git_mutation(
    monkeypatch, capsys
):
    args = SimpleNamespace(branch="axiom")
    zip_calls = []
    monkeypatch.setattr(update_cmd, "_update_via_zip", lambda value: zip_calls.append(value))
    error = subprocess.CalledProcessError(1, ["git", "fetch"])

    with patch.object(update_cmd.sys, "platform", "win32"):
        handled = update_cmd._handle_update_called_process_error(
            error,
            args,
            git_phase_completed=False,
            is_fork=True,
        )

    assert handled is False
    assert zip_calls == []
    assert "fork checkout" in capsys.readouterr().out
