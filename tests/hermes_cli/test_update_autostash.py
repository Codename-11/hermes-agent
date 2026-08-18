import json
import subprocess
import sys
from io import StringIO
from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import axiom_update as hermes_axiom_update
from hermes_cli import config as hermes_config
from hermes_cli import main as hermes_main


def _plain_git_cmd(cmd):
    """Normalize Windows git -c wrappers so tests assert git semantics."""
    if cmd[:3] == ["git", "-c", "windows.appendAtomically=false"]:
        return ["git"] + cmd[3:]
    return cmd


# ---------------------------------------------------------------------------
# Managed-uv compatibility for tests that patch shutil.which
# ---------------------------------------------------------------------------
# The production code now uses ``ensure_uv()`` / ``update_managed_uv()``
# instead of ``shutil.which("uv")``.  Many tests in this file patch
# ``shutil.which`` to control whether uv is "available" — these autouse
# fixtures make the managed_uv functions delegate to the patched
# ``shutil.which`` so the existing test setup keeps working without
# per-test changes.
@pytest.fixture(autouse=True)
def _patch_managed_uv(request):
    """Make managed_uv helpers follow shutil.which mocking in tests."""
    import shutil

    # resolve_uv delegates to shutil.which("uv") so that test patches
    # on shutil.which flow through naturally.
    def _fake_resolve_uv(**kwargs):
        return shutil.which("uv")

    def _fake_ensure_uv(**kwargs):
        return shutil.which("uv")

    def _fake_update_managed_uv(**kwargs):
        return None  # never actually self-update in tests

    with patch("hermes_cli.managed_uv.resolve_uv", side_effect=_fake_resolve_uv), \
         patch("hermes_cli.managed_uv.ensure_uv", side_effect=_fake_ensure_uv), \
         patch("hermes_cli.managed_uv.update_managed_uv", side_effect=_fake_update_managed_uv), \
         patch.object(hermes_main, "_pause_windows_gateways_for_update", return_value=None), \
         patch.object(hermes_main, "_resume_windows_gateways_after_update"), \
         patch.object(hermes_main, "_detect_concurrent_hermes_instances", return_value=[]), \
         patch.object(hermes_main, "_detect_venv_python_processes", return_value=[]), \
         patch.object(hermes_main, "_quarantine_running_hermes_exe", return_value=[]), \
         patch.object(hermes_main, "_refresh_windows_gateway_launchers"), \
         patch.object(hermes_main, "_cold_start_windows_gateway_after_update"), \
         patch.object(hermes_main, "_write_update_incomplete_marker"), \
         patch.object(hermes_main, "_clear_update_incomplete_marker"), \
         patch.object(hermes_main, "_write_lazy_refresh_incomplete_marker"), \
         patch.object(hermes_main, "_clear_lazy_refresh_incomplete_marker"):
        yield

def test_stash_local_changes_if_needed_returns_none_when_tree_clean(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)

    assert stash_ref is None
    assert [cmd[-2:] for cmd, _ in calls] == [["status", "--porcelain"]]


def test_stash_local_changes_if_needed_returns_specific_stash_commit(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout=" M hermes_cli/main.py\n?? notes.txt\n", returncode=0)
        if cmd[-2:] == ["ls-files", "--unmerged"]:
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[1:4] == ["stash", "push", "--include-untracked"]:
            return SimpleNamespace(stdout="Saved working directory\n", returncode=0)
        if cmd[-3:] == ["rev-parse", "--verify", "refs/stash"]:
            return SimpleNamespace(stdout="abc123\n", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)

    assert stash_ref == "abc123"
    assert calls[1][0][-2:] == ["ls-files", "--unmerged"]
    # Pre-push probe of refs/stash (baseline for detecting a fresh entry),
    # then the push, then the post-push probe.
    assert calls[2][0][-3:] == ["rev-parse", "--verify", "refs/stash"]
    assert calls[3][0][1:4] == ["stash", "push", "--include-untracked"]
    assert calls[4][0][-3:] == ["rev-parse", "--verify", "refs/stash"]


def test_resolve_stash_selector_returns_matching_entry(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        assert cmd == ["git", "stash", "list", "--format=%gd %H"]
        return SimpleNamespace(
            stdout="stash@{0} def456\nstash@{1} abc123\n",
            returncode=0,
        )

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    assert hermes_main._resolve_stash_selector(["git"], tmp_path, "abc123") == "stash@{1}"



def test_restore_stashed_changes_prompts_before_applying(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{1} abc123\n", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "drop"]:
            return SimpleNamespace(stdout="dropped\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda: "")

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=True)

    assert restored is True
    assert calls[0][0] == ["git", "stash", "apply", "abc123"]
    assert calls[1][0] == ["git", "diff", "--name-only", "--diff-filter=U"]
    assert calls[2][0] == ["git", "stash", "list", "--format=%gd %H"]
    assert calls[3][0] == ["git", "stash", "drop", "stash@{1}"]
    out = capsys.readouterr().out
    assert "Restore local changes now? [Y/n]" in out
    assert "restored on top of the updated codebase" in out
    assert "git diff" in out
    assert "git status" in out


def test_restore_stashed_changes_can_skip_restore_and_keep_stash(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda: "n")

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=True)

    assert restored is False
    assert calls == []
    out = capsys.readouterr().out
    assert "Restore local changes now? [Y/n]" in out
    assert "Your changes are still preserved in git stash." in out
    assert "git stash apply abc123" in out


def test_restore_stashed_changes_applies_without_prompt_when_disabled(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{0} abc123\n", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "drop"]:
            return SimpleNamespace(stdout="dropped\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=False)

    assert restored is True
    assert calls[0][0] == ["git", "stash", "apply", "abc123"]
    assert calls[1][0] == ["git", "diff", "--name-only", "--diff-filter=U"]
    assert calls[2][0] == ["git", "stash", "list", "--format=%gd %H"]
    assert calls[3][0] == ["git", "stash", "drop", "stash@{0}"]
    assert "Restore local changes now?" not in capsys.readouterr().out



def test_print_stash_cleanup_guidance_with_selector(capsys):
    hermes_main._print_stash_cleanup_guidance("abc123", "stash@{2}")

    out = capsys.readouterr().out
    assert "Check `git status` first" in out
    assert "git stash list --format='%gd %H %s'" in out
    assert "git stash drop stash@{2}" in out



def test_restore_stashed_changes_keeps_going_when_stash_entry_cannot_be_resolved(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{0} def456\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=False)

    assert restored is True
    _utf8 = {"encoding": "utf-8", "errors": "replace"}
    assert calls[0] == (["git", "stash", "apply", "abc123"], {"cwd": tmp_path, "capture_output": True, "text": True, **_utf8})
    assert calls[1] == (["git", "diff", "--name-only", "--diff-filter=U"], {"cwd": tmp_path, "capture_output": True, "text": True, **_utf8})
    assert calls[2] == (["git", "stash", "list", "--format=%gd %H"], {"cwd": tmp_path, "capture_output": True, "text": True, **_utf8, "check": True})
    out = capsys.readouterr().out
    assert "couldn't find the stash entry to drop" in out
    assert "stash was left in place" in out
    assert "Check `git status` first" in out
    assert "git stash list --format='%gd %H %s'" in out
    assert "Look for commit abc123" in out



def test_restore_stashed_changes_keeps_going_when_drop_fails(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{0} abc123\n", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "drop"]:
            return SimpleNamespace(stdout="", stderr="drop failed\n", returncode=1)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=False)

    assert restored is True
    assert calls[3][0] == ["git", "stash", "drop", "stash@{0}"]
    out = capsys.readouterr().out
    assert "couldn't drop the saved stash entry" in out
    assert "drop failed" in out
    assert "Check `git status` first" in out
    assert "git stash list --format='%gd %H %s'" in out
    assert "git stash drop stash@{0}" in out


def test_restore_stashed_changes_always_resets_on_conflict(monkeypatch, tmp_path, capsys):
    """Conflicts always auto-reset (no prompt) and return False, even interactively.

    Leaving conflict markers in source files makes hermes unrunnable (SyntaxError).
    The stash is preserved for manual recovery; cmd_update continues normally.
    """
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="conflict output\n", stderr="conflict stderr\n", returncode=1)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="hermes_cli/main.py\n", stderr="", returncode=0)
        if cmd[1:3] == ["rev-parse", "--abbrev-ref"]:
            return SimpleNamespace(stdout="axiom\n", stderr="", returncode=0)
        if cmd[1:3] == ["reset", "--hard"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda: "y")

    result = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=True)

    assert result is False
    out = capsys.readouterr().out
    assert "Conflicted files:" in out
    assert "hermes_cli/main.py" in out
    assert "Branch: axiom" in out
    assert "stashed changes are preserved" in out
    assert "Working tree reset to clean state" in out
    assert "git stash apply abc123" in out
    reset_calls = [c for c, _ in calls if c[1:3] == ["reset", "--hard"]]
    assert len(reset_calls) == 1


def test_restore_stashed_changes_auto_resets_non_interactive(monkeypatch, tmp_path, capsys):
    """Non-interactive mode auto-resets without prompting and returns False
    instead of sys.exit(1) so the update can continue (gateway /update path)."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="cli.py\n", stderr="", returncode=0)
        if cmd[1:3] == ["rev-parse", "--abbrev-ref"]:
            return SimpleNamespace(stdout="axiom\n", stderr="", returncode=0)
        if cmd[1:3] == ["reset", "--hard"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    result = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=False)

    assert result is False
    out = capsys.readouterr().out
    assert "Working tree reset to clean state" in out
    reset_calls = [c for c, _ in calls if c[1:3] == ["reset", "--hard"]]
    assert len(reset_calls) == 1


def test_stash_local_changes_if_needed_raises_when_stash_ref_missing(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout=" M hermes_cli/main.py\n", returncode=0)
        if cmd[-2:] == ["ls-files", "--unmerged"]:
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[1:4] == ["stash", "push", "--include-untracked"]:
            return SimpleNamespace(stdout="Saved working directory\n", returncode=0)
        if cmd[-3:] == ["rev-parse", "--verify", "refs/stash"]:
            raise CalledProcessError(returncode=128, cmd=cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    with pytest.raises(CalledProcessError):
        hermes_main._stash_local_changes_if_needed(["git"], Path(tmp_path))


def test_discard_lockfile_churn_skips_lock_when_package_json_dirty(tmp_path):
    """Intentional dependency edits update package.json and lockfile together."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "package.json").write_text('{"dependencies":{"a":"1"}}\n')
    (tmp_path / "package-lock.json").write_text('{"lock":"old"}\n')
    git("add", "package.json", "package-lock.json")
    git("commit", "-qm", "init")

    (tmp_path / "package.json").write_text('{"dependencies":{"a":"2"}}\n')
    (tmp_path / "package-lock.json").write_text('{"lock":"new"}\n')

    hermes_main._discard_lockfile_churn(["git"], tmp_path)

    assert (tmp_path / "package-lock.json").read_text() == '{"lock":"new"}\n'


