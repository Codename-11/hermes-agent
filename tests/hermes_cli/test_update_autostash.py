import json
import sys
from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import config as hermes_config
from hermes_cli import main as hermes_main


def _plain_git_cmd(cmd):
    if cmd[:3] == ["git", "-c", "windows.appendAtomically=false"]:
        return ["git", *cmd[3:]]
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

    def _fake_rebuild_venv(*args, **kwargs):
        return True  # no-op in tests

    with patch("hermes_cli.managed_uv.resolve_uv", side_effect=_fake_resolve_uv), \
         patch("hermes_cli.managed_uv.ensure_uv", side_effect=_fake_ensure_uv), \
         patch("hermes_cli.managed_uv.update_managed_uv", side_effect=_fake_update_managed_uv), \
         patch("hermes_cli.managed_uv.rebuild_venv", side_effect=_fake_rebuild_venv):
        yield


@pytest.fixture(autouse=True)
def _isolate_gateway_discovery():
    """Keep updater tests from discovering or signaling live gateways."""
    with patch("hermes_cli.gateway.find_gateway_pids", return_value=[]), \
         patch("hermes_cli.gateway.supports_systemd_services", return_value=False), \
         patch("hermes_cli.gateway.find_profile_gateway_processes", return_value=[]):
        yield













# ---------------------------------------------------------------------------
# Update uses .[all] with fallback to .
# ---------------------------------------------------------------------------

