"""Tests for the update check mechanism in hermes_cli.banner.

Passive checks go through the GitHub REST API — never ``git fetch``. Every CLI, TUI and desktop
start used to fetch; across the install base that was tens of millions of fetch requests a day
and GitHub asked us to poll the API instead. These tests pin that contract plus the cache
policy that keeps the API traffic to one request a day per install.
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import hermes_cli.banner as banner

SHA_A = "a" * 40
SHA_B = "b" * 40


def test_update_branch_defaults_to_checked_out_deploy_branch(monkeypatch):
    """Bare `hermes update --check` must reach the fork-aware path on axiom."""
    from argparse import Namespace
    import hermes_cli.main as main

    monkeypatch.setattr(main, "_resolve_reconciliation_branch", lambda _args: "axiom")
    assert main._resolve_update_branch(Namespace(branch=None)) == "axiom"

    monkeypatch.setattr(main, "_resolve_reconciliation_branch", lambda _args: "feature/test")
    assert main._resolve_update_branch(Namespace(branch=None)) == "main"
    assert main._resolve_update_branch(Namespace(branch=" tgi ")) == "tgi"


def test_cli_check_deploy_branch_reports_origin_and_upstream_separately(tmp_path, monkeypatch, capsys):
    """The CLI must not collapse a current deploy artifact into 'up to date' while upstream is pending."""
    import hermes_cli.main as main
    import hermes_cli.update_cmd as update_cmd
    import hermes_cli.update_contract as update_contract

    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="false\n", stderr="")
        if cmd == ["git", "remote", "get-url", "upstream"]:
            return MagicMock(returncode=0, stdout="https://github.com/NousResearch/hermes-agent.git\n", stderr="")
        if cmd in (["git", "fetch", "origin", "axiom"], ["git", "fetch", "upstream", "main"]):
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd == ["git", "rev-parse", "--verify", "--quiet", "origin/axiom"]:
            return MagicMock(returncode=0, stdout="origin-sha\n", stderr="")
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return MagicMock(returncode=0, stdout="0\n", stderr="")
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return MagicMock(returncode=0, stdout="3\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(main, "PROJECT_ROOT", repo_dir)
    monkeypatch.setattr(update_contract, "evaluate_update_admission", lambda _root: None)
    monkeypatch.setattr(update_cmd.subprocess, "run", fake_run)

    update_cmd._cmd_update_check("axiom", branch_explicit=False)

    output = capsys.readouterr().out
    assert "Deploy branch: 0 commits from origin/axiom." in output
    assert "Hermes upstream: 3 commits awaiting reconciliation" in output
    assert "Update work is available" in output
    assert "Already up to date" not in output


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A fake checkout the update check resolves to, with git calls stubbed out."""
    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_REVISION", raising=False)
    monkeypatch.setattr(banner, "_resolve_repo_dir", lambda: repo_dir)
    monkeypatch.setattr("hermes_cli.config.detect_install_method", lambda root: "git")
    monkeypatch.setattr("hermes_cli.config.get_project_root", lambda: repo_dir)
    return repo_dir


def _stub_git(monkeypatch, *, head=SHA_A, origin="https://github.com/NousResearch/hermes-agent.git"):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        sub = args[1]
        if sub == "rev-parse":
            return MagicMock(returncode=0, stdout=f"{head}\n")
        if sub == "remote":
            return MagicMock(returncode=0, stdout=f"{origin}\n")
        if sub == "merge-base":
            return MagicMock(returncode=1, stdout="")
        raise AssertionError(f"passive check must not run git {sub}: {args}")

    monkeypatch.setattr(banner.subprocess, "run", fake_run)
    return calls


def test_passive_check_uses_the_api_and_never_fetches(git_repo, monkeypatch):
    """The whole point: no ``git fetch`` / ``ls-remote`` for a GitHub origin, exact count via compare."""
    calls = _stub_git(monkeypatch, head=SHA_A)
    tip = MagicMock(return_value=SHA_B)
    monkeypatch.setattr(banner, "_github_branch_tip", tip)
    monkeypatch.setattr(banner, "_github_compare_behind", lambda cur, tgt: 61)

    assert banner.check_for_updates() == 61
    tip.assert_called_once_with("nousresearch/hermes-agent", "main")
    assert not any(c[1] in {"fetch", "ls-remote"} for c in calls)

    cached = json.loads((git_repo.parent / ".update_check").read_text())
    assert (cached["head"], cached["target"], cached["behind"]) == (SHA_A, SHA_B, 61)


def test_cache_is_daily_but_invalidated_when_head_moves(git_repo, monkeypatch):
    """A fresh cache answers without any network; ``hermes update`` moving HEAD busts it at once;
    an inconclusive (None) result is retried after the shorter failure window, not never."""
    from hermes_cli import __version__

    cache_file = git_repo.parent / ".update_check"
    _stub_git(monkeypatch, head=SHA_A)
    tip = MagicMock(return_value=None)
    monkeypatch.setattr(banner, "_github_branch_tip", tip)

    def write_cache(*, ts, head, behind):
        cache_file.write_text(json.dumps(
            {"ts": ts, "behind": behind, "rev": None, "ver": __version__, "head": head}))

    write_cache(ts=time.time() - banner._UPDATE_CHECK_CACHE_SECONDS + 60, head=SHA_A, behind=3)
    assert banner.check_for_updates() == 3
    tip.assert_not_called()

    write_cache(ts=time.time(), head=SHA_B, behind=3)  # cached for a different HEAD
    assert banner.check_for_updates() is None  # API unreachable → inconclusive, re-asked
    tip.assert_called_once()

    tip.reset_mock()
    write_cache(ts=time.time() - banner._UPDATE_CHECK_FAILURE_CACHE_SECONDS + 60, head=SHA_A, behind=None)
    assert banner.check_for_updates() is None
    tip.assert_not_called()

    write_cache(ts=time.time() - banner._UPDATE_CHECK_FAILURE_CACHE_SECONDS - 1, head=SHA_A, behind=None)
    banner.check_for_updates()
    tip.assert_called_once()


def test_prefetch_non_blocking():
    """prefetch_update_check() should return immediately without blocking."""
    banner._update_result = None
    banner._update_check_done = threading.Event()

    with patch.object(banner, "check_for_updates", return_value=5):
        start = time.monotonic()
        banner.prefetch_update_check()
        assert time.monotonic() - start < 1.0
        banner._update_check_done.wait(timeout=5)
        assert banner._update_result == 5


def test_upstream_main_sha_ls_remote_fallback_disables_git_prompts(monkeypatch):
    """When the API is unreachable the HTTPS ls-remote fallback must never inherit the terminal."""
    monkeypatch.setattr(banner, "_github_branch_tip", lambda slug, branch: None)
    completed = MagicMock(returncode=1, stdout="", stderr="auth required")
    run = MagicMock(return_value=completed)
    monkeypatch.setattr(banner.subprocess, "run", run)

    assert banner._upstream_main_sha() is None
    kwargs = run.call_args.kwargs
    assert kwargs["stdin"] is banner.subprocess.DEVNULL
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GCM_INTERACTIVE"] == "Never"