def test_discard_lockfile_churn_restores_lock_when_package_json_clean(tmp_path):
    """Runtime npm lockfile rewrites are still discarded on managed updates."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "package.json").write_text('{"dependencies":{"a":"1"}}\n')
    (tmp_path / "package-lock.json").write_text('{"lock":"old"}\n')
    git("add", "package.json", "package-lock.json")
    git("commit", "-qm", "init")

    (tmp_path / "package-lock.json").write_text('{"lock":"runtime-churn"}\n')

    hermes_main._discard_lockfile_churn(["git"], tmp_path)

    assert (tmp_path / "package-lock.json").read_text() == '{"lock":"old"}\n'


# ---------------------------------------------------------------------------
# Axiom/deploy branch updater
# ---------------------------------------------------------------------------

def test_deploy_branch_update_fast_forwards_when_origin_ahead(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "origin",
            "axiom:refs/remotes/origin/axiom",
            "--quiet",
        ]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return SimpleNamespace(stdout="2\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/axiom"]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="2\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "oldhead"
    )

    assert changed == 2
    assert [cmd for cmd, _ in calls if cmd[:3] == ["git", "merge", "--ff-only"]] == [
        ["git", "merge", "--ff-only", "origin/axiom"]
    ]


def test_deploy_branch_update_pins_exact_staged_target(monkeypatch, tmp_path):
    target = "a" * 40
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-parse", "origin/axiom"]:
            return SimpleNamespace(stdout=f"{target}\n", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", target]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="3\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "oldhead", target_sha=target
    )

    assert changed == 3
    assert ["git", "fetch", "upstream", "--quiet"] not in calls
    assert ["git", "merge", "--ff-only", target] in calls


def test_deploy_branch_update_rejects_moved_staged_target(monkeypatch, tmp_path):
    target = "a" * 40
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-parse", "origin/axiom"]:
            return SimpleNamespace(stdout=f"{'b' * 40}\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "oldhead", target_sha=target
    )

    assert changed is None
    assert not any(cmd[:3] == ["git", "merge", "--ff-only"] for cmd in calls)


def test_sync_deploy_main_to_upstream_fast_forwards_without_checkout(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="2\n", stderr="", returncode=0)
        if cmd == ["git", "branch", "-f", "main", "upstream/main"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    assert hermes_main._sync_deploy_main_to_upstream(["git"], tmp_path) is True
    assert ["git", "branch", "-f", "main", "upstream/main"] in calls


def test_sync_deploy_main_to_upstream_refuses_local_main_commits(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="2\n", stderr="", returncode=0)
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return SimpleNamespace(stdout="abc123\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    assert hermes_main._sync_deploy_main_to_upstream(["git"], tmp_path) is False
    assert ["git", "branch", "-f", "main", "upstream/main"] not in calls
    out = capsys.readouterr().out
    assert "local main has commits that are not on upstream/main" in out


def test_deploy_branch_update_merges_live_ahead_with_origin_then_upstream(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "update-parent"
    parent.mkdir()
    worktree_path = parent / "worktree"
    calls = []

    monkeypatch.setattr(hermes_main.tempfile, "mkdtemp", lambda prefix: str(parent))

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "origin",
            "axiom:refs/remotes/origin/axiom",
            "--quiet",
        ]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return SimpleNamespace(stdout="10\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return SimpleNamespace(stdout="30\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"]:
            worktree_path.mkdir()
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--no-edit", "origin/axiom"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge origin\n", stderr="", returncode=0)
        if cmd == ["git", "merge", "--no-edit", "upstream/main"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge upstream\n", stderr="", returncode=0)
        if cmd == ["git", "push", "origin", "HEAD:axiom"] and cwd == worktree_path:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/axiom"] and cwd == repo:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree_path), "--force"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="7\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(["git"], repo, "axiom", "oldhead")

    assert changed == 7
    commands = [cmd for cmd, _ in calls]
    assert commands.index(["git", "merge", "--no-edit", "origin/axiom"]) < commands.index(
        ["git", "merge", "--no-edit", "upstream/main"]
    )
    assert commands.index(["git", "push", "origin", "HEAD:axiom"]) < commands.index(
        ["git", "merge", "--ff-only", "origin/axiom"]
    )
    assert not parent.exists()


def test_deploy_handoff_marker_completes_when_live_origin_and_upstream_match(
    monkeypatch, tmp_path
):
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps({"repo": str(tmp_path), "branch": "axiom", "pre_update_head": "oldhead"}),
        encoding="utf-8",
    )
    calls = []

    # _completed_deploy_handoff_requires_post_update lives in
    # hermes_cli.axiom_update (extracted from main.py to shrink the fork's
    # footprint in upstream's most-refactored file). Patch the dependencies on
    # the module where the function actually resolves them, not on
    # hermes_cli.main. _count_commits_between is still imported lazily from
    # main inside the function, so it reads hermes_main.subprocess too — patch
    # both module surfaces so every git call routes through fake_run.
    from hermes_cli import axiom_update as hermes_axiom_update

    monkeypatch.setattr(
        hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "merge-base", "--is-ancestor", "upstream/main", "origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    assert hermes_axiom_update._completed_deploy_handoff_requires_post_update(
        ["git"], tmp_path, "axiom"
    ) is True
    assert not marker.exists()
    assert ["git", "merge-base", "--is-ancestor", "upstream/main", "origin/axiom"] in calls


def test_deploy_branch_update_merges_upstream_in_temp_worktree(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "update-parent"
    parent.mkdir()
    worktree_path = parent / "worktree"
    calls = []

    monkeypatch.setattr(hermes_main.tempfile, "mkdtemp", lambda prefix: str(parent))

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "origin",
            "axiom:refs/remotes/origin/axiom",
            "--quiet",
        ]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return SimpleNamespace(stdout="3\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "add", "--detach", str(worktree_path), "origin/axiom"]:
            worktree_path.mkdir()
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--no-edit", "upstream/main"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge made\n", stderr="", returncode=0)
        if cmd == ["git", "push", "origin", "HEAD:axiom"] and cwd == worktree_path:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/axiom"] and cwd == repo:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree_path), "--force"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="4\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(["git"], repo, "axiom", "oldhead")

    assert changed == 4
    commands = [cmd for cmd, _ in calls]
    assert commands.index(["git", "push", "origin", "HEAD:axiom"]) < commands.index(
        ["git", "merge", "--ff-only", "origin/axiom"]
    )
    assert not parent.exists()


def test_deploy_branch_publish_only_pushes_upstream_without_fast_forwarding_live(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "update-parent"
    parent.mkdir()
    worktree_path = parent / "worktree"
    calls = []

    monkeypatch.setattr(hermes_main.tempfile, "mkdtemp", lambda prefix: str(parent))

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        responses = {
            ("git", "fetch", "upstream", "--quiet"): "",
            ("git", "fetch", "origin", "axiom:refs/remotes/origin/axiom", "--quiet"): "",
            ("git", "rev-list", "--count", "HEAD..origin/axiom"): "0\n",
            ("git", "rev-list", "--count", "origin/axiom..HEAD"): "0\n",
            ("git", "rev-list", "--count", "origin/axiom..upstream/main"): "2\n",
        }
        key = tuple(cmd)
        if key in responses:
            return SimpleNamespace(stdout=responses[key], stderr="", returncode=0)
        if cmd == ["git", "worktree", "add", "--detach", str(worktree_path), "origin/axiom"]:
            worktree_path.mkdir()
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--no-edit", "upstream/main"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge made\n", stderr="", returncode=0)
        if cmd == ["git", "push", "origin", "HEAD:axiom"] and cwd == worktree_path:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree_path), "--force"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(
        ["git"], repo, "axiom", "oldhead", publish_only=True
    )

    commands = [cmd for cmd, _ in calls]
    assert changed == 2
    assert ["git", "push", "origin", "HEAD:axiom"] in commands
    assert ["git", "merge", "--ff-only", "origin/axiom"] not in commands
    assert not parent.exists()


def test_sync_upstream_to_deploy_publishes_origin_without_moving_live_head(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    live = tmp_path / "live"

    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "init", "--bare", "-q", str(upstream)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    subprocess.run(["git", "config", "user.name", "E2E Test"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.email", "e2e@example.invalid"], cwd=seed, check=True)
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=seed, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=seed, text=True).strip()
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=seed, check=True)
    subprocess.run(["git", "remote", "add", "upstream", str(upstream)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main:axiom"], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "upstream", "main"], cwd=seed, check=True)
    (seed / "upstream.txt").write_text("upstream\n", encoding="utf-8")
    subprocess.run(["git", "add", "upstream.txt"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "upstream"], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "upstream", "main"], cwd=seed, check=True)

    subprocess.run(["git", "clone", "-q", "--branch", "axiom", str(origin), str(live)], check=True)
    subprocess.run(["git", "branch", "main", base], cwd=live, check=True)
    subprocess.run(["git", "remote", "add", "upstream", str(upstream)], cwd=live, check=True)
    subprocess.run(["git", "config", "user.name", "E2E Test"], cwd=live, check=True)
    subprocess.run(["git", "config", "user.email", "e2e@example.invalid"], cwd=live, check=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=live, text=True).strip()
    main_before = subprocess.check_output(
        ["git", "rev-parse", "refs/heads/main"], cwd=live, text=True
    ).strip()
    result = hermes_axiom_update.sync_upstream_to_deploy(live, "axiom")
    after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=live, text=True).strip()
    main_after = subprocess.check_output(
        ["git", "rev-parse", "refs/heads/main"], cwd=live, text=True
    ).strip()
    published = subprocess.check_output(
        ["git", "--git-dir", str(origin), "rev-parse", "refs/heads/axiom"], text=True
    ).strip()
    subprocess.run(
        ["git", "--git-dir", str(origin), "fetch", "-q", str(upstream), "main:refs/remotes/check/main"],
        check=True,
    )
    contained = subprocess.run(
        [
            "git",
            "--git-dir",
            str(origin),
            "merge-base",
            "--is-ancestor",
            "refs/remotes/check/main",
            "refs/heads/axiom",
        ]
    )

    assert result["ok"] is True
    assert result["state"] == "completed"
    assert result["branch"] == "axiom"
    assert result["reconciled"] == 1
    assert result["targetSha"] == published[:7]
    assert before == after == base
    assert main_before == main_after == base
    assert published != before
    assert contained.returncode == 0


def test_sync_upstream_to_deploy_retries_retained_handoff_even_without_pending_divergence(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    def fake_run(cmd, **kwargs):
        if cmd[-3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="axiom\n", stderr="", returncode=0)
        if cmd[-3:-1] == ["remote", "get-url"]:
            return SimpleNamespace(stdout="local\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_exists_for", lambda *_: True)
    monkeypatch.setattr(hermes_axiom_update, "_short_git_ref", lambda *args: "oldhead")
    monkeypatch.setattr(
        hermes_axiom_update,
        "_resolve_deploy_handoff",
        lambda **kwargs: calls.append(kwargs) or None,
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_read_deploy_handoff_payload",
        lambda *_: {"worktree": "/retained/worktree", "report_path": "/retained/report.md"},
    )

    result = hermes_axiom_update.sync_upstream_to_deploy(repo, "axiom")

    assert result == {
        "ok": False,
        "state": "handoff",
        "error": "reconciliation-stopped",
        "message": "Upstream reconciliation stopped safely; the live checkout was not changed.",
        "worktree": "/retained/worktree",
        "reportPath": "/retained/report.md",
    }
    assert calls[0]["publish_only"] is True


def test_sync_upstream_to_deploy_reports_successful_retained_handoff(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(cmd, **kwargs):
        if cmd[-3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="axiom\n", stderr="", returncode=0)
        if cmd[-3:-1] == ["remote", "get-url"]:
            return SimpleNamespace(stdout="local\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_exists_for", lambda *_: True)
    monkeypatch.setattr(hermes_axiom_update, "_short_git_ref", lambda *args: "abc1234")
    monkeypatch.setattr(hermes_axiom_update, "_resolve_deploy_handoff", lambda **kwargs: 1)

    result = hermes_axiom_update.sync_upstream_to_deploy(repo, "axiom")

    assert result == {
        "ok": True,
        "state": "completed",
        "branch": "axiom",
        "reconciled": 1,
        "targetSha": "abc1234",
        "message": "Resolved the retained handoff and published origin/axiom.",
    }


def test_deploy_branch_update_recovers_when_push_reject_remote_already_contains_merge(
    monkeypatch, tmp_path, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "update-parent"
    parent.mkdir()
    worktree_path = parent / "worktree"
    calls = []

    monkeypatch.setattr(hermes_main.tempfile, "mkdtemp", lambda prefix: str(parent))

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "origin",
            "axiom:refs/remotes/origin/axiom",
            "--quiet",
        ]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return SimpleNamespace(stdout="3\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "add", "--detach", str(worktree_path), "origin/axiom"]:
            worktree_path.mkdir()
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--no-edit", "upstream/main"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge made\n", stderr="", returncode=0)
        if cmd == ["git", "push", "origin", "HEAD:axiom"] and cwd == worktree_path:
            return SimpleNamespace(stdout="", stderr="remote advanced\n", returncode=1)
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge-base", "--is-ancestor", "HEAD", "origin/axiom"] and cwd == worktree_path:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/axiom"] and cwd == repo:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree_path), "--force"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="3\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(["git"], repo, "axiom", "oldhead")

    assert changed == 3
    assert not parent.exists()
    commands = [cmd for cmd, _ in calls]
    assert commands.count(["git", "push", "origin", "HEAD:axiom"]) == 1
    assert ["git", "merge", "--ff-only", "origin/axiom"] in commands
    out = capsys.readouterr().out
    assert "origin/axiom advanced during update; reconciling once" in out
    assert "already contains this deploy merge" in out
    assert "hermes update: push to origin/axiom failed" not in out


def test_deploy_branch_update_retries_push_after_merging_remote_advanced_origin(
    monkeypatch, tmp_path, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "update-parent"
    parent.mkdir()
    worktree_path = parent / "worktree"
    calls = []
    first_push = {"done": False}

    monkeypatch.setattr(hermes_main.tempfile, "mkdtemp", lambda prefix: str(parent))

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "origin",
            "axiom:refs/remotes/origin/axiom",
            "--quiet",
        ]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return SimpleNamespace(stdout="3\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "add", "--detach", str(worktree_path), "origin/axiom"]:
            worktree_path.mkdir()
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--no-edit", "upstream/main"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge upstream\n", stderr="", returncode=0)
        if cmd == ["git", "push", "origin", "HEAD:axiom"] and cwd == worktree_path:
            if not first_push["done"]:
                first_push["done"] = True
                return SimpleNamespace(stdout="", stderr="remote advanced\n", returncode=1)
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge-base", "--is-ancestor", "HEAD", "origin/axiom"] and cwd == worktree_path:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if cmd == ["git", "merge-base", "--is-ancestor", "HEAD", "origin/axiom"] and cwd == repo:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if cmd == ["git", "merge-base", "--is-ancestor", "upstream/main", "origin/axiom"] and cwd == repo:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if cmd == ["git", "merge", "--no-edit", "origin/axiom"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge origin\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/axiom"] and cwd == repo:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree_path), "--force"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="4\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(["git"], repo, "axiom", "oldhead")

    assert changed == 4
    assert not parent.exists()
    commands = [cmd for cmd, _ in calls]
    assert commands.count(["git", "push", "origin", "HEAD:axiom"]) == 2
    assert ["git", "merge", "--no-edit", "origin/axiom"] in commands
    assert ["git", "merge", "--ff-only", "origin/axiom"] in commands
    out = capsys.readouterr().out
    assert "Reconciled remote-advanced origin/axiom and pushed retry merge" in out
    assert "hermes update: push to origin/axiom failed" not in out


def test_deploy_branch_update_conflict_prints_handoff_and_keeps_worktree(
    monkeypatch, tmp_path, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "update-parent"
    parent.mkdir()
    worktree_path = parent / "worktree"
    calls = []

    from hermes_cli import axiom_update as hermes_axiom_update

    monkeypatch.setattr(hermes_main.tempfile, "mkdtemp", lambda prefix: str(parent))
    monkeypatch.setattr(hermes_axiom_update, "_review_reports_dir", lambda: tmp_path / "reports")
    marker = tmp_path / ".update_handoff.json"
    monkeypatch.setattr(
        hermes_axiom_update,
        "_deploy_handoff_marker_path",
        lambda: marker,
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_call_llm_update_review",
        lambda review: ("LLM says: pause, resolve in worktree, run focused tests.", ""),
    )
    resolver_calls = []
    monkeypatch.setattr(
        hermes_axiom_update,
        "_resolve_deploy_handoff",
        lambda **kwargs: resolver_calls.append(kwargs) or None,
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "origin",
            "axiom:refs/remotes/origin/axiom",
            "--quiet",
        ]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return SimpleNamespace(stdout="5\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "add", "--detach", str(worktree_path), "origin/axiom"]:
            worktree_path.mkdir()
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--no-edit", "upstream/main"] and cwd == worktree_path:
            return SimpleNamespace(stdout="CONFLICT\n", stderr="", returncode=1)
        if cmd == ["git", "diff", "--name-only", "--diff-filter=U"] and cwd == worktree_path:
            return SimpleNamespace(stdout="hermes_cli/main.py\n", stderr="", returncode=0)
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return SimpleNamespace(stdout="abc123\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(["git"], repo, "axiom", "oldhead")

    assert changed is None
    assert worktree_path.exists()
    commands = [cmd for cmd, _ in calls]
    assert ["git", "push", "origin", "HEAD:axiom"] not in commands
    assert ["git", "merge", "--ff-only", "origin/axiom"] not in commands
    out = capsys.readouterr().out
    assert "Update conflict review" in out
    assert "LLM says: pause" in out
    assert "Full report:" in out
    assert "hermes update: merge into axiom failed." in out
    assert "Worktree:" in out
    assert "hermes_cli/main.py" in out
    assert "starting automatic resolution" in out
    assert resolver_calls
    reports = list((tmp_path / "reports").glob("*-axiom-conflict-review.md"))
    assert len(reports) == 1
    report_text = reports[0].read_text(encoding="utf-8")
    assert "LLM says: pause" in report_text
    assert "hermes_cli/main.py" in report_text
    assert "Deploy-branch-safe updater" in report_text
    marker_text = marker.read_text(encoding="utf-8")
    marker_payload = json.loads(marker_text)
    assert marker_payload["schema"] == 3
    assert marker_payload["conflict_files"] == ["hermes_cli/main.py"]
    assert marker_payload["report_path"]
    assert marker_payload["focused_checks"]


def test_deploy_update_first_host_publishes_second_host_consumes_real_git(tmp_path):
    """Resolve/publish happens once across two real deploy checkouts."""

    def git(cwd, *args, capture=False):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=capture,
            text=True,
        )

    upstream_bare = tmp_path / "upstream.git"
    origin_bare = tmp_path / "origin.git"
    upstream_work = tmp_path / "upstream-work"
    host_a = tmp_path / "host-a"
    host_b = tmp_path / "host-b"

    git(tmp_path, "init", "--bare", str(upstream_bare))
    git(tmp_path, "init", "--bare", str(origin_bare))
    git(tmp_path, "init", "-b", "main", str(upstream_work))
    git(upstream_work, "config", "user.name", "Hermes Test")
    git(upstream_work, "config", "user.email", "hermes-test@example.invalid")
    (upstream_work / "shared.txt").write_text("base\n", encoding="utf-8")
    git(upstream_work, "add", "shared.txt")
    git(upstream_work, "commit", "-m", "base")
    git(upstream_work, "remote", "add", "upstream-bare", str(upstream_bare))
    git(upstream_work, "push", "upstream-bare", "main")
    git(upstream_work, "remote", "add", "fork", str(origin_bare))
    git(upstream_work, "push", "fork", "main")

    git(upstream_work, "checkout", "-b", "axiom")
    (upstream_work / "fork.txt").write_text("axiom\n", encoding="utf-8")
    git(upstream_work, "add", "fork.txt")
    git(upstream_work, "commit", "-m", "axiom carry")
    git(upstream_work, "push", "fork", "axiom")

    for host in (host_a, host_b):
        git(tmp_path, "clone", "--branch", "axiom", str(origin_bare), str(host))
        git(host, "config", "user.name", "Hermes Test")
        git(host, "config", "user.email", "hermes-test@example.invalid")
        git(host, "remote", "add", "upstream", str(upstream_bare))
        git(host, "fetch", "upstream", "main")
        git(host, "branch", "main", "upstream/main")

    host_b_before = git(host_b, "rev-parse", "HEAD", capture=True).stdout.strip()

    git(upstream_work, "checkout", "main")
    (upstream_work / "shared.txt").write_text("base\nupstream\n", encoding="utf-8")
    git(upstream_work, "add", "shared.txt")
    git(upstream_work, "commit", "-m", "upstream feature")
    git(upstream_work, "push", "upstream-bare", "main")

    host_a_before = git(host_a, "rev-parse", "HEAD", capture=True).stdout.strip()
    changed_a = hermes_axiom_update._run_deploy_branch_update(
        ["git"], host_a, "axiom", host_a_before
    )
    published = git(host_a, "rev-parse", "HEAD", capture=True).stdout.strip()
    origin_after_a = git(
        origin_bare, "rev-parse", "refs/heads/axiom", capture=True
    ).stdout.strip()

    assert changed_a and changed_a > 0
    assert published == origin_after_a
    git(host_a, "merge-base", "--is-ancestor", "upstream/main", "HEAD")

    changed_b = hermes_axiom_update._run_deploy_branch_update(
        ["git"], host_b, "axiom", host_b_before
    )
    host_b_after = git(host_b, "rev-parse", "HEAD", capture=True).stdout.strip()
    origin_after_b = git(
        origin_bare, "rev-parse", "refs/heads/axiom", capture=True
    ).stdout.strip()

    assert changed_b and changed_b > 0
    assert host_b_after == published
    assert origin_after_b == origin_after_a


def test_deploy_handoff_resolve_runs_agent_pushes_and_fast_forwards(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "README.md").write_text("resolved\n", encoding="utf-8")
    (worktree / "MERGE_HEAD").write_text("merge\n", encoding="utf-8")
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "schema": 2,
                "repo": str(repo),
                "branch": "axiom",
                "reason": "merge into axiom failed.",
                "worktree": str(worktree),
                "conflict_files": ["README.md"],
                "focused_checks": [],
            }
        ),
        encoding="utf-8",
    )
    calls = []
    resolved_head = "a" * 40

    monkeypatch.setattr(
        hermes_axiom_update,
        "_deploy_handoff_marker_path",
        lambda: marker,
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_update_resolver_agent",
        lambda prompt, cwd: SimpleNamespace(returncode=0),
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "upstream", "main", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "diff", "--name-only", "--diff-filter=U"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "status", "--porcelain", "--untracked-files=all"] and cwd == worktree:
            return SimpleNamespace(stdout=" M README.md\n", stderr="", returncode=0)
        if cmd == ["git", "diff", "--check"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "add", "--update"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "diff", "--cached", "--check"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == [
            "git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "*.py"
        ] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "diff", "--cached", "--quiet"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if cmd == ["git", "rev-parse", "--git-path", "MERGE_HEAD"] and cwd == worktree:
            return SimpleNamespace(stdout="MERGE_HEAD\n", stderr="", returncode=0)
        if cmd == ["git", "commit", "--no-edit"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-parse", "--verify", "HEAD^{commit}"] and cwd == worktree:
            return SimpleNamespace(stdout=f"{resolved_head}\n", stderr="", returncode=0)
        if cmd == ["git", "push", "origin", f"{resolved_head}:axiom"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/axiom"] and cwd == repo:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"] and cwd == repo:
            return SimpleNamespace(stdout="2\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree), "--force"] and cwd == repo:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_axiom_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="axiom", pre_update_head="oldhead"
    )

    assert changed == 2
    assert not marker.exists()
    commands = [cmd for cmd, _ in calls]
    assert ["git", "commit", "--no-edit"] in commands
    assert ["git", "push", "origin", f"{resolved_head}:axiom"] in commands
    assert ["git", "merge", "--ff-only", "origin/axiom"] in commands
    out = capsys.readouterr().out
    assert "prepare resolve" in out
    assert "agent resolve" in out
    assert "Live Hermes resolver session (advisory)" in out
    assert "authoritative parent validation" in out
    assert "sync live" in out
    assert "resolved handoff" in out
    assert "Resolved deploy handoff" in out
    assert "\r" not in out
    assert not any(frame in out for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


def test_deploy_resolver_prompt_assigns_only_structural_validation_to_child():
    from hermes_cli import axiom_update

    prompt = axiom_update._build_deploy_resolver_prompt(
        {
            "repo": "/repo",
            "branch": "axiom",
            "worktree": "/worktree",
            "reason": "conflict",
            "resolver_brief_path": "/reports/brief.md",
            "conflict_files": ["hermes_cli/axiom_update.py"],
        },
        ["python -m pytest tests/hermes_cli/test_update_autostash.py"],
    )

    assert "git diff --check" in prompt
    assert "no unmerged paths" in prompt
    assert "Resolver brief: /reports/brief.md" in prompt
    assert "FORK.md" not in prompt
    assert "axiom-fork-contract" not in prompt
    assert "Obsidian" not in prompt
    assert "skill_view" not in prompt
    assert "python -m pytest" not in prompt
    assert "vitest" not in prompt
    assert "typecheck" not in prompt
    assert "focused verification" not in prompt.lower()


def test_deploy_resolver_prompt_includes_failed_parent_diagnostics_for_repair():
    from hermes_cli import axiom_update

    prompt = axiom_update._build_deploy_resolver_prompt(
        {
            "repo": "/repo",
            "branch": "axiom",
            "worktree": "/worktree",
            "reason": "conflict",
            "phase": "repair_pending",
            "conflict_files": ["apps/desktop/src/store/profile.ts"],
            "check_ledger": {
                "resolved_sha": "a" * 40,
                "results": {
                    "desktop-typecheck": {
                        "status": "failed",
                        "output_tail": "profile.ts: missing exported member $hiddenProfiles",
                    }
                },
            },
        },
        [],
    )

    assert "parent validation repair" in prompt.lower()
    assert "desktop-typecheck" in prompt
    assert "missing exported member $hiddenProfiles" in prompt
    assert "Do not rerun parent-owned checks" in prompt


def test_failed_parent_check_immediately_schedules_bounded_resolver_repair(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "branch": "axiom",
                "repo": str(tmp_path),
                "phase": "validation_failed",
                "check_ledger": {
                    "results": {
                        "desktop-typecheck": {
                            "status": "failed",
                            "output_tail": "missing export",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    calls = []
    monkeypatch.setattr(
        axiom_update,
        "_resolve_deploy_handoff",
        lambda **kwargs: calls.append(kwargs) or 3,
    )

    result = axiom_update._retry_validation_with_resolver(
        git_cmd=["git"],
        repo=tmp_path,
        branch="axiom",
        pre_update_head="old",
        publish_only=False,
    )

    assert result == 3
    assert len(calls) == 1
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["phase"] == "repair_pending"
    assert payload["validation_repair_attempts"] == 1


def test_validation_repair_does_not_send_dependency_prep_failure_to_resolver(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "branch": "axiom",
                "repo": str(tmp_path),
                "phase": "validation_failed",
                "error": "npm ci failed",
                "check_ledger": {"results": {}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        axiom_update,
        "_resolve_deploy_handoff",
        lambda **kwargs: pytest.fail("resolver must not repair environment failures"),
    )

    assert (
        axiom_update._retry_validation_with_resolver(
            git_cmd=["git"],
            repo=tmp_path,
            branch="axiom",
            pre_update_head="old",
        )
        is None
    )


def test_validation_repair_stops_at_bounded_attempt_limit(monkeypatch, tmp_path):
    from hermes_cli import axiom_update

    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "branch": "axiom",
                "repo": str(tmp_path),
                "phase": "validation_failed",
                "validation_repair_attempts": 2,
                "check_ledger": {
                    "results": {
                        "desktop-typecheck": {
                            "status": "failed",
                            "output_tail": "still broken",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        axiom_update,
        "_resolve_deploy_handoff",
        lambda **kwargs: pytest.fail("repair loop exceeded its cap"),
    )

    assert (
        axiom_update._retry_validation_with_resolver(
            git_cmd=["git"],
            repo=tmp_path,
            branch="axiom",
            pre_update_head="old",
        )
        is None
    )


def test_checkpoint_resolved_handoff_rejects_untracked_files_before_commit(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repo / "package-lock.generated").write_text("generated\n", encoding="utf-8")

    marker = tmp_path / ".update_handoff.json"
    marker.write_text(json.dumps({"phase": "resolve_pending"}), encoding="utf-8")
    monkeypatch.setattr(axiom_update, "_deploy_handoff_marker_path", lambda: marker)

    resolved_head, error = axiom_update._checkpoint_resolved_handoff(
        ["git"], repo, "axiom"
    )

    assert resolved_head == ""
    assert "unexpected untracked" in error.lower()
    assert subprocess.run(
        ["git", "diff", "--quiet", "HEAD"], cwd=repo
    ).returncode == 1
    assert json.loads(marker.read_text(encoding="utf-8"))["phase"] == "resolve_pending"


def test_live_sync_discards_generated_root_lockfile_churn(tmp_path):
    from hermes_cli import axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "package.json").write_text('{"name":"test"}\n', encoding="utf-8")
    (repo / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    subprocess.run(["git", "add", "package.json", "package-lock.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    (repo / "package-lock.json").write_text('{"runtime":"npm churn"}\n', encoding="utf-8")

    discarded = axiom_update._discard_generated_live_lockfile_churn(["git"], repo)

    assert discarded == ["package-lock.json"]
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout == ""
    assert (repo / "package-lock.json").read_text(encoding="utf-8") == '{"lockfileVersion":3}\n'


def test_live_sync_preserves_lockfile_when_manifest_is_also_modified(tmp_path):
    from hermes_cli import axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "package.json").write_text('{"name":"test"}\n', encoding="utf-8")
    (repo / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    subprocess.run(["git", "add", "package.json", "package-lock.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    (repo / "package.json").write_text('{"name":"test","dependencies":{"x":"1"}}\n', encoding="utf-8")
    (repo / "package-lock.json").write_text('{"intentional":"dependency update"}\n', encoding="utf-8")

    discarded = axiom_update._discard_generated_live_lockfile_churn(["git"], repo)

    assert discarded == []
    changed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "package.json" in changed
    assert "package-lock.json" in changed


def test_live_sync_discards_obsolete_case_collision_when_physical_blob_is_tracked(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    upper = "contributors/emails/agent@Agents-Mac-mini.local"
    lower = "contributors/emails/agent@agents-Mac-mini.local"
    physical = repo / upper
    physical.parent.mkdir(parents=True)
    physical.write_text("skip-agent\n", encoding="utf-8")
    subprocess.run(["git", "add", upper], cwd=repo, check=True)
    lower_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="momomojo\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"100644,{lower_blob},{lower}"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "case collision"], cwd=repo, check=True, capture_output=True)
    empty_tree = subprocess.run(
        ["git", "mktree"], cwd=repo, input="", capture_output=True, text=True, check=True
    ).stdout.strip()
    target = subprocess.run(
        ["git", "commit-tree", empty_tree, "-p", "HEAD", "-m", "remove collision"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(["git", "update-ref", "refs/remotes/origin/axiom", target], cwd=repo, check=True)
    monkeypatch.setattr(axiom_update.os, "name", "nt")

    discarded = axiom_update._discard_obsolete_live_case_collisions(
        ["git"], repo, "origin/axiom"
    )

    assert discarded == [upper, lower]
    assert not physical.exists()


def test_live_sync_preserves_obsolete_case_collision_with_untracked_content(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    upper = "contributors/emails/agent@Agents-Mac-mini.local"
    lower = "contributors/emails/agent@agents-Mac-mini.local"
    physical = repo / upper
    physical.parent.mkdir(parents=True)
    physical.write_text("skip-agent\n", encoding="utf-8")
    subprocess.run(["git", "add", upper], cwd=repo, check=True)
    lower_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="momomojo\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"100644,{lower_blob},{lower}"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "case collision"], cwd=repo, check=True, capture_output=True)
    empty_tree = subprocess.run(
        ["git", "mktree"], cwd=repo, input="", capture_output=True, text=True, check=True
    ).stdout.strip()
    target = subprocess.run(
        ["git", "commit-tree", empty_tree, "-p", "HEAD", "-m", "remove collision"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(["git", "update-ref", "refs/remotes/origin/axiom", target], cwd=repo, check=True)
    physical.write_text("real local edit\n", encoding="utf-8")
    monkeypatch.setattr(axiom_update.os, "name", "nt")

    discarded = axiom_update._discard_obsolete_live_case_collisions(
        ["git"], repo, "origin/axiom"
    )

    assert discarded == []
    assert physical.read_text(encoding="utf-8") == "real local edit\n"


def test_checkpoint_resolved_handoff_commits_tracked_resolution_and_persists_sha(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(json.dumps({"phase": "resolve_pending"}), encoding="utf-8")
    monkeypatch.setattr(axiom_update, "_deploy_handoff_marker_path", lambda: marker)

    resolved_head, error = axiom_update._checkpoint_resolved_handoff(
        ["git"], repo, "axiom"
    )

    assert error == ""
    assert len(resolved_head) == 40
    assert subprocess.run(
        ["git", "diff", "--quiet", "HEAD"], cwd=repo
    ).returncode == 0
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["phase"] == "validation_pending"
    assert payload["resolved_head"] == resolved_head
    assert payload["check_status"] == {}


def test_checkpoint_resolved_handoff_rejects_whitespace_errors_already_staged(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("after   \n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)

    marker = tmp_path / ".update_handoff.json"
    marker.write_text(json.dumps({"phase": "resolve_pending"}), encoding="utf-8")
    monkeypatch.setattr(axiom_update, "_deploy_handoff_marker_path", lambda: marker)

    resolved_head, error = axiom_update._checkpoint_resolved_handoff(
        ["git"], repo, "axiom"
    )

    assert resolved_head == ""
    assert "whitespace" in error.lower()
    assert json.loads(marker.read_text(encoding="utf-8"))["phase"] == "resolve_pending"


def test_resolver_timeout_terminates_windows_process_tree(monkeypatch):
    from hermes_cli import axiom_update

    process = SimpleNamespace(pid=4242, poll=lambda: None, kill=lambda: None)
    calls = []
    monkeypatch.setattr(axiom_update.os, "name", "nt")
    monkeypatch.setattr(
        axiom_update.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append((cmd, kwargs))
        or SimpleNamespace(returncode=0),
    )

    terminated = axiom_update._terminate_resolver_process_tree(process)

    assert terminated is True
    assert calls == [
        (["taskkill", "/PID", "4242", "/T", "/F"], {"capture_output": True, "text": True})
    ]


def test_resolver_timeout_kills_parent_when_windows_tree_termination_fails(monkeypatch):
    from hermes_cli import axiom_update

    killed = []
    process = SimpleNamespace(
        pid=4242,
        poll=lambda: None,
        kill=lambda: killed.append(True),
    )
    monkeypatch.setattr(axiom_update.os, "name", "nt")
    monkeypatch.setattr(
        axiom_update.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )

    terminated = axiom_update._terminate_resolver_process_tree(process)

    assert terminated is False
    assert killed == [True]


def test_focused_check_decodes_utf8_output_independent_of_windows_locale(monkeypatch, tmp_path):
    from hermes_cli import axiom_update

    calls = []
    monkeypatch.setattr(
        axiom_update.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append((cmd, kwargs))
        or subprocess.CompletedProcess(cmd, 0, stdout="✓", stderr=""),
    )

    result = axiom_update._run_focused_check("node check.mjs", tmp_path)

    assert result.returncode == 0
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["errors"] == "replace"


def test_parent_validation_serializes_python_before_desktop_and_prepares_once(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    marker = tmp_path / ".update_handoff.json"
    sha = "a" * 40
    marker.write_text(
        json.dumps({"phase": "validation_pending", "resolved_head": sha}),
        encoding="utf-8",
    )
    monkeypatch.setattr(axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    events = []
    monkeypatch.setattr(
        axiom_update,
        "_prepare_isolated_worktree_dependencies",
        lambda worktree: events.append("prepare") or (True, ""),
    )
    monkeypatch.setattr(
        axiom_update,
        "_run_focused_check",
        lambda check, worktree, **kwargs: events.append(check) or True,
    )

    ok = axiom_update._run_parent_handoff_validation(
        tmp_path,
        sha,
        [
            "cd apps/desktop && npm run typecheck",
            "python -m pytest tests/hermes_cli/test_cmd_update.py",
            "cd apps/desktop && npx vitest run src/example.test.ts",
        ],
        {},
    )

    assert ok
    assert events == [
        "python -m pytest tests/hermes_cli/test_cmd_update.py",
        "prepare",
        "cd apps/desktop && npm run typecheck",
        "cd apps/desktop && npx vitest run src/example.test.ts",
    ]
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["phase"] == "commit_push_pending"
    assert set(payload["check_status"]) == {
        "python -m pytest tests/hermes_cli/test_cmd_update.py",
        "cd apps/desktop && npm run typecheck",
        "cd apps/desktop && npx vitest run src/example.test.ts",
    }
    assert all(value == "passed" for value in payload["check_status"].values())


def test_parent_validation_ledger_reuses_only_matching_sha_and_fingerprint(
    monkeypatch, tmp_path
):
    marker = tmp_path / ".update_handoff.json"
    sha = "a" * 40
    marker.write_text(json.dumps({"phase": "validation_pending"}), encoding="utf-8")
    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    calls = []
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_focused_check",
        lambda command, worktree, **kwargs: calls.append(command) or True,
    )
    one = {"id": "one", "kind": "pytest", "command": "python -m pytest one.py", "timeout_seconds": 30}
    two = {"id": "two", "kind": "pytest", "command": "python -m pytest two.py", "timeout_seconds": 30}
    prior = {
        "resolved_sha": sha,
        "results": {
            "one": {
                "check_id": "one",
                "fingerprint": hermes_axiom_update._check_fingerprint(one),
                "status": "passed",
                "returncode": 0,
                "output_tail": "",
                "duration_seconds": 0.1,
                "completed_at": "2026-01-01T00:00:00",
            }
        },
    }

    assert hermes_axiom_update._run_parent_handoff_validation(tmp_path, sha, [one, two], prior)
    assert calls == [two["command"]]

    calls.clear()
    changed = {**one, "command": "python -m pytest changed.py"}
    assert hermes_axiom_update._run_parent_handoff_validation(tmp_path, sha, [changed, two], prior)
    assert calls == [changed["command"], two["command"]]

    calls.clear()
    assert hermes_axiom_update._run_parent_handoff_validation(tmp_path, "b" * 40, [one], prior)
    assert calls == [one["command"]]
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["check_ledger"]["resolved_sha"] == "b" * 40
    assert payload["check_ledger"]["results"]["one"]["fingerprint"] == hermes_axiom_update._check_fingerprint(one)


def test_failed_check_persists_bounded_result_and_resume_restarts_there(monkeypatch, tmp_path):
    marker = tmp_path / ".update_handoff.json"
    sha = "c" * 40
    marker.write_text(json.dumps({"phase": "validation_pending"}), encoding="utf-8")
    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    calls = []
    outcomes = [True, subprocess.CompletedProcess([], 7, stdout="secret=" + "x" * 10_000, stderr="")]
    monkeypatch.setattr(
        hermes_axiom_update, "_run_focused_check",
        lambda command, worktree, **kwargs: calls.append(command) or outcomes.pop(0),
    )
    checks = [
        {"id": "passed", "kind": "python", "command": "python passed.py", "timeout_seconds": 30},
        {"id": "failed", "kind": "python", "command": "python failed.py", "timeout_seconds": 30},
    ]
    assert not hermes_axiom_update._run_parent_handoff_validation(tmp_path, sha, checks, {})
    ledger = json.loads(marker.read_text(encoding="utf-8"))["check_ledger"]
    assert ledger["results"]["failed"]["status"] == "failed"
    assert ledger["results"]["failed"]["returncode"] == 7
    assert len(ledger["results"]["failed"]["output_tail"]) <= 4000

    calls.clear()
    monkeypatch.setattr(
        hermes_axiom_update, "_run_focused_check",
        lambda command, worktree, **kwargs: calls.append(command) or True,
    )
    assert hermes_axiom_update._run_parent_handoff_validation(tmp_path, sha, checks, ledger)
    assert calls == ["python failed.py"]


def test_failed_check_persists_stderr_and_stdout(monkeypatch, tmp_path):
    marker = tmp_path / ".update_handoff.json"
    sha = "d" * 40
    marker.write_text(json.dumps({"phase": "validation_pending"}), encoding="utf-8")
    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_focused_check",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 2, stdout="TypeScript diagnostic", stderr="npm warning"
        ),
    )
    checks = [
        {"id": "typecheck", "kind": "node", "command": "npm run typecheck", "timeout_seconds": 30}
    ]

    assert not hermes_axiom_update._run_parent_handoff_validation(tmp_path, sha, checks, {})
    output = json.loads(marker.read_text(encoding="utf-8"))["check_ledger"]["results"]["typecheck"]["output_tail"]
    assert "npm warning" in output
    assert "TypeScript diagnostic" in output


def test_validation_pending_checkpoint_is_not_discarded_as_stale_snapshot(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update

    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    marker = tmp_path / ".update_handoff.json"
    sha = "a" * 40
    marker.write_text(
        json.dumps(
            {
                "repo": str(repo),
                "branch": "axiom",
                "worktree": str(worktree),
                "phase": "validation_pending",
                "resolved_head": sha,
                "validation_sha": sha,
                "check_status": {},
                "conflict_files": [],
                "focused_checks": [],
                "origin_head": "old-origin",
                "upstream_head": "old-upstream",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(axiom_update, "_full_git_ref", lambda *args: sha)
    monkeypatch.setattr(
        axiom_update,
        "_handoff_snapshot_is_published",
        lambda *args: True,
    )
    validation = []
    monkeypatch.setattr(
        axiom_update,
        "_run_parent_handoff_validation",
        lambda *args: validation.append(args) or True,
    )
    monkeypatch.setattr(
        axiom_update,
        "_discard_published_handoff",
        lambda *args: pytest.fail("checkpoint must not be discarded before validation"),
    )

    def fake_run(cmd, **kwargs):
        if cmd[:3] in (["git", "fetch", "origin"], ["git", "fetch", "upstream"]):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "push", "origin", f"{sha}:axiom"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="offline")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(axiom_update.subprocess, "run", fake_run)

    assert axiom_update._resolve_deploy_handoff(
        git_cmd=["git"],
        repo=repo,
        branch="axiom",
        pre_update_head="live",
        publish_only=True,
    ) is None
    assert validation


def test_published_handoff_snapshot_is_discarded_before_agent_resolve(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    worktree_parent = tmp_path / "hermes-update-axiom-stale"
    worktree = worktree_parent / "worktree"
    worktree.mkdir(parents=True)
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "schema": 2,
                "repo": str(repo),
                "branch": "axiom",
                "worktree": str(worktree),
                "conflict_files": ["README.md"],
                "origin_head": "old-origin",
                "upstream_head": "old-upstream",
            }
        ),
        encoding="utf-8",
    )
    calls = []
    fresh_updates = []

    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_update_resolver_agent",
        lambda *args, **kwargs: pytest.fail("stale handoff must not launch an agent"),
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_deploy_branch_update",
        lambda git_cmd, cwd, branch, pre_update_head: fresh_updates.append(
            (git_cmd, cwd, branch, pre_update_head)
        )
        or 4,
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd in (
            ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"],
            ["git", "fetch", "upstream", "main", "--quiet"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd in (
            ["git", "merge-base", "--is-ancestor", "old-origin", "origin/axiom"],
            ["git", "merge-base", "--is-ancestor", "old-upstream", "origin/axiom"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree), "--force"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)

    changed = hermes_axiom_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="axiom", pre_update_head="live-head"
    )

    assert changed == 4
    assert fresh_updates == [(["git"], repo, "axiom", "live-head")]
    assert not marker.exists()
    assert any(
        cmd == ["git", "worktree", "remove", str(worktree), "--force"]
        for cmd, _kwargs in calls
    )
    out = capsys.readouterr().out
    assert "already published" in out
    assert "starting a fresh deploy update" in out


def test_push_recovery_handoff_preserves_diagnostics_and_fresh_chat_command(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    marker = tmp_path / ".update_handoff.json"
    resolved_head = "a" * 40
    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_short_git_ref",
        lambda _git, _cwd, ref: {
            "HEAD": "resolved123",
            "origin/axiom": "origin123",
            "upstream/main": "upstream123",
        }[ref],
    )

    hermes_axiom_update._print_push_recovery_handoff(
        repo=repo,
        branch="axiom",
        worktree=worktree,
        resolved_head=resolved_head,
        error=(
            "To https://secret-token@github.com/Codename-11/hermes-agent.git\n"
            " ! [rejected] HEAD -> axiom (non-fast-forward)\n"
            "error: failed to push some refs\n"
        ),
    )

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["schema"] == 3
    assert payload["phase"] == "push_pending"
    assert payload["resolved_head"] == resolved_head
    assert "non-fast-forward" in payload["error"]
    assert "secret-token" not in payload["error"]
    assert "github.com/Codename-11/hermes-agent.git" in payload["error"]
    out = capsys.readouterr().out
    assert "non-fast-forward" in out
    assert "retry this exact commit without rerunning resolution" in out
    assert f'hermes chat -q "Read {marker}' in out


def test_push_pending_handoff_retries_exact_commit_without_resolver(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    marker = tmp_path / ".update_handoff.json"
    resolved_head = "a" * 40
    marker.write_text(
        json.dumps(
            {
                "schema": 3,
                "repo": str(repo),
                "branch": "axiom",
                "worktree": str(worktree),
                "phase": "push_pending",
                "resolved_head": resolved_head,
            }
        ),
        encoding="utf-8",
    )
    calls = []
    discarded = []
    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_update_resolver_agent",
        lambda *args, **kwargs: pytest.fail("push retry must not rerun resolution"),
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_full_git_ref",
        lambda _git, cwd, ref: resolved_head if cwd == worktree and ref == "HEAD" else "",
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_discard_published_handoff",
        lambda _git, _repo, retained: discarded.append(retained) or marker.unlink() or True,
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("cwd")))
        if cmd in (
            ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"],
            ["git", "fetch", "upstream", "main", "--quiet"],
            ["git", "push", "origin", f"{resolved_head}:axiom"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)

    changed = hermes_axiom_update._resolve_deploy_handoff(
        git_cmd=["git"],
        repo=repo,
        branch="axiom",
        pre_update_head="live123",
        publish_only=True,
    )

    assert changed == 1
    assert (["git", "push", "origin", f"{resolved_head}:axiom"], worktree) in calls
    assert discarded == [worktree]
    assert not marker.exists()
    assert "Published retained commit" in capsys.readouterr().out


def test_push_pending_handoff_stops_when_retained_head_changed(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    marker = tmp_path / ".update_handoff.json"
    validated_head = "a" * 40
    changed_head = "b" * 40
    marker.write_text(
        json.dumps(
            {
                "schema": 3,
                "repo": str(repo),
                "branch": "axiom",
                "worktree": str(worktree),
                "phase": "push_pending",
                "resolved_head": validated_head,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_full_git_ref",
        lambda _git, cwd, ref: changed_head if cwd == worktree and ref == "HEAD" else "",
    )

    def fake_run(cmd, **kwargs):
        if cmd in (
            ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"],
            ["git", "fetch", "upstream", "main", "--quiet"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)

    changed = hermes_axiom_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="axiom", pre_update_head="live123"
    )

    assert changed is None
    assert marker.exists()
    assert "no longer matches its validated commit" in capsys.readouterr().out


def test_discard_published_handoff_does_not_remove_non_temp_path(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update as hermes_axiom_update

    marker = tmp_path / ".update_handoff.json"
    marker.write_text("{}", encoding="utf-8")
    safe_temp = tmp_path / "safe-temp"
    safe_temp.mkdir()
    outside_parent = tmp_path / "outside" / "hermes-update-axiom-forged"
    worktree = outside_parent / "worktree"
    worktree.mkdir(parents=True)

    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(hermes_axiom_update.tempfile, "gettempdir", lambda: str(safe_temp))
    monkeypatch.setattr(
        hermes_axiom_update.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("non-temp worktree must not be removed"),
    )

    hermes_axiom_update._discard_published_handoff(["git"], tmp_path, worktree)

    assert not marker.exists()
    assert outside_parent.exists()


def test_discard_published_handoff_leaves_directory_when_git_remove_fails(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update as hermes_axiom_update

    marker = tmp_path / ".update_handoff.json"
    marker.write_text("{}", encoding="utf-8")
    worktree_parent = tmp_path / "hermes-update-axiom-stale"
    worktree = worktree_parent / "worktree"
    worktree.mkdir(parents=True)

    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(hermes_axiom_update.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        hermes_axiom_update.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="not registered"),
    )

    hermes_axiom_update._discard_published_handoff(["git"], tmp_path, worktree)

    assert not marker.exists()
    assert worktree_parent.exists()


def test_discard_published_handoff_reports_marker_unlink_failure(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update as hermes_axiom_update

    marker = tmp_path / ".update_handoff.json"
    marker.write_text("{}", encoding="utf-8")
    worktree = tmp_path / "hermes-update-axiom-stale" / "worktree"
    worktree.mkdir(parents=True)
    original_unlink = Path.unlink

    def fail_marker_unlink(path, *args, **kwargs):
        if path == marker:
            raise PermissionError("read only")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(Path, "unlink", fail_marker_unlink)
    monkeypatch.setattr(
        hermes_axiom_update.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("worktree cleanup must not run"),
    )

    assert not hermes_axiom_update._discard_published_handoff(
        ["git"], tmp_path, worktree
    )
    assert marker.exists()


def test_published_handoff_stops_if_stale_marker_cannot_be_cleared(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = tmp_path / "hermes-update-axiom-stale" / "worktree"
    worktree.mkdir(parents=True)
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "branch": "axiom",
                "worktree": str(worktree),
                "origin_head": "old-origin",
                "upstream_head": "old-upstream",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "fetch", "origin"] or cmd[:3] == [
            "git",
            "fetch",
            "upstream",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_discard_published_handoff",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_deploy_branch_update",
        lambda *args, **kwargs: pytest.fail("fresh update must not start"),
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_update_resolver_agent",
        lambda *args, **kwargs: pytest.fail("resolver must not start"),
    )

    changed = hermes_axiom_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="axiom", pre_update_head="live-head"
    )

    assert changed is None
    assert "Could not clear stale deploy handoff marker" in capsys.readouterr().out


def test_deploy_handoff_fetch_failure_stops_before_snapshot_classification(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = tmp_path / "hermes-update-axiom-stale" / "worktree"
    worktree.mkdir(parents=True)
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps({"branch": "axiom", "worktree": str(worktree)}),
        encoding="utf-8",
    )

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "fetch", "origin"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="network unavailable")
        if cmd[:3] == ["git", "fetch", "upstream"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_update_resolver_agent",
        lambda *args, **kwargs: pytest.fail("resolver must not start"),
    )

    changed = hermes_axiom_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="axiom", pre_update_head="live-head"
    )

    assert changed is None
    out = capsys.readouterr().out
    assert "Could not refresh deploy refs before resolver classification" in out
    assert "network unavailable" in out


def test_deploy_handoff_resolver_failure_prints_captured_diagnostics(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "schema": 2,
                "repo": str(repo),
                "branch": "axiom",
                "worktree": str(worktree),
                "conflict_files": ["README.md"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_update_resolver_agent",
        lambda prompt, cwd: SimpleNamespace(
            returncode=17,
            stdout="agent setup completed\nfinal resolver context\n",
            stderr="provider invocation failed\n",
        ),
    )

    def fake_run(cmd, **kwargs):
        if cmd in (
            ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"],
            ["git", "fetch", "upstream", "main", "--quiet"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)

    changed = hermes_axiom_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="axiom", pre_update_head="live-head"
    )

    assert changed is None
    out = capsys.readouterr().out
    assert "Resolver exit code: 17" in out
    assert "provider invocation failed" in out
    assert "final resolver context" in out


def test_update_conflict_review_status_prints_plain_progress(capsys):
    from hermes_cli import axiom_update as hermes_axiom_update

    result = getattr(hermes_axiom_update, "_run_conflict_review_status")(
        "review conflict handoff",
        lambda: ("summary", ""),
    )

    assert result == ("summary", "")
    out = capsys.readouterr().out
    assert "review conflict handoff" in out
    assert "handoff ready" in out


def test_focused_pytest_check_is_unavailable_without_pytest(monkeypatch, tmp_path):
    from hermes_cli import axiom_update as hermes_axiom_update

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == [sys.executable, "-c", "import pytest"]:
            return SimpleNamespace(returncode=1)
        pytest.fail("focused pytest command must not run without pytest")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)

    result = hermes_axiom_update._run_focused_check(
        "python -m pytest -q tests/example.py",
        tmp_path,
    )

    assert result is None
    assert calls == [
        (
            [sys.executable, "-c", "import pytest"],
            {"capture_output": True, "text": True},
        )
    ]


def test_update_resolver_agent_uses_visible_noninteractive_chat(monkeypatch, tmp_path):
    from hermes_cli import axiom_update as hermes_axiom_update

    calls = []

    class FakeProcess:
        def __init__(self):
            self.stdout = StringIO("inspecting conflict\nresolved file\n")
            self.returncode = 0
            self.killed = False

        def wait(self, timeout=None):
            assert timeout == 3600
            return self.returncode

        def kill(self):
            self.killed = True

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProcess()

    monkeypatch.setattr(hermes_axiom_update.subprocess, "Popen", fake_popen)

    result = hermes_axiom_update._run_update_resolver_agent("resolve this", tmp_path)

    assert result.returncode == 0
    cmd, kwargs = calls[0]
    assert cmd[:4] == [sys.executable, "-P", "-m", "hermes_cli.main"]
    assert "chat" in cmd
    assert "-z" not in cmd
    assert "-Q" not in cmd
    assert cmd[cmd.index("-q") + 1] == "resolve this"
    assert "terminal,file,search,skills" in cmd
    assert cmd[cmd.index("--source") + 1] == "update-resolver"
    assert "--yolo" in cmd
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"]["HERMES_UPDATE_RESOLVE"] == "1"
    resolver_source = Path(hermes_axiom_update.__file__).resolve().parents[1]
    assert kwargs["env"]["PYTHONPATH"].split(hermes_axiom_update.os.pathsep)[0] == str(
        resolver_source
    )
    assert kwargs["stdin"] is hermes_axiom_update.subprocess.DEVNULL
    assert kwargs["stdout"] is hermes_axiom_update.subprocess.PIPE
    assert kwargs["stderr"] is hermes_axiom_update.subprocess.STDOUT
    assert kwargs["bufsize"] == 1
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert result.stdout == "inspecting conflict\nresolved file\n"


def test_update_resolver_agent_streams_advisory_transcript(monkeypatch, tmp_path, capsys):
    from hermes_cli import axiom_update as hermes_axiom_update

    class FakeStream:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            return iter(["tool: read_file\n", "\n", "resolver summary\n"])

        def close(self):
            self.closed = True

    stream = FakeStream()
    process = SimpleNamespace(
        stdout=stream,
        wait=lambda timeout=None: 0,
        kill=lambda: pytest.fail("successful resolver must not be killed"),
    )
    monkeypatch.setattr(
        hermes_axiom_update.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )

    result = hermes_axiom_update._run_update_resolver_agent("resolve", tmp_path)

    out = capsys.readouterr().out
    assert "  │ tool: read_file" in out
    assert "  │\n" in out
    assert "  │ resolver summary" in out
    assert stream.closed is True
    assert result.returncode == 0
    assert result.stdout.endswith("resolver summary\n")


def test_update_resolver_agent_marks_timeout_unsafe_when_tree_kill_is_unconfirmed(
    monkeypatch, tmp_path
):
    from hermes_cli import axiom_update as hermes_axiom_update

    class TimedOutProcess:
        pid = 4242
        stdout = StringIO("")

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(["resolver"], timeout)
            return -9

    monkeypatch.setattr(
        hermes_axiom_update.subprocess,
        "Popen",
        lambda *args, **kwargs: TimedOutProcess(),
    )
    monkeypatch.setattr(
        hermes_axiom_update,
        "_terminate_resolver_process_tree",
        lambda process: False,
    )

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        hermes_axiom_update._run_update_resolver_agent("resolve", tmp_path)

    assert caught.value.process_tree_terminated is False


def test_timeout_salvage_requires_confirmed_process_tree_termination():
    from hermes_cli import axiom_update as hermes_axiom_update

    unsafe = subprocess.TimeoutExpired(["resolver"], 3600)
    unsafe.process_tree_terminated = False
    safe = subprocess.TimeoutExpired(["resolver"], 3600)
    safe.process_tree_terminated = True

    assert hermes_axiom_update._resolver_timeout_is_safe_to_salvage(unsafe) is False
    assert hermes_axiom_update._resolver_timeout_is_safe_to_salvage(safe) is True


def test_fork_watch_area_pytest_checks_reference_existing_files():
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = Path(__file__).resolve().parents[2]
    missing = []
    for area in hermes_axiom_update.FORK_WATCH_AREAS:
        for check in area["checks"]:
            command = check["command"]
            if "-m pytest" not in command:
                continue
            for token in command.split():
                if token.startswith("tests/"):
                    test_path = token.split("::", 1)[0]
                    if not (repo / test_path).exists():
                        missing.append((area["name"], test_path))

    assert missing == []


def test_fork_watch_catalog_has_typed_unique_ids_and_valid_references():
    repo = Path(__file__).resolve().parents[2]
    area_ids = set()
    check_specs = []

    for area in hermes_axiom_update.FORK_WATCH_AREAS:
        assert set(area) == {
            "id", "name", "paths", "invariants", "prefer_upstream",
            "drop_when", "references", "checks",
        }
        assert area["id"] not in area_ids
        area_ids.add(area["id"])
        assert isinstance(area["paths"], tuple) and area["paths"]
        assert isinstance(area["invariants"], tuple) and area["invariants"]
        assert area["prefer_upstream"] and area["drop_when"]
        for reference in area["references"]:
            path = reference.split("#", 1)[0]
            assert (repo / path).is_file(), reference
        check_specs.extend(area["checks"])

    normalized = hermes_axiom_update._normalize_check_specs(check_specs)
    assert len({check["id"] for check in normalized}) == len(normalized)
    assert all(set(check) == {"id", "kind", "command", "timeout_seconds"} for check in normalized)


def test_check_normalization_supports_legacy_strings_and_rejects_conflicting_ids():
    legacy = "python -m py_compile hermes_cli/axiom_update.py"
    normalized = hermes_axiom_update._normalize_check_specs([legacy, legacy])
    assert len(normalized) == 1
    assert normalized[0]["command"] == legacy
    assert normalized[0]["id"].startswith("legacy-")

    with pytest.raises(ValueError, match="Conflicting check id"):
        hermes_axiom_update._normalize_check_specs([
            {"id": "same", "kind": "python", "command": "python -V", "timeout_seconds": 5},
            {"id": "same", "kind": "python", "command": "python -VV", "timeout_seconds": 5},
        ])


def test_resolver_brief_is_conflict_scoped_deterministic_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes_axiom_update, "_review_reports_dir", lambda: tmp_path)
    review = {
        "branch": "axiom",
        "worktree": "/retained/worktree",
        "conflict_files": ["gateway/platforms/slack.py"],
        "watch_areas": hermes_axiom_update._matched_fork_watch_areas(
            ["gateway/platforms/slack.py"]
        ),
        "incoming_commits": "incoming " + ("x" * 50_000),
        "error": "failure " + ("y" * 50_000),
    }

    first = hermes_axiom_update._render_resolver_brief(review)
    second = hermes_axiom_update._render_resolver_brief(dict(reversed(list(review.items()))))

    assert first == second
    assert len(first) < 16_000
    assert "gateway/platforms/slack.py" in first
    assert "Slack channel/session behavior" in first
    assert "slack-channel-session" in first
    assert "Deploy-branch-safe updater" not in first
    assert "docs/axiom-fork-contract.md" not in first
    assert "Read the full" not in first
    assert "python -m pytest" not in first
    assert "Parent-owned check IDs" in first


def test_windows_focused_check_normalizes_posix_env_assignment():
    from hermes_cli import axiom_update as hermes_axiom_update

    check = "cd apps/desktop && NODE_ENV=test npm run typecheck"

    assert hermes_axiom_update._focused_check_shell_command(check, windows=True) == (
        'cd apps/desktop && set "NODE_ENV=test" && npm run typecheck'
    )


def test_desktop_focused_checks_reference_typescript_sources():
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = Path(__file__).resolve().parents[2]
    desktop_areas = [
        area
        for area in hermes_axiom_update.FORK_WATCH_AREAS
        if str(area["name"]).startswith("Desktop ")
    ]

    assert desktop_areas
    for area in desktop_areas:
        paths = area["paths"]
        checks = area["checks"]
        assert isinstance(paths, tuple)
        assert isinstance(checks, tuple)
        for path in paths:
            if path.endswith((".ts", ".tsx")):
                assert (repo / path).exists(), path
        for check in checks:
            assert ".cjs" not in check["command"], check


def test_slack_focused_checks_reference_existing_files():
    from pathlib import Path

    from hermes_cli import axiom_update as hermes_axiom_update

    repo = Path(__file__).resolve().parents[2]
    checks = hermes_axiom_update._focused_checks_for_paths(
        ["gateway/platforms/slack.py"],
        {},
    )

    assert checks
    for check in checks:
        for token in check["command"].split():
            if token.startswith("tests/") and token.endswith(".py"):
                assert (repo / token).exists(), token


def test_conflict_marker_scan_ignores_decorative_equals_separators(tmp_path):
    from hermes_cli import axiom_update as hermes_axiom_update

    worktree = tmp_path / "worktree"
    jobs = worktree / "cron" / "jobs.py"
    cli = worktree / "cli.py"
    jobs.parent.mkdir(parents=True)
    jobs.write_text(
        "# =============================================================================\n"
        "# Configuration\n"
        "# =============================================================================\n",
        encoding="utf-8",
    )
    cli.write_text(
        "print('ok')\n"
        "# =============================================================================\n",
        encoding="utf-8",
    )

    assert hermes_axiom_update._scan_conflict_markers(
        worktree, ["cron/jobs.py", "cli.py"]
    ) == []

    cli.write_text("<<<<<<< ours\nold\n=======\nnew\n>>>>>>> theirs\n", encoding="utf-8")
    assert hermes_axiom_update._scan_conflict_markers(
        worktree, ["cron/jobs.py", "cli.py"]
    ) == ["cli.py"]


def test_deploy_handoff_resolve_suppresses_child_success_before_validation(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import axiom_update as hermes_axiom_update

    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = tmp_path / "worktree"
    jobs = worktree / "cron" / "jobs.py"
    jobs.parent.mkdir(parents=True)
    jobs.write_text("<<<<<<< ours\nold\n=======\nnew\n>>>>>>> theirs\n", encoding="utf-8")
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "schema": 2,
                "repo": str(repo),
                "branch": "axiom",
                "reason": "merge into axiom failed.",
                "worktree": str(worktree),
                "conflict_files": ["cron/jobs.py"],
                "focused_checks": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(hermes_axiom_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_axiom_update,
        "_run_update_resolver_agent",
        lambda prompt, cwd: SimpleNamespace(
            returncode=0,
            stdout="Ready for the parent updater to validate, commit, push, fast-forward the live checkout.\n",
            stderr="",
        ),
    )

    def fake_run(cmd, **kwargs):
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "upstream", "main", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "diff", "--name-only", "--diff-filter=U"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_axiom_update.subprocess, "run", fake_run)

    changed = hermes_axiom_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="axiom", pre_update_head="oldhead"
    )

    assert changed is None
    out = capsys.readouterr().out
    assert "Ready for the parent updater" not in out
    assert "conflict markers remain" in out
    assert "Resolver left conflict markers in files" in out
    assert "cron/jobs.py" in out


def test_deploy_update_refreshes_stale_origin_ref_before_comparing(monkeypatch, tmp_path):
    """Any host must discover a deploy push even when origin/axiom is stale locally."""
    calls = []
    refreshed = {"origin": False}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "origin",
            "axiom:refs/remotes/origin/axiom",
            "--quiet",
        ]:
            refreshed["origin"] = True
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            # Reproduce the Windows failure: the stale ref looked current until
            # the deploy branch was fetched explicitly.
            count = 12 if refreshed["origin"] else 0
            return SimpleNamespace(stdout=f"{count}\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "fetch", "origin", "axiom:refs/remotes/origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/axiom"]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="12\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "oldhead"
    )

    assert changed == 12
    deploy_fetch = [
        "git",
        "fetch",
        "origin",
        "axiom:refs/remotes/origin/axiom",
        "--quiet",
    ]
    compare = ["git", "rev-list", "--count", "HEAD..origin/axiom"]
    assert calls.index(deploy_fetch) < calls.index(compare)
# ---------------------------------------------------------------------------
# Update uses .[all] with fallback to .
# ---------------------------------------------------------------------------

def _setup_update_mocks(monkeypatch, tmp_path):
    """Common setup for cmd_update tests."""
    from hermes_cli import gateway as hermes_gateway

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(hermes_main, "_stash_local_changes_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(hermes_main, "_restore_stashed_changes", lambda *a, **kw: True)
    monkeypatch.setattr(hermes_config, "get_missing_env_vars", lambda required_only=True: [])
    monkeypatch.setattr(hermes_config, "get_missing_config_fields", lambda: [])
    monkeypatch.setattr(hermes_config, "check_config_version", lambda: (5, 5))
    monkeypatch.setattr(hermes_config, "migrate_config", lambda **kw: {"env_added": [], "config_added": []})
    monkeypatch.setattr(hermes_main, "_upgrade_pip_before_lazy_refresh", lambda *a, **kw: None)
    monkeypatch.setattr(hermes_main, "_refresh_active_lazy_features", lambda *a, **kw: True)
    monkeypatch.setattr(hermes_main, "_capture_active_lazy_features", lambda: [])
    monkeypatch.setattr(hermes_main, "_capture_active_tool_dependencies", lambda: [])
    # cmd_update now snapshots, drains, and rechecks the gateway fleet after the
    # code swap. These dependency/update tests do not own that integration and
    # must never discover or signal the developer's live gateways.
    monkeypatch.setattr(hermes_gateway, "find_gateway_pids", lambda *a, **kw: [])
    monkeypatch.setattr(hermes_gateway, "find_profile_gateway_processes", lambda *a, **kw: [])
    monkeypatch.setattr(hermes_gateway, "_get_service_pids", lambda *a, **kw: set())
    monkeypatch.setattr(hermes_gateway, "supports_systemd_services", lambda: False)
    monkeypatch.setattr(hermes_gateway, "is_macos", lambda: False)


def test_cmd_update_retries_optional_extras_individually_when_all_fails(monkeypatch, tmp_path, capsys):
    """When .[all] fails, update should keep base deps and retry extras individually."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)
    monkeypatch.setattr(hermes_main, "_load_installable_optional_extras", lambda group="all": ["matrix", "mcp"])

    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(cmd)
        plain = _plain_git_cmd(cmd)
        if plain == ["git", "fetch", "origin", "main"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if plain == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="main\n", stderr="", returncode=0)
        if plain == ["git", "rev-list", "HEAD..origin/main", "--count"]:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if plain == ["git", "merge", "--ff-only", "origin/main"]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["/usr/bin/uv", "pip", "install", "-e", ".[all]"]:
            raise CalledProcessError(returncode=1, cmd=cmd)
        if cmd == ["/usr/bin/uv", "pip", "install", "-e", "."]:
            return SimpleNamespace(returncode=0)
        if cmd == ["/usr/bin/uv", "pip", "install", "-e", ".[matrix]"]:
            raise CalledProcessError(returncode=1, cmd=cmd)
        if cmd == ["/usr/bin/uv", "pip", "install", "-e", ".[mcp]"]:
            return SimpleNamespace(returncode=0)
        # Catch-all must include stdout/stderr so consumers that parse
        # output (e.g. the dashboard-restart `ps -A` scan added in the
        # updater) don't crash on AttributeError.
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main.cmd_update(SimpleNamespace())

    install_cmds = [c for c in recorded if "pip" in c and "install" in c]
    assert install_cmds == [
        ["/usr/bin/uv", "pip", "install", "-e", ".[all]"],
        ["/usr/bin/uv", "pip", "install", "-e", "."],
        ["/usr/bin/uv", "pip", "install", "-e", ".[matrix]"],
        ["/usr/bin/uv", "pip", "install", "-e", ".[mcp]"],
    ]

    out = capsys.readouterr().out
    assert "retrying extras individually" in out
    assert "Reinstalled optional extras individually: mcp" in out
    assert "Skipped optional extras that still failed: matrix" in out