def test_cmd_update_fetch_is_scoped_to_target_branch(monkeypatch, tmp_path):
    """The update fetch must name the target branch. A bare `git fetch origin`
    pulls every ref, and this repo has thousands of auto-generated branches, so
    an unscoped fetch can stall for minutes on a non-single-branch checkout."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    fetch_calls = [c for c in recorded if "fetch" in c]
    assert fetch_calls == [["git", "fetch", "origin", "main"]]
    assert ["git", "fetch", "origin"] not in recorded


def test_tgi_is_registered_as_deploy_branch():
    assert "tgi" in hermes_main.DEPLOY_BRANCHES


def test_deploy_branch_update_fast_forwards_when_origin_ahead(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[:3] == ["git", "fetch", "origin"] and cmd[-1] == "--quiet":
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
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return SimpleNamespace(stdout="false\n", stderr="", returncode=0)
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return SimpleNamespace(stdout="abc123\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    assert hermes_main._sync_deploy_main_to_upstream(["git"], tmp_path) is False
    assert ["git", "branch", "-f", "main", "upstream/main"] not in calls
    out = capsys.readouterr().out
    assert "local main has commits that are not on upstream/main" in out


def test_sync_deploy_main_to_upstream_deepens_shallow_history_before_refusing(
    monkeypatch, tmp_path
):
    calls = []
    local_counts = iter((691, 0))
    behind_counts = iter((1, 2))

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout=f"{next(local_counts)}\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout=f"{next(behind_counts)}\n", stderr="", returncode=0)
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return SimpleNamespace(stdout="true\n", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "--deepen=1024",
            "upstream",
            "main:refs/remotes/upstream/main",
        ]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "branch", "-f", "main", "upstream/main"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    assert hermes_main._sync_deploy_main_to_upstream(["git"], tmp_path) is True
    assert [
        "git",
        "fetch",
        "--deepen=1024",
        "upstream",
        "main:refs/remotes/upstream/main",
    ] in calls
    assert ["git", "branch", "-f", "main", "upstream/main"] in calls


def test_sync_deploy_main_to_upstream_stops_if_deepened_counts_fail(
    monkeypatch, tmp_path
):
    calls = []
    local_counts = iter((691, -1))
    behind_counts = iter((1, -1))

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            value = next(local_counts)
            return SimpleNamespace(
                stdout=f"{value}\n" if value >= 0 else "",
                stderr="comparison failed" if value < 0 else "",
                returncode=0 if value >= 0 else 1,
            )
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            value = next(behind_counts)
            return SimpleNamespace(
                stdout=f"{value}\n" if value >= 0 else "",
                stderr="comparison failed" if value < 0 else "",
                returncode=0 if value >= 0 else 1,
            )
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return SimpleNamespace(stdout="true\n", stderr="", returncode=0)
        if cmd == [
            "git",
            "fetch",
            "--deepen=1024",
            "upstream",
            "main:refs/remotes/upstream/main",
        ]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    assert hermes_main._sync_deploy_main_to_upstream(["git"], tmp_path) is False
    assert ["git", "branch", "-f", "main", "upstream/main"] not in calls


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
        if cmd[:3] == ["git", "fetch", "origin"] and cmd[-1] == "--quiet":
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
    # hermes_cli.fork_update (extracted from main.py to shrink the fork's
    # footprint in upstream's most-refactored file). Patch the dependencies on
    # the module where the function actually resolves them, not only on
    # hermes_cli.main's imported aliases.
    from hermes_cli import fork_update as hermes_fork_update

    monkeypatch.setattr(hermes_fork_update, "_deploy_handoff_marker_path", lambda: marker)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/axiom"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/axiom..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "merge-base", "--is-ancestor", "upstream/main", "origin/axiom"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_fork_update.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    assert hermes_fork_update._completed_deploy_handoff_requires_post_update(
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
        if cmd[:3] == ["git", "fetch", "origin"] and cmd[-1] == "--quiet":
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


def test_tgi_first_host_publishes_second_host_consumes_real_git(tmp_path):
    """Bare update reconciles once; a later host consumes the published result."""
    import subprocess

    from hermes_cli import fork_update as hermes_fork_update

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

    git(upstream_work, "checkout", "-b", "tgi")
    (upstream_work / "fork.txt").write_text("tgi\n", encoding="utf-8")
    git(upstream_work, "add", "fork.txt")
    git(upstream_work, "commit", "-m", "tgi carry")
    git(upstream_work, "push", "fork", "tgi")

    for host in (host_a, host_b):
        git(tmp_path, "clone", "--branch", "tgi", str(origin_bare), str(host))
        git(host, "config", "user.name", "Hermes Test")
        git(host, "config", "user.email", "hermes-test@example.invalid")
        git(host, "remote", "add", "upstream", str(upstream_bare))
        git(host, "fetch", "upstream", "main")
        git(host, "branch", "main", "upstream/main")

    host_b_before = git(host_b, "rev-parse", "HEAD", capture=True).stdout.strip()

    git(upstream_work, "checkout", "main")
    (upstream_work / "shared.txt").write_text(
        "base\nupstream\n", encoding="utf-8"
    )
    git(upstream_work, "add", "shared.txt")
    git(upstream_work, "commit", "-m", "upstream feature")
    git(upstream_work, "push", "upstream-bare", "main")

    host_a_before = git(host_a, "rev-parse", "HEAD", capture=True).stdout.strip()
    changed_a = hermes_fork_update._run_deploy_branch_update(
        ["git"], host_a, "tgi", host_a_before
    )
    published = git(host_a, "rev-parse", "HEAD", capture=True).stdout.strip()
    origin_after_a = git(
        origin_bare, "rev-parse", "refs/heads/tgi", capture=True
    ).stdout.strip()

    assert changed_a and changed_a > 0
    assert published == origin_after_a
    git(host_a, "merge-base", "--is-ancestor", "upstream/main", "HEAD")

    changed_b = hermes_fork_update._run_deploy_branch_update(
        ["git"], host_b, "tgi", host_b_before
    )
    host_b_after = git(host_b, "rev-parse", "HEAD", capture=True).stdout.strip()
    origin_after_b = git(
        origin_bare, "rev-parse", "refs/heads/tgi", capture=True
    ).stdout.strip()

    assert changed_b and changed_b > 0
    assert host_b_after == published
    assert origin_after_b == origin_after_a


def test_deploy_branch_update_conflict_prints_handoff_and_starts_resolver(
    monkeypatch, tmp_path, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "update-parent"
    parent.mkdir()
    worktree_path = parent / "worktree"
    calls = []

    from hermes_cli import fork_update as hermes_fork_update

    monkeypatch.setattr(hermes_main.tempfile, "mkdtemp", lambda prefix: str(parent))
    monkeypatch.setattr(hermes_fork_update, "_review_reports_dir", lambda: tmp_path / "reports")
    monkeypatch.setattr(
        hermes_fork_update,
        "_call_llm_update_review",
        lambda review: ("LLM says: pause, resolve in worktree, run focused tests.", ""),
    )
    resolver_calls = []
    monkeypatch.setattr(
        hermes_fork_update,
        "_resolve_deploy_handoff",
        lambda **kwargs: resolver_calls.append(kwargs) or None,
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[:3] == ["git", "fetch", "origin"] and cmd[-1] == "--quiet":
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


# ---------------------------------------------------------------------------
# Update uses .[all] with fallback to .
# ---------------------------------------------------------------------------

def _setup_update_mocks(monkeypatch, tmp_path):
    """Common setup for cmd_update tests."""
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
        cmd = _plain_git_cmd(cmd)
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


# ---------------------------------------------------------------------------
# Non-main branch → auto-checkout main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fetch failure — friendly error messages
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# --keep-stash (desktop updater): stash for the update, never re-apply.
# ---------------------------------------------------------------------------

def _setup_keep_stash_test(monkeypatch, tmp_path):
    """Wiring for --keep-stash tests: stash returns a ref; restore, discard,
    and park are all recorded."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(
        hermes_main, "_stash_local_changes_if_needed",
        lambda *a, **kw: "abc123deadbeef",
    )
    restore_calls = []
    discard_calls = []
    park_calls = []
    monkeypatch.setattr(
        hermes_main, "_restore_stashed_changes",
        lambda *a, **kw: restore_calls.append(1) or True,
    )
    monkeypatch.setattr(
        hermes_main, "_discard_stashed_changes",
        lambda *a, **kw: discard_calls.append(1) or True,
    )
    monkeypatch.setattr(
        hermes_main, "_park_stashed_changes",
        lambda *a, **kw: park_calls.append(a) or None,
    )
    # Keep the update flow away from the real gateway fleet on this machine —
    # a live gateway PID would trip the test-suite kill guard and turn the
    # run into exit 1 (gateway_fleet_restart_incomplete).
    monkeypatch.setattr(
        "hermes_cli.gateway.find_gateway_pids", lambda **kw: [], raising=False
    )
    return restore_calls, discard_calls, park_calls


def test_update_keep_stash_parks_instead_of_restoring(monkeypatch, tmp_path):
    """--keep-stash: after a successful update, the autostash is parked (left
    in git stash) — never re-applied, never discarded."""
    restore_calls, discard_calls, park_calls = _setup_keep_stash_test(monkeypatch, tmp_path)
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace(yes=True, keep_stash=True))

    assert len(park_calls) == 1
    assert park_calls[0][0] == "abc123deadbeef"
    assert restore_calls == []
    assert discard_calls == []


