"""Tests for the update check mechanism in hermes_cli.banner."""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest




def test_check_for_updates_uses_cache(tmp_path, monkeypatch):
    """When cache is fresh, check_for_updates should return cached value without calling git."""
    from hermes_cli.banner import check_for_updates
    from hermes_cli import __version__

    # Create a fake git repo and fresh cache
    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    cache_file = tmp_path / ".update_check"
    cache_file.write_text(json.dumps({"schema": 2, "ts": time.time(), "behind": 3, "ver": __version__}))

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.banner.subprocess.run") as mock_run:
        result = check_for_updates()

    assert result == 3
    mock_run.assert_not_called()




def test_check_for_updates_deploy_branch_uses_origin_deploy_baseline(tmp_path, monkeypatch):
    """Deploy branches compare against origin/<branch>, not stale local main."""
    import hermes_cli.banner as banner

    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="https://github.com/Codename-11/hermes-agent.git\n")
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="false\n")
        if cmd == ["git", "fetch", "origin", "--quiet"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "remote", "get-url", "upstream"]:
            return MagicMock(returncode=0, stdout="https://github.com/NousResearch/hermes-agent.git\n")
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return MagicMock(returncode=0, stdout="axiom\n")
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return MagicMock(returncode=0, stdout="2\n")
        if cmd == ["git", "rev-list", "--count", "origin/axiom..upstream/main"]:
            return MagicMock(returncode=0, stdout="3\n")
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            raise AssertionError("deploy branch status must not use local main")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(banner, "__file__", str(repo_dir / "hermes_cli" / "banner.py"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.banner.subprocess.run", side_effect=fake_run):
        assert banner.check_for_updates() == 5

    assert ["git", "rev-list", "--count", "HEAD..origin/axiom"] in calls
    assert ["git", "rev-list", "--count", "origin/axiom..upstream/main"] in calls


def test_prefetch_non_blocking():
    """prefetch_update_check() should return immediately without blocking."""
    import hermes_cli.banner as banner

    # Reset module state
    banner._update_result = None
    banner._update_check_done = threading.Event()

    with patch.object(banner, "check_for_updates", return_value=5):
        start = time.monotonic()
        banner.prefetch_update_check()
        elapsed = time.monotonic() - start

        # Should return almost immediately (well under 1 second)
        assert elapsed < 1.0

        # Wait for the background thread to finish
        banner._update_check_done.wait(timeout=5)
        assert banner._update_result == 5