def test_cmd_update_succeeds_with_extras(monkeypatch, tmp_path):
    """When .[all] succeeds, no fallback should be attempted."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(cmd)
        plain = _plain_git_cmd(cmd)
        if plain == ["git", "fetch", "origin", "main"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if plain == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="main\n", stderr="", returncode=0)
        if plain == ["git", "rev-list", "HEAD..origin/main", "--count"]:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if plain == ["git", "merge", "--ff-only", "origin/main"]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main.cmd_update(SimpleNamespace())

    install_cmds = [c for c in recorded if "pip" in c and "install" in c]
    assert len(install_cmds) == 1
    assert ".[all]" in install_cmds[0]


def test_refresh_active_memory_provider_dependencies_reinstalls_active_provider(monkeypatch):
    """#53272/#70636: update must re-run the active provider's dep install."""
    recorded = []

    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"memory": {"provider": "mem0"}},
    )
    monkeypatch.setattr(
        "hermes_cli.memory_setup._install_dependencies",
        lambda provider_name, force=False: recorded.append((provider_name, force)),
    )

    hermes_main._refresh_active_memory_provider_dependencies()

    assert recorded == [("mem0", True)]


@pytest.mark.parametrize(
    "memory_cfg",
    [
        {},                                          # no provider configured
        {"provider": ""},                            # empty provider
        {"provider": "default"},                     # built-in store
        {"provider": "mem0", "enabled": False},      # memory disabled
    ],
)
def test_refresh_active_memory_provider_dependencies_skips_inactive(monkeypatch, memory_cfg):
    recorded = []

    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"memory": memory_cfg},
    )
    monkeypatch.setattr(
        "hermes_cli.memory_setup._install_dependencies",
        lambda provider_name, force=False: recorded.append((provider_name, force)),
    )

    hermes_main._refresh_active_memory_provider_dependencies()

    assert recorded == []