def test_update_without_keep_stash_still_restores(monkeypatch, tmp_path):
    """Regression guard: default behavior (no --keep-stash) is unchanged —
    the autostash is auto-restored under --yes."""
    restore_calls, discard_calls, park_calls = _setup_keep_stash_test(monkeypatch, tmp_path)
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace(yes=True, keep_stash=False))

    assert restore_calls == [1]
    assert park_calls == []
    assert discard_calls == []


def test_update_keep_stash_failure_path_still_preserves(monkeypatch, tmp_path, capsys):
    """--keep-stash + failed update: neither restore nor park runs; the
    existing preserved-in-stash message fires (working tree unknown)."""
    restore_calls, discard_calls, park_calls = _setup_keep_stash_test(monkeypatch, tmp_path)
    side_effect, _ = _make_update_side_effect(ff_only_fails=True, reset_fails=True)
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace(yes=True, keep_stash=True))

    assert restore_calls == []
    assert park_calls == []
    assert discard_calls == []
    assert "preserved in stash" in capsys.readouterr().out


def test_update_parser_accepts_keep_stash():
    """The flag parses and defaults off."""
    import argparse

    from hermes_cli.subcommands.update import build_update_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    build_update_parser(subparsers, cmd_update=lambda args: None)

    args = parser.parse_args(["update", "--keep-stash"])
    assert args.keep_stash is True
    args = parser.parse_args(["update"])
    assert args.keep_stash is False






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


def test_tgi_deploy_branch_update_retries_push_after_merging_remote_advanced_origin(
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
        if cmd[:3] == ["git", "fetch", "origin"] and cmd[-1] == "--quiet":
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "upstream/main..main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "main..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/tgi"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/tgi..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/tgi..upstream/main"]:
            return SimpleNamespace(stdout="3\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "add", "--detach", str(worktree_path), "origin/tgi"]:
            worktree_path.mkdir()
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--no-edit", "upstream/main"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge upstream\n", stderr="", returncode=0)
        if cmd == ["git", "push", "origin", "HEAD:tgi"] and cwd == worktree_path:
            if not first_push["done"]:
                first_push["done"] = True
                return SimpleNamespace(stdout="", stderr="remote advanced\n", returncode=1)
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "origin", "tgi:refs/remotes/origin/tgi"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge-base", "--is-ancestor", "HEAD", "origin/tgi"] and cwd == worktree_path:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if cmd == ["git", "merge-base", "--is-ancestor", "HEAD", "origin/tgi"] and cwd == repo:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if cmd == ["git", "merge-base", "--is-ancestor", "upstream/main", "origin/tgi"] and cwd == repo:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if cmd == ["git", "merge", "--no-edit", "origin/tgi"] and cwd == worktree_path:
            return SimpleNamespace(stdout="Merge origin\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/tgi"] and cwd == repo:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree_path), "--force"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="4\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(["git"], repo, "tgi", "oldhead")

    assert changed == 4
    assert not parent.exists()
    commands = [cmd for cmd, _ in calls]
    assert commands.count(["git", "push", "origin", "HEAD:tgi"]) == 2
    assert ["git", "merge", "--no-edit", "origin/tgi"] in commands
    assert ["git", "merge", "--ff-only", "origin/tgi"] in commands
    out = capsys.readouterr().out
    assert "Reconciled remote-advanced origin/tgi and pushed retry merge" in out
    assert "hermes update: push to origin/tgi failed" not in out