def test_refresh_active_memory_provider_dependencies_never_raises(monkeypatch):
    """A provider install failure must not block the rest of the update."""
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"memory": {"provider": "hindsight"}},
    )

    def boom(provider_name, force=False):
        raise RuntimeError("pip exploded")

    monkeypatch.setattr("hermes_cli.memory_setup._install_dependencies", boom)

    hermes_main._refresh_active_memory_provider_dependencies()  # must not raise


def test_cmd_update_refreshes_active_memory_provider_dependencies(monkeypatch, tmp_path):
    """The git-pull update path must invoke the memory-provider refresh."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    refresh_calls = []
    monkeypatch.setattr(
        hermes_main,
        "_refresh_active_memory_provider_dependencies",
        lambda: refresh_calls.append(True),
    )

    def fake_run(cmd, **kwargs):
        plain = ["git"] + cmd[3:] if cmd[:3] == ["git", "-c", "windows.appendAtomically=false"] else cmd
        if plain == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="main\n", stderr="", returncode=0)
        if plain == ["git", "rev-list", "HEAD..origin/main", "--count"]:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if plain == ["git", "merge", "--ff-only", "origin/main"]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main.cmd_update(SimpleNamespace())

    assert refresh_calls == [True]


def test_cmd_update_reloads_runtime_modules_before_lazy_refresh(monkeypatch, tmp_path):
    """Lazy refresh must not see pre-pull modules cached in this process."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    events = []

    def fake_run(cmd, **kwargs):
        plain = ["git"] + cmd[3:] if cmd[:3] == ["git", "-c", "windows.appendAtomically=false"] else cmd
        if plain == ["git", "fetch", "origin", "main"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if plain == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="main\n", stderr="", returncode=0)
        if plain == ["git", "rev-list", "HEAD..origin/main", "--count"]:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if plain == ["git", "merge", "--ff-only", "origin/main"]:
            events.append("pull")
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if "pip" in cmd and "install" in cmd:
            events.append("install")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_reload_runtime_modules():
        events.append("reload")

    def fake_refresh_lazy_features(install_prefix=None, env=None, features=None):
        events.append("lazy-refresh")
        return True

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_main, "_reload_updated_runtime_modules", fake_reload_runtime_modules)
    monkeypatch.setattr(hermes_main, "_refresh_active_lazy_features", fake_refresh_lazy_features)

    hermes_main.cmd_update(SimpleNamespace())

    assert (
        events.index("pull")
        < events.index("install")
        < events.index("reload")
        < events.index("lazy-refresh")
    )


def test_reload_updated_runtime_modules_restores_new_hermes_constants_symbol(monkeypatch):
    """A pre-pull module object missing a new helper is repaired by reload."""
    import hermes_constants

    monkeypatch.delattr(hermes_constants, "apply_subprocess_home_env", raising=False)
    assert not hasattr(hermes_constants, "apply_subprocess_home_env")

    hermes_main._reload_updated_runtime_modules()

    assert callable(hermes_constants.apply_subprocess_home_env)






# ---------------------------------------------------------------------------
# ff-only fallback to reset --hard on diverged history
# ---------------------------------------------------------------------------

def _make_update_side_effect(
    current_branch="main",
    commit_count="3",
    ff_only_fails=False,
    reset_fails=False,
    fetch_fails=False,
    fetch_stderr="",
):
    """Build a subprocess.run side_effect for cmd_update tests."""
    recorded = []

    def side_effect(cmd, **kwargs):
        recorded.append(cmd)
        joined = " ".join(str(c) for c in cmd)
        if "fetch" in joined and "origin" in joined:
            if fetch_fails:
                return SimpleNamespace(stdout="", stderr=fetch_stderr, returncode=128)
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if "rev-parse" in joined and "--abbrev-ref" in joined:
            return SimpleNamespace(stdout=f"{current_branch}\n", stderr="", returncode=0)
        if "checkout" in joined and "main" in joined:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if "rev-list" in joined:
            return SimpleNamespace(stdout=f"{commit_count}\n", stderr="", returncode=0)
        if "--ff-only" in joined:
            if ff_only_fails:
                return SimpleNamespace(
                    stdout="",
                    stderr="fatal: Not possible to fast-forward, aborting.\n",
                    returncode=128,
                )
            return SimpleNamespace(stdout="Updating abc..def\n", stderr="", returncode=0)
        if "reset" in joined and "--hard" in joined:
            if reset_fails:
                return SimpleNamespace(stdout="", stderr="error: unable to write\n", returncode=1)
            return SimpleNamespace(stdout="HEAD is now at abc123\n", stderr="", returncode=0)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return side_effect, recorded