def test_tgi_deploy_handoff_resolve_runs_agent_pushes_and_fast_forwards(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import fork_update as hermes_fork_update

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
                "branch": "tgi",
                "reason": "merge into tgi failed.",
                "worktree": str(worktree),
                "conflict_files": ["README.md"],
                "focused_checks": [],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    monkeypatch.setattr(hermes_fork_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_fork_update,
        "_run_update_resolver_agent",
        lambda prompt, cwd: SimpleNamespace(returncode=0),
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "origin", "tgi:refs/remotes/origin/tgi"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "upstream", "main", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "diff", "--name-only", "--diff-filter=U"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "add", "-A"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "diff", "--cached", "--quiet"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if cmd == ["git", "rev-parse", "--git-path", "MERGE_HEAD"] and cwd == worktree:
            return SimpleNamespace(stdout="MERGE_HEAD\n", stderr="", returncode=0)
        if cmd == ["git", "commit", "--no-edit"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "push", "origin", "HEAD:tgi"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/tgi"] and cwd == repo:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"] and cwd == repo:
            return SimpleNamespace(stdout="2\n", stderr="", returncode=0)
        if cmd == ["git", "worktree", "remove", str(worktree), "--force"] and cwd == repo:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_fork_update.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_fork_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="tgi", pre_update_head="oldhead"
    )

    assert changed == 2
    assert not marker.exists()
    commands = [cmd for cmd, _ in calls]
    assert ["git", "commit", "--no-edit"] in commands
    assert ["git", "push", "origin", "HEAD:tgi"] in commands
    assert ["git", "merge", "--ff-only", "origin/tgi"] in commands
    out = capsys.readouterr().out
    assert "prepare resolve" in out
    assert "agent resolve" in out
    assert "sync live" in out
    assert "resolved handoff" in out
    assert "Resolved deploy handoff" in out
    assert "\r" not in out
    assert not any(frame in out for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


def test_tgi_update_conflict_review_status_prints_scrollback_safe_progress(capsys):
    from hermes_cli import fork_update as hermes_fork_update

    result = hermes_fork_update._run_conflict_review_status(
        "review conflict handoff",
        lambda: ("summary", ""),
    )

    assert result == ("summary", "")
    out = capsys.readouterr().out
    assert "review conflict handoff" in out
    assert "handoff ready" in out
    assert "\r" not in out
    assert not any(frame in out for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


def test_tgi_update_resolver_agent_uses_oneshot_not_chat(monkeypatch, tmp_path):
    from hermes_cli import fork_update as hermes_fork_update

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(hermes_fork_update.subprocess, "run", fake_run)

    result = hermes_fork_update._run_update_resolver_agent("resolve this", tmp_path)

    assert result.returncode == 0
    cmd, kwargs = calls[0]
    assert cmd[:2] == [hermes_fork_update.sys.executable, "-c"]
    assert "from hermes_cli.main import main" in cmd[2]
    assert "-z" in cmd
    assert "chat" not in cmd
    assert "terminal,file,search,skills" in cmd
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"]["HERMES_UPDATE_RESOLVE"] == "1"
    assert kwargs["capture_output"] is True


def test_tgi_resolver_bootstrap_avoids_conflicted_worktree_main(tmp_path):
    import subprocess

    from hermes_cli import fork_update as hermes_fork_update

    conflicted_package = tmp_path / "hermes_cli"
    conflicted_package.mkdir()
    (conflicted_package / "__init__.py").write_text("", encoding="utf-8")
    (conflicted_package / "main.py").write_text(
        "<<<<<<< ours\ninvalid python\n=======\nstill invalid\n>>>>>>> theirs\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            hermes_fork_update.sys.executable,
            "-c",
            getattr(hermes_fork_update, "_resolver_cli_bootstrap")(tmp_path),
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "Hermes Agent" in result.stdout or "usage:" in result.stdout
    assert "SyntaxError" not in result.stderr


@pytest.mark.skipif(
    sys.platform == "win32", reason="Symlinks require elevated privileges on Windows"
)
def test_tgi_update_focused_check_env_keeps_virtualenv_symlink(
    monkeypatch, tmp_path
):
    from hermes_cli import fork_update as hermes_fork_update

    base_python = tmp_path / "uv" / "python"
    base_python.parent.mkdir()
    base_python.write_text("", encoding="utf-8")
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)
    monkeypatch.setattr(
        hermes_fork_update.sys,
        "executable",
        str(venv_python),
    )
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")

    env = hermes_fork_update._focused_check_env()

    assert env["PATH"].split(hermes_fork_update.os.pathsep) == [
        str(venv_python.parent),
        "/usr/local/bin",
        "/usr/bin",
    ]


@pytest.mark.skipif(
    sys.platform == "win32", reason="Symlinks require elevated privileges on Windows"
)
def test_tgi_focused_node_checks_reuse_live_dependencies(monkeypatch, tmp_path):
    from hermes_cli import fork_update as hermes_fork_update

    live_root = tmp_path / "live"
    live_modules = live_root / "node_modules"
    live_modules.mkdir(parents=True)
    live_desktop_modules = live_root / "apps" / "desktop" / "node_modules"
    live_desktop_modules.mkdir(parents=True)
    worktree = tmp_path / "worktree"
    (worktree / "apps" / "desktop").mkdir(parents=True)
    monkeypatch.setattr(hermes_fork_update, "__file__", str(live_root / "hermes_cli" / "fork_update.py"))

    with hermes_fork_update._focused_node_modules(worktree, ["cd apps/desktop && npx vitest run"]):
        link = worktree / "node_modules"
        assert link.is_symlink()
        assert link.resolve() == live_modules.resolve()
        desktop_link = worktree / "apps" / "desktop" / "node_modules"
        assert desktop_link.is_symlink()
        assert desktop_link.resolve() == live_desktop_modules.resolve()

    assert not (worktree / "node_modules").exists()
    assert not (worktree / "apps" / "desktop" / "node_modules").exists()


def test_tgi_focused_checks_install_declared_pytest_tooling_when_missing(monkeypatch):
    from hermes_cli import fork_update as hermes_fork_update

    calls = []
    responses = iter(
        [
            SimpleNamespace(returncode=1),
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0),
        ]
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return next(responses)

    monkeypatch.setattr(hermes_fork_update.sys, "executable", "/opt/hermes/venv/bin/python")
    monkeypatch.setattr(hermes_fork_update.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_fork_update.shutil, "which", lambda *args, **kwargs: "/usr/bin/uv")
    monkeypatch.setattr(
        hermes_fork_update,
        "_focused_pytest_requirements",
        lambda: ["pytest==9.0.2", "pytest-asyncio==1.3.0"],
    )

    ready = hermes_fork_update._ensure_focused_pytest(
        ["python -m pytest -q tests/hermes_cli/test_cmd_update.py"],
        {"PATH": "/opt/hermes/venv/bin:/usr/bin"},
    )

    assert ready is True
    assert calls[0][0] == [
        "/opt/hermes/venv/bin/python",
        "-c",
        "import pytest, pytest_asyncio",
    ]
    assert calls[1][0] == [
        "/usr/bin/uv",
        "pip",
        "install",
        "--python",
        "/opt/hermes/venv/bin/python",
        "pytest==9.0.2",
        "pytest-asyncio==1.3.0",
    ]
    assert calls[2][0] == calls[0][0]


def test_tgi_focused_checks_skip_pytest_bootstrap_for_non_pytest_checks(monkeypatch):
    from hermes_cli import fork_update as hermes_fork_update

    monkeypatch.setattr(
        hermes_fork_update.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("non-pytest checks must not probe tooling"),
    )

    assert hermes_fork_update._ensure_focused_pytest(
        ["python -m py_compile hermes_cli/main.py"],
        {"PATH": "/usr/bin"},
    )


def test_tgi_update_focused_checks_replace_stale_marker_snapshot():
    from hermes_cli import fork_update as hermes_fork_update

    checks = hermes_fork_update._focused_checks_for_paths(
        ["apps/desktop/electron/main.ts"],
        {
            "focused_checks": [
                "cd apps/desktop && node --check electron/main.cjs electron/preload.cjs"
            ]
        },
    )

    assert checks
    assert all(".cjs" not in check for check in checks)
    assert any("npm run typecheck" in check for check in checks)


def test_tgi_update_focused_checks_keep_custom_marker_fallback():
    from hermes_cli import fork_update as hermes_fork_update

    checks = hermes_fork_update._focused_checks_for_paths(
        ["custom/integration.txt"],
        {"focused_checks": ["./scripts/check-custom-integration"]},
    )

    assert checks == ["./scripts/check-custom-integration"]


def test_tgi_published_handoff_snapshot_is_discarded_before_agent_resolve(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import fork_update as hermes_fork_update

    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "hermes-update-tgi-published"
    worktree = parent / "worktree"
    worktree.mkdir(parents=True)
    marker = tmp_path / ".update_handoff.json"
    payload = {
        "schema": 2,
        "repo": str(repo),
        "branch": "tgi",
        "worktree": str(worktree),
        "conflict_files": ["README.md"],
        "origin_head": "old-origin",
        "upstream_head": "old-upstream",
    }
    marker.write_text(json.dumps(payload), encoding="utf-8")
    calls = []
    rebuilt = []

    monkeypatch.setattr(
        hermes_fork_update, "_deploy_handoff_marker_path", lambda: marker
    )
    monkeypatch.setattr(
        hermes_fork_update,
        "_run_update_resolver_agent",
        lambda *args, **kwargs: pytest.fail(
            "a published handoff must not launch the resolver"
        ),
    )
    monkeypatch.setattr(
        hermes_fork_update,
        "_run_deploy_branch_update",
        lambda git_cmd, cwd, branch, pre_update_head: rebuilt.append(
            (git_cmd, cwd, branch, pre_update_head)
        )
        or 7,
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd in (
            ["git", "fetch", "origin", "tgi:refs/remotes/origin/tgi"],
            ["git", "fetch", "upstream", "main", "--quiet"],
            ["git", "merge-base", "--is-ancestor", "old-origin", "origin/tgi"],
            ["git", "merge-base", "--is-ancestor", "old-upstream", "origin/tgi"],
            ["git", "worktree", "remove", str(worktree), "--force"],
        ):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_fork_update.subprocess, "run", fake_run)

    result = hermes_fork_update._resolve_deploy_handoff(
        git_cmd=["git"],
        repo=repo,
        branch="tgi",
        pre_update_head="live-head",
    )

    assert result == 7
    assert not marker.exists()
    assert rebuilt == [(["git"], repo, "tgi", "live-head")]
    assert ["git", "worktree", "remove", str(worktree), "--force"] in calls
    out = capsys.readouterr().out
    assert "already published" in out
    assert "starting a fresh deploy update" in out


def test_tgi_handoff_with_superseded_origin_base_rebuilds_once(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import fork_update as hermes_fork_update

    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "hermes-update-tgi-superseded"
    worktree = parent / "worktree"
    worktree.mkdir(parents=True)
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "repo": str(repo),
                "branch": "tgi",
                "worktree": str(worktree),
                "origin_head": "old-origin",
                "upstream_head": "pending-upstream",
            }
        ),
        encoding="utf-8",
    )
    rebuilt = []

    monkeypatch.setattr(
        hermes_fork_update, "_deploy_handoff_marker_path", lambda: marker
    )
    monkeypatch.setattr(
        hermes_fork_update,
        "_run_update_resolver_agent",
        lambda *args, **kwargs: pytest.fail(
            "a superseded-base handoff must rebuild before resolving"
        ),
    )
    monkeypatch.setattr(
        hermes_fork_update,
        "_run_deploy_branch_update",
        lambda git_cmd, cwd, branch, pre_update_head: rebuilt.append(
            (git_cmd, cwd, branch, pre_update_head)
        )
        or 9,
    )

    def fake_run(cmd, **kwargs):
        if cmd in (
            ["git", "fetch", "origin", "tgi:refs/remotes/origin/tgi"],
            ["git", "fetch", "upstream", "main", "--quiet"],
            ["git", "merge-base", "--is-ancestor", "old-origin", "origin/tgi"],
            ["git", "worktree", "remove", str(worktree), "--force"],
        ):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd in (
            [
                "git",
                "merge-base",
                "--is-ancestor",
                "pending-upstream",
                "origin/tgi",
            ],
            ["git", "merge-base", "--is-ancestor", "origin/tgi", "old-origin"],
        ):
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_fork_update.subprocess, "run", fake_run)

    result = hermes_fork_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="tgi", pre_update_head="live-head"
    )

    assert result == 9
    assert not marker.exists()
    assert rebuilt == [(["git"], repo, "tgi", "live-head")]
    out = capsys.readouterr().out
    assert "advanced after this handoff was created" in out
    assert "rebuilding once" in out


def test_tgi_handoff_with_missing_worktree_is_discarded_and_rebuilt(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import fork_update as hermes_fork_update

    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / ".update_handoff.json"
    marker.write_text(
        json.dumps(
            {
                "schema": 2,
                "repo": str(repo),
                "branch": "tgi",
                "worktree": "",
                "origin_head": "old-origin",
                "upstream_head": "old-upstream",
            }
        ),
        encoding="utf-8",
    )
    rebuilt = []

    monkeypatch.setattr(
        hermes_fork_update, "_deploy_handoff_marker_path", lambda: marker
    )
    monkeypatch.setattr(
        hermes_fork_update,
        "_run_deploy_branch_update",
        lambda git_cmd, cwd, branch, pre_update_head: rebuilt.append(
            (git_cmd, cwd, branch, pre_update_head)
        )
        or 11,
    )

    def fake_run(cmd, **kwargs):
        if cmd in (
            ["git", "fetch", "origin", "tgi:refs/remotes/origin/tgi"],
            ["git", "fetch", "upstream", "main", "--quiet"],
        ):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_fork_update.subprocess, "run", fake_run)

    result = hermes_fork_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="tgi", pre_update_head="live-head"
    )

    assert result == 11
    assert not marker.exists()
    assert rebuilt == [(["git"], repo, "tgi", "live-head")]
    out = capsys.readouterr().out
    assert "worktree is missing" in out
    assert "rebuilding once from current refs" in out