def test_cmd_update_falls_back_to_reset_when_ff_only_fails(monkeypatch, tmp_path, capsys):
    """When --ff-only fails (diverged history), update resets to origin/{branch}."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect(ff_only_fails=True)
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    reset_calls = [c for c in recorded if "reset" in c and "--hard" in c]
    assert len(reset_calls) == 1
    assert _plain_git_cmd(reset_calls[0]) == ["git", "reset", "--hard", "origin/main"]

    out = capsys.readouterr().out
    assert "Fast-forward not possible" in out


def test_cmd_update_no_reset_when_ff_only_succeeds(monkeypatch, tmp_path):
    """When --ff-only succeeds, no reset is attempted."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    reset_calls = [c for c in recorded if "reset" in c and "--hard" in c]
    assert len(reset_calls) == 0


# ---------------------------------------------------------------------------
# Non-main branch → auto-checkout main
# ---------------------------------------------------------------------------

def test_cmd_update_switches_to_main_from_feature_branch(monkeypatch, tmp_path, capsys):
    """When on a feature branch, update checks out main before pulling."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect(current_branch="fix/something")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    checkout_calls = [c for c in recorded if "checkout" in c and "main" in c]
    assert len(checkout_calls) == 1

    out = capsys.readouterr().out
    assert "fix/something" in out
    assert "switching to main" in out


def test_cmd_update_switches_to_main_from_detached_head(monkeypatch, tmp_path, capsys):
    """When in detached HEAD state, update checks out main before pulling."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect(current_branch="HEAD")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    checkout_calls = [c for c in recorded if "checkout" in c and "main" in c]
    assert len(checkout_calls) == 1

    out = capsys.readouterr().out
    assert "detached HEAD" in out