def test_deploy_handoff_without_worktree_is_not_recorded(monkeypatch, tmp_path):
    from hermes_cli import fork_update as hermes_fork_update

    marker = tmp_path / ".update_handoff.json"
    monkeypatch.setattr(
        hermes_fork_update, "_deploy_handoff_marker_path", lambda: marker
    )

    hermes_fork_update._record_deploy_handoff(
        repo=tmp_path,
        branch="tgi",
        reason="local main cannot be synchronized with upstream/main.",
    )

    assert not marker.exists()


def test_conflict_marker_scan_ignores_decorative_equals_separators(tmp_path):
    from hermes_cli import fork_update as hermes_fork_update

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

    assert hermes_fork_update._scan_conflict_markers(
        worktree, ["cron/jobs.py", "cli.py"]
    ) == []

    cli.write_text("<<<<<<< ours\nold\n=======\nnew\n>>>>>>> theirs\n", encoding="utf-8")
    assert hermes_fork_update._scan_conflict_markers(
        worktree, ["cron/jobs.py", "cli.py"]
    ) == ["cli.py"]


def test_tgi_deploy_handoff_resolve_suppresses_child_success_before_validation(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import fork_update as hermes_fork_update

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
                "branch": "tgi",
                "reason": "merge into tgi failed.",
                "worktree": str(worktree),
                "conflict_files": ["cron/jobs.py"],
                "focused_checks": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(hermes_fork_update, "_deploy_handoff_marker_path", lambda: marker)
    monkeypatch.setattr(
        hermes_fork_update,
        "_run_update_resolver_agent",
        lambda prompt, cwd: SimpleNamespace(
            returncode=0,
            stdout="Ready for the parent updater to validate, commit, push, fast-forward the live checkout.\n",
            stderr="",
        ),
    )

    def fake_run(cmd, **kwargs):
        cwd = kwargs.get("cwd")
        if cmd == ["git", "fetch", "origin", "tgi:refs/remotes/origin/tgi"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "fetch", "upstream", "main", "--quiet"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "diff", "--name-only", "--diff-filter=U"] and cwd == worktree:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd} cwd={cwd}")

    monkeypatch.setattr(hermes_fork_update.subprocess, "run", fake_run)

    changed = hermes_fork_update._resolve_deploy_handoff(
        git_cmd=["git"], repo=repo, branch="tgi", pre_update_head="oldhead"
    )

    assert changed is None
    out = capsys.readouterr().out
    assert "Ready for the parent updater" not in out
    assert "conflict markers remain" in out
    assert "Resolver left conflict markers in files" in out
    assert "cron/jobs.py" in out


def test_tgi_update_parser_has_one_deploy_update_mode():
    import argparse

    from hermes_cli.subcommands.update import build_update_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_update_parser(subparsers, cmd_update=lambda args: None)

    args = parser.parse_args(["update"])
    assert not hasattr(args, "resolve")
    assert not hasattr(args, "consume")
    with pytest.raises(SystemExit):
        parser.parse_args(["update", "--resolve"])
    with pytest.raises(SystemExit):
        parser.parse_args(["update", "--consume"])


def test_tgi_bare_update_refreshes_origin_before_comparing(monkeypatch, tmp_path):
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
            "tgi:refs/remotes/origin/tgi",
            "--quiet",
        ]:
            refreshed["origin"] = True
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/tgi"]:
            count = 12 if refreshed["origin"] else 0
            return SimpleNamespace(stdout=f"{count}\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/tgi..HEAD"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "origin/tgi..upstream/main"]:
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd in (
            ["git", "rev-list", "--count", "upstream/main..main"],
            ["git", "rev-list", "--count", "main..upstream/main"],
        ):
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if cmd == ["git", "fetch", "origin", "tgi:refs/remotes/origin/tgi"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "merge", "--ff-only", "origin/tgi"]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["git", "rev-list", "--count", "oldhead..HEAD"]:
            return SimpleNamespace(stdout="12\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "tgi", "oldhead"
    )

    assert changed == 12
    deploy_fetch = [
        "git",
        "fetch",
        "origin",
        "tgi:refs/remotes/origin/tgi",
        "--quiet",
    ]
    compare = ["git", "rev-list", "--count", "HEAD..origin/tgi"]
    assert calls.index(deploy_fetch) < calls.index(compare)


def test_install_method_marker_not_autostashed_by_update(tmp_path):
    """#66189: the installer ``.install_method`` stamp must be git-ignored so
    ``hermes update``'s ``git stash push --include-untracked`` does not sweep it
    into an autostash on every run.

    ``scripts/install.sh`` writes ``$INSTALL_DIR/.install_method`` as runtime
    metadata; it is a sibling of ``.hermes-bootstrap-complete`` /
    ``.update-incomplete`` and must be ignored the same way. Behavioral +
    hermetic: adopt the project's real ``.gitignore`` (the contract under test),
    drop the marker, and confirm the exact stash invocation the updater uses
    leaves it untouched.
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

    marker = tmp_path / ".install_method"
    marker.write_text("managed\n")

    # Exact flags used by hermes update (hermes_cli/main.py).
    git("stash", "push", "--include-untracked", "-m", "hermes-update-autostash")

    assert marker.exists(), (
        ".install_method was swept into the update autostash — it must be listed "
        "in .gitignore so `git stash -u` skips it (#66189)."
    )
    # It must not even register as a dirty/untracked change.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert ".install_method" not in status


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


def test_restore_rejects_invalid_python_and_keeps_clean_updated_tree(
    monkeypatch, tmp_path, capsys
):
    """A cleanly-applied stash must not be allowed to brick every agent turn."""
    import subprocess
    from hermes_cli import update_cmd

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=check,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    source = tmp_path / "tools" / "terminal_tool.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    source.write_text("<<<<<<< Updated upstream\nVALUE = 2\n", encoding="utf-8")
    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
    assert stash_ref
    monkeypatch.setattr(update_cmd, "_UPDATE_CRITICAL_MODULES", ())

    with pytest.raises(SystemExit) as exc_info:
        hermes_main._restore_stashed_changes(
            ["git"], tmp_path, stash_ref, prompt_user=False
        )

    assert exc_info.value.code == 1
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git("status", "--porcelain").stdout == ""
    assert git("stash", "list").stdout.strip()
    output = capsys.readouterr().out
    assert "made the Hermes agent unexecutable" in output
    assert "gateway was not restarted" in output
    assert f"git stash apply {stash_ref}" in output


def test_restore_rejects_new_import_time_failure_and_preserves_stash(
    monkeypatch, tmp_path, capsys
):
    """A valid-Python stash must not introduce a critical import failure."""
    import subprocess
    from hermes_cli import update_cmd

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=check,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    source = tmp_path / "consumer.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    source.write_text("raise RuntimeError('restored local failure')\n", encoding="utf-8")
    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
    assert stash_ref
    monkeypatch.setattr(update_cmd, "_UPDATE_CRITICAL_MODULES", ("consumer",))

    with pytest.raises(SystemExit) as exc_info:
        hermes_main._restore_stashed_changes(
            ["git"], tmp_path, stash_ref, prompt_user=False
        )

    assert exc_info.value.code == 1
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git("status", "--porcelain").stdout == ""
    assert git("stash", "list").stdout.strip()
    output = capsys.readouterr().out
    assert "agent import consumer" in output
    assert "restored local failure" in output
    assert "gateway was not restarted" in output


def test_restore_allows_preexisting_import_time_failure(monkeypatch, tmp_path):
    """A restore may proceed when it does not worsen an environment failure."""
    import subprocess
    from hermes_cli import update_cmd

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=check,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "consumer.py").write_text(
        "raise RuntimeError('missing local config')\n", encoding="utf-8"
    )
    local_file = tmp_path / "local.txt"
    local_file.write_text("original\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    local_file.write_text("restored\n", encoding="utf-8")
    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
    assert stash_ref
    monkeypatch.setattr(update_cmd, "_UPDATE_CRITICAL_MODULES", ("consumer",))

    assert hermes_main._restore_stashed_changes(
        ["git"], tmp_path, stash_ref, prompt_user=False
    )
    assert local_file.read_text(encoding="utf-8") == "restored\n"
    assert git("stash", "list").stdout.strip() == ""


def test_restore_rejects_later_failure_masked_by_preexisting_failure(
    monkeypatch, tmp_path, capsys
):
    """Every critical module must be compared, not only the first failure."""
    import subprocess
    from hermes_cli import update_cmd

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=check,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "first.py").write_text(
        "raise RuntimeError('missing local config')\n", encoding="utf-8"
    )
    second = tmp_path / "second.py"
    second.write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    second.write_text("raise RuntimeError('restored later failure')\n", encoding="utf-8")
    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
    assert stash_ref
    monkeypatch.setattr(update_cmd, "_UPDATE_CRITICAL_MODULES", ("first", "second"))

    with pytest.raises(SystemExit) as exc_info:
        hermes_main._restore_stashed_changes(
            ["git"], tmp_path, stash_ref, prompt_user=False
        )

    assert exc_info.value.code == 1
    assert second.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git("status", "--porcelain").stdout == ""
    assert git("stash", "list").stdout.strip()
    output = capsys.readouterr().out
    assert "agent import second" in output
    assert "restored later failure" in output
    assert "gateway was not restarted" in output


def test_restore_rejects_system_exit_masked_by_preexisting_failure(
    monkeypatch, tmp_path, capsys
):
    """A terminating import must be compared instead of hiding the marker."""
    import subprocess
    from hermes_cli import update_cmd

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=check,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "first.py").write_text(
        "raise RuntimeError('missing local config')\n", encoding="utf-8"
    )
    second = tmp_path / "second.py"
    second.write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    second.write_text("raise SystemExit('restored exit')\n", encoding="utf-8")
    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
    assert stash_ref
    monkeypatch.setattr(update_cmd, "_UPDATE_CRITICAL_MODULES", ("first", "second"))

    with pytest.raises(SystemExit) as exc_info:
        hermes_main._restore_stashed_changes(
            ["git"], tmp_path, stash_ref, prompt_user=False
        )

    assert exc_info.value.code == 1
    assert second.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git("status", "--porcelain").stdout == ""
    assert git("stash", "list").stdout.strip()
    output = capsys.readouterr().out
    assert "agent import second" in output
    assert "restored exit" in output
    assert "gateway was not restarted" in output


def test_restore_rejects_probe_termination(monkeypatch, tmp_path, capsys):
    """A stash cannot bypass import validation by terminating the probe."""
    import subprocess
    from hermes_cli import update_cmd

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=check,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    source = tmp_path / "consumer.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    source.write_text("import os\nos._exit(7)\n", encoding="utf-8")
    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
    assert stash_ref
    monkeypatch.setattr(update_cmd, "_UPDATE_CRITICAL_MODULES", ("consumer",))

    with pytest.raises(SystemExit) as exc_info:
        hermes_main._restore_stashed_changes(
            ["git"], tmp_path, stash_ref, prompt_user=False
        )

    assert exc_info.value.code == 1
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git("status", "--porcelain").stdout == ""
    assert git("stash", "list").stdout.strip()
    output = capsys.readouterr().out
    assert "critical-module probe" in output
    assert "exit code 7" in output
    assert "gateway was not restarted" in output


def test_restore_stays_parked_when_untracked_baseline_is_unknown(
    monkeypatch, tmp_path, capsys
):
    """Unknown cleanup scope must not turn into a destructive empty baseline."""
    from hermes_cli import update_cmd

    monkeypatch.setattr(update_cmd, "_git_untracked_paths", lambda *_args: None)

    restored = hermes_main._restore_stashed_changes(
        ["git"], tmp_path, "stash@{0}", prompt_user=False
    )

    assert restored is False
    output = capsys.readouterr().out
    assert "cleanup baseline is unknown" in output
    assert "git stash apply stash@{0}" in output


def test_reject_does_not_claim_cleanup_when_git_state_is_unknown(
    monkeypatch, tmp_path, capsys
):
    """Cleanup failures must not be reported as a restored clean tree."""
    from hermes_cli import update_cmd

    monkeypatch.setattr(update_cmd, "_git_untracked_paths", lambda *_args: None)

    with pytest.raises(SystemExit):
        update_cmd._reject_unsafe_stash_restore(
            ["git"], tmp_path, "stash@{0}", set(), "consumer.py", "invalid"
        )

    output = capsys.readouterr().out
    assert "could not be fully restored automatically" in output
    assert "The clean updated tree has been restored" not in output


def test_restore_rejects_unknown_restored_python_paths(
    monkeypatch, tmp_path, capsys
):
    """A failed post-apply path query cannot skip restored syntax validation."""
    import subprocess
    from hermes_cli import update_cmd

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=check,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    source = tmp_path / "consumer.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")
    source.write_text("VALUE = 2\n", encoding="utf-8")
    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
    assert stash_ref
    monkeypatch.setattr(update_cmd, "_UPDATE_CRITICAL_MODULES", ())
    monkeypatch.setattr(update_cmd, "_restored_python_paths", lambda *_args: None)

    with pytest.raises(SystemExit) as exc_info:
        hermes_main._restore_stashed_changes(
            ["git"], tmp_path, stash_ref, prompt_user=False
        )

    assert exc_info.value.code == 1
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git("status", "--porcelain").stdout == ""
    assert git("stash", "list").stdout.strip()
    output = capsys.readouterr().out
    assert "restored Python source discovery" in output
    assert "gateway was not restarted" in output


def test_gateway_restore_prompt_defaults_to_keep_stash(tmp_path, capsys):
    prompts = []

    restored = hermes_main._restore_stashed_changes(
        ["git"],
        tmp_path,
        "stash@{0}",
        prompt_user=True,
        input_fn=lambda prompt, default: prompts.append((prompt, default)) or "",
    )

    assert restored is False
    assert prompts == [("Restore local changes now? [y/N]", "n")]
    assert "still preserved in git stash" in capsys.readouterr().out