def test_cmd_update_restores_stash_and_branch_when_already_up_to_date(monkeypatch, tmp_path, capsys):
    """When on a feature branch with no updates, stash is restored and branch switched back."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    # Enable stash so it returns a ref
    monkeypatch.setattr(
        hermes_main, "_stash_local_changes_if_needed",
        lambda *a, **kw: "abc123deadbeef",
    )
    restore_calls = []
    monkeypatch.setattr(
        hermes_main, "_restore_stashed_changes",
        lambda *a, **kw: restore_calls.append(1) or True,
    )

    side_effect, recorded = _make_update_side_effect(
        current_branch="fix/something", commit_count="0",
    )
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    # Stash should have been restored
    assert len(restore_calls) == 1

    # Should have checked out back to the original branch
    checkout_back = [c for c in recorded if "checkout" in c and "fix/something" in c]
    assert len(checkout_back) == 1

    out = capsys.readouterr().out
    assert "Already up to date" in out


def test_cmd_update_no_checkout_when_already_on_main(monkeypatch, tmp_path):
    """When already on main, no checkout is needed."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    checkout_calls = [c for c in recorded if "checkout" in c]
    assert len(checkout_calls) == 0


def test_cmd_update_fetch_is_scoped_to_target_branch(monkeypatch, tmp_path):
    """The update fetch must name the target branch. A bare `git fetch origin`
    pulls every ref, and this repo has thousands of auto-generated branches, so
    an unscoped fetch can stall for minutes on a non-single-branch checkout."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    fetch_calls = [_plain_git_cmd(c) for c in recorded if "fetch" in c]
    assert fetch_calls == [["git", "fetch", "origin", "main"]]
    assert ["git", "fetch", "origin"] not in [_plain_git_cmd(c) for c in recorded]


# ---------------------------------------------------------------------------
# Fetch failure — friendly error messages
# ---------------------------------------------------------------------------

def test_cmd_update_network_error_shows_friendly_message(monkeypatch, tmp_path, capsys):
    """Network failures during fetch show a user-friendly message."""
    _setup_update_mocks(monkeypatch, tmp_path)

    side_effect, _ = _make_update_side_effect(
        fetch_fails=True,
        fetch_stderr="fatal: unable to access 'https://...': Could not resolve host: github.com",
    )
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Network error" in out


def test_cmd_update_auth_error_shows_friendly_message(monkeypatch, tmp_path, capsys):
    """Auth failures during fetch show a user-friendly message."""
    _setup_update_mocks(monkeypatch, tmp_path)

    side_effect, _ = _make_update_side_effect(
        fetch_fails=True,
        fetch_stderr="fatal: Authentication failed for 'https://...'",
    )
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Authentication failed" in out


# ---------------------------------------------------------------------------
# reset --hard failure — don't attempt stash restore
# ---------------------------------------------------------------------------

def test_cmd_update_skips_stash_restore_when_reset_fails(monkeypatch, tmp_path, capsys):
    """When reset --hard fails, stash restore is skipped with a helpful message."""
    _setup_update_mocks(monkeypatch, tmp_path)
    # Re-enable stash so it actually returns a ref
    monkeypatch.setattr(
        hermes_main, "_stash_local_changes_if_needed",
        lambda *a, **kw: "abc123deadbeef",
    )
    restore_calls = []
    monkeypatch.setattr(
        hermes_main, "_restore_stashed_changes",
        lambda *a, **kw: restore_calls.append(1) or True,
    )

    side_effect, _ = _make_update_side_effect(ff_only_fails=True, reset_fails=True)
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())

    # Stash restore should NOT have been called
    assert len(restore_calls) == 0

    out = capsys.readouterr().out
    assert "preserved in stash" in out


# ---------------------------------------------------------------------------
# Non-interactive update.non_interactive_local_changes setting
# (chat app / gateway): "discard" throws stashed changes away, "stash"
# (default) restores them. Interactive terminal updates ignore the setting
# and always go through the restore path.
# ---------------------------------------------------------------------------

def _setup_setting_test(monkeypatch, tmp_path, mode):
    """Common wiring: real stash returns a ref, restore + discard are
    recorded, and load_config reports the given non_interactive_local_changes
    mode."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(
        hermes_main, "_stash_local_changes_if_needed",
        lambda *a, **kw: "abc123deadbeef",
    )
    restore_calls = []
    discard_calls = []
    monkeypatch.setattr(
        hermes_main, "_restore_stashed_changes",
        lambda *a, **kw: restore_calls.append(1) or True,
    )
    monkeypatch.setattr(
        hermes_main, "_discard_stashed_changes",
        lambda *a, **kw: discard_calls.append(1) or True,
    )
    monkeypatch.setattr(
        hermes_config, "load_config",
        lambda *a, **kw: {"updates": {"non_interactive_local_changes": mode}},
    )
    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)
    return restore_calls, discard_calls, recorded






def test_bootstrap_marker_not_autostashed_by_update(tmp_path):
    """#38529: the Desktop bootstrap marker must be git-ignored so that
    ``hermes update``'s ``git stash push --include-untracked`` does not sweep it
    into an autostash on every run.

    Behavioral + hermetic: build a throwaway repo that adopts the project's real
    ``.gitignore`` (the contract under test), drop the marker, and confirm the
    same stash invocation the updater uses leaves it untouched.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")

    repo_gitignore = Path(hermes_main.__file__).resolve().parents[1] / ".gitignore"

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / ".gitignore").write_text(repo_gitignore.read_text())
    (tmp_path / "tracked.txt").write_text("x\n")
    git("add", "-A")
    git("commit", "-qm", "init")

    marker = tmp_path / ".hermes-bootstrap-complete"
    marker.write_text("")

    # Exact flags used by hermes update (hermes_cli/main.py).
    git("stash", "push", "--include-untracked", "-m", "hermes-update-autostash")

    assert marker.exists(), (
        ".hermes-bootstrap-complete was swept into the update autostash — it must "
        "be listed in .gitignore so `git stash -u` skips it (#38529)."
    )
    # It must not even register as a dirty/untracked change.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert ".hermes-bootstrap-complete" not in status


# ---------------------------------------------------------------------------
# Permission-denied autostash class: undeletable untracked files (root-owned
# packaging/ etc.) must not abort the update when the stash entry was created.
# ---------------------------------------------------------------------------






def test_update_autostash_survives_undeletable_untracked_dir(tmp_path):
    """Behavioral E2E of the whole permission-denied class with real git:
    root-owned-style undeletable untracked dir → stash succeeds, update-style
    reset works, restore round-trips, nothing lost. (#70127 follow-up)"""
    import os
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")
    if os.name == "nt":
        pytest.skip("POSIX permission semantics")
    if os.geteuid() == 0:
        pytest.skip("root ignores directory write bits")

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=check
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "tracked.txt").write_text("v1\n")
    git("add", "-A")
    git("commit", "-qm", "init")

    (tmp_path / "tracked.txt").write_text("v2 local change\n")
    pkg = tmp_path / "packaging" / "homebrew"
    pkg.mkdir(parents=True)
    (pkg / "hermes-agent.rb").write_text("formula\n")
    os.chmod(pkg, 0o555)  # undeletable contents, like a root-owned dir
    try:
        stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
        assert stash_ref

        # The tracked change is stashed; simulate the updater's checkout window.
        assert (tmp_path / "tracked.txt").read_text() == "v1\n"

        restored = hermes_main._restore_stashed_changes(
            ["git"], tmp_path, stash_ref, prompt_user=False
        )
        assert restored is True
        assert (tmp_path / "tracked.txt").read_text() == "v2 local change\n"
        assert (pkg / "hermes-agent.rb").read_text() == "formula\n"
    finally:
        os.chmod(pkg, 0o755)
