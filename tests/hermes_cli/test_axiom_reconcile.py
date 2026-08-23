import hashlib
import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from hermes_cli import axiom_reconcile
from hermes_cli import axiom_update
from hermes_cli import main as hermes_main
from hermes_cli import update_cmd


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def test_reconciliation_lock_is_atomic(tmp_path):
    lock_path = tmp_path / "axiom.lock"

    first = axiom_update._claim_reconciliation_lock(lock_path)
    try:
        assert first is not None
        assert axiom_update._claim_reconciliation_lock(lock_path) is None
    finally:
        axiom_update._release_reconciliation_lock(lock_path, first)

    second = axiom_update._claim_reconciliation_lock(lock_path)
    assert second is not None
    axiom_update._release_reconciliation_lock(lock_path, second)


def test_queue_snapshots_immutable_worker_and_manifest(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / "hermes_cli").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "hermes_cli" / "axiom_reconcile.py").write_text("# worker\n", encoding="utf-8")
    (repo / "scripts" / "fork_carry_manifest.py").write_text("# validator\n", encoding="utf-8")
    (repo / "fork-carries.json").write_text('{"carries": []}\n', encoding="utf-8")
    state_path = tmp_path / "state" / "axiom.json"
    monkeypatch.setattr(axiom_update, "_reconciliation_state_path", lambda _branch: state_path)
    launched = []

    class FakeProcess:
        pid = 4242

    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(axiom_update.subprocess, "Popen", fake_popen)

    state = axiom_update._queue_fork_reconciliation(
        repo=repo, branch="axiom", upstream_sha="a" * 40
    )

    run_dir = Path(str(state["run_dir"]))
    command, kwargs = launched[0]
    assert state["run_id"]
    assert (run_dir / "axiom_reconcile.py").read_text() == "# worker\n"
    assert (run_dir / "fork_carry_manifest.py").read_text() == "# validator\n"
    assert (run_dir / "fork-carries.json").read_text() == '{"carries": []}\n'
    assert command[:2] == [sys.executable, str(run_dir / "axiom_reconcile.py")]
    assert command[command.index("--manifest-path") + 1] == str(
        run_dir / "fork-carries.json"
    )
    assert command[command.index("--validator-path") + 1] == str(
        run_dir / "fork_carry_manifest.py"
    )
    assert kwargs["cwd"] == run_dir


def test_pid_liveness_probe_does_not_signal_process():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert axiom_update._pid_is_running(child.pid) is True
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_bare_update_has_no_legacy_handoff_resolver_call():
    source = inspect.getsource(update_cmd._cmd_update_impl)
    assert "_resolve_deploy_handoff(" not in source


def test_bare_update_refuses_locally_ahead_deploy_without_legacy_push(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        responses = {
            ("git", "fetch", "upstream", "--quiet"): "",
            ("git", "fetch", "origin", "axiom:refs/remotes/origin/axiom", "--quiet"): "",
            ("git", "rev-list", "--count", "HEAD..origin/axiom"): "0\n",
            ("git", "rev-list", "--count", "origin/axiom..HEAD"): "1\n",
            ("git", "rev-list", "--count", "origin/axiom..upstream/main"): "0\n",
        }
        key = tuple(cmd)
        if key in responses:
            return SimpleNamespace(stdout=responses[key], stderr="", returncode=0)
        raise AssertionError(f"unexpected legacy command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "localhead"
    )

    assert changed is None
    assert not any("push" in cmd or "worktree" in cmd for cmd in calls)


def test_deploy_branch_update_queues_upstream_without_mutating_live_refs(
    monkeypatch, tmp_path, capsys
):
    calls = []
    queued = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        responses = {
            ("git", "fetch", "upstream", "--quiet"): "",
            ("git", "fetch", "origin", "axiom:refs/remotes/origin/axiom", "--quiet"): "",
            ("git", "rev-list", "--count", "HEAD..origin/axiom"): "0\n",
            ("git", "rev-list", "--count", "origin/axiom..HEAD"): "0\n",
            ("git", "rev-list", "--count", "origin/axiom..upstream/main"): "3\n",
            ("git", "rev-parse", "--verify", "upstream/main^{commit}"): f"{'a' * 40}\n",
        }
        key = tuple(cmd)
        if key in responses:
            return SimpleNamespace(stdout=responses[key], stderr="", returncode=0)
        raise AssertionError(f"unexpected mutating command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr(
        axiom_update,
        "_queue_fork_reconciliation",
        lambda **kwargs: queued.append(kwargs) or {"state": "queued", "pid": 42},
    )

    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "oldhead"
    )

    assert changed == axiom_update.RECONCILIATION_QUEUED
    assert queued == [
        {"repo": tmp_path, "branch": "axiom", "upstream_sha": "a" * 40}
    ]
    assert not any("worktree" in cmd or "push" in cmd or "branch" in cmd for cmd in calls)
    out = capsys.readouterr().out
    assert "Candidate verification started" in out
    assert "Current deployment unchanged" in out


def test_deploy_branch_update_consumes_published_candidate_before_queueing_new_upstream(
    monkeypatch, tmp_path
):
    calls = []
    queued = []
    queue_call_counts = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        responses = {
            ("git", "fetch", "upstream", "--quiet"): "",
            ("git", "fetch", "origin", "axiom:refs/remotes/origin/axiom", "--quiet"): "",
            ("git", "rev-list", "--count", "HEAD..origin/axiom"): "2\n",
            ("git", "rev-list", "--count", "origin/axiom..HEAD"): "0\n",
            ("git", "rev-list", "--count", "origin/axiom..upstream/main"): "3\n",
            ("git", "merge", "--ff-only", "origin/axiom"): "Updating\n",
            ("git", "rev-list", "--count", "oldhead..HEAD"): "2\n",
            ("git", "rev-parse", "--verify", "upstream/main^{commit}"): f"{'b' * 40}\n",
        }
        key = tuple(cmd)
        if key in responses:
            return SimpleNamespace(stdout=responses[key], stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    def record_queue(**kwargs):
        queue_call_counts.append(len(calls))
        queued.append(kwargs)
        return {"state": "queued", "pid": 43}

    monkeypatch.setattr(axiom_update, "_queue_fork_reconciliation", record_queue)

    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "oldhead"
    )

    assert changed == 2
    assert calls.index(["git", "merge", "--ff-only", "origin/axiom"]) < queue_call_counts[0]
    assert queued[0]["upstream_sha"] == "b" * 40
    assert not any("push" in cmd or "worktree" in cmd for cmd in calls)

    calls.clear()
    monkeypatch.setattr(
        axiom_update,
        "_queue_fork_reconciliation",
        lambda **_kwargs: {"state": "failed", "error": "spawn denied"},
    )
    changed_with_queue_failure = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "oldhead"
    )
    assert changed_with_queue_failure == 2


def test_generate_candidate_publishes_only_candidate_ref(monkeypatch, tmp_path):
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", "-q", str(origin))
    _git(tmp_path, "init", "--bare", "-q", str(upstream))
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Candidate Test")
    _git(repo, "config", "user.email", "candidate@example.invalid")
    (repo / "owned.txt").write_text("upstream\n", encoding="utf-8")
    _git(repo, "add", "owned.txt")
    _git(repo, "commit", "-q", "-m", "upstream base")
    upstream_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "remote", "add", "upstream", str(upstream))
    _git(repo, "push", "-q", "origin", "HEAD:axiom")
    _git(repo, "push", "-q", "upstream", "HEAD:main")
    _git(repo, "fetch", "-q", "upstream", "main")

    (repo / "owned.txt").write_text("carried\n", encoding="utf-8")
    _git(repo, "add", "owned.txt")
    _git(repo, "commit", "-q", "-m", "carry")
    carry_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "--hard", "-q", upstream_sha)

    manifest = {
        "carries": [
            {
                "id": "test-carry",
                "order": 10,
                "status": "active",
                "paths": ["owned.txt"],
                "tests": ["tests/test_owned.py"],
                "contract": {"path": "FORK.md", "heading": "Test"},
                "checks": [],
                "replay": {"commits": [carry_sha]},
            }
        ]
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (repo / "fork-carries.json").write_bytes(manifest_bytes)
    (repo / "validator.py").write_text("# validator\n", encoding="utf-8")
    monkeypatch.setattr(
        axiom_reconcile,
        "_load_manifest",
        lambda _repo, **_kwargs: (manifest, []),
    )
    monkeypatch.setattr(
        axiom_reconcile,
        "_run_checks",
        lambda _worktree, _checks: ([{"id": "test", "returncode": 0}], True),
    )
    state_path = tmp_path / "state" / "axiom.json"
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps({"state": "queued", "upstream_sha": upstream_sha}),
        encoding="utf-8",
    )

    canonical_state_path = tmp_path / "state" / "canonical.json"
    report_path = tmp_path / "state" / "run-report.json"
    report = axiom_reconcile.generate_candidate(
        repo=repo,
        branch="axiom",
        upstream_sha=upstream_sha,
        state_path=state_path,
        canonical_state_path=canonical_state_path,
        report_path=report_path,
        manifest_path=repo / "fork-carries.json",
        validator_path=repo / "validator.py",
        input_digest="input-digest",
        run_checks=True,
        publish=True,
    )

    deploy_sha = _git(tmp_path, "--git-dir", str(origin), "rev-parse", "refs/heads/axiom")
    candidate_sha = _git(
        tmp_path, "--git-dir", str(origin), "rev-parse", "refs/heads/axiom-next"
    )
    assert deploy_sha == upstream_sha
    assert report["state"] == "ready"
    assert report["published"] is True
    assert report["candidate_sha"] == candidate_sha
    assert report["input_digest"] == "input-digest"
    assert report["replay_sha256"]
    assert report["report_path"] == str(report_path)
    assert json.loads(canonical_state_path.read_text())["state"] == "ready"
    assert report["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    source_committer_date = _git(repo, "show", "-s", "--format=%cI", carry_sha)
    candidate_committer = _git(
        repo, "show", "-s", "--format=%cn|%ce|%cI", candidate_sha
    )
    assert candidate_committer == (
        f"Axiom Carry Replay|axiom-carry-replay@localhost|{source_committer_date}"
    )
    assert report["upstream_survival"] == {
        "mode": "generated-from-pinned-upstream",
        "noncarry_paths_equal": True,
    }

    (repo / "fork-carries.json").write_bytes(manifest_bytes + b" \n")
    refused = axiom_update._promote_ready_reconciliation_candidate(
        git_cmd=["git"],
        repo=repo,
        branch="axiom",
        upstream_sha=upstream_sha,
        pre_update_head=upstream_sha,
        state_path=state_path,
    )
    assert refused == 0
    assert _git(
        tmp_path, "--git-dir", str(origin), "rev-parse", "refs/heads/axiom"
    ) == upstream_sha
    (repo / "fork-carries.json").write_bytes(manifest_bytes)

    (repo / "local-only.txt").write_text("do not discard\n", encoding="utf-8")
    _git(repo, "add", "local-only.txt")
    _git(repo, "commit", "-q", "-m", "local only")
    local_only_sha = _git(repo, "rev-parse", "HEAD")
    refused_divergence = axiom_update._promote_ready_reconciliation_candidate(
        git_cmd=["git"],
        repo=repo,
        branch="axiom",
        upstream_sha=upstream_sha,
        pre_update_head=local_only_sha,
        state_path=state_path,
    )
    assert refused_divergence == 0
    assert _git(repo, "rev-parse", "HEAD") == local_only_sha
    assert _git(
        tmp_path, "--git-dir", str(origin), "rev-parse", "refs/heads/axiom"
    ) == upstream_sha
    _git(repo, "reset", "--hard", "-q", upstream_sha)

    real_run = axiom_update.subprocess.run

    def fail_candidate_reset(command, **kwargs):
        if command == ["git", "reset", "--hard", candidate_sha]:
            return SimpleNamespace(returncode=1, stdout="", stderr="reset failed")
        return real_run(command, **kwargs)

    monkeypatch.setattr(axiom_update.subprocess, "run", fail_candidate_reset)
    rollback_result = axiom_update._promote_ready_reconciliation_candidate(
        git_cmd=["git"],
        repo=repo,
        branch="axiom",
        upstream_sha=upstream_sha,
        pre_update_head=upstream_sha,
        state_path=state_path,
    )
    assert rollback_result == 0
    assert _git(
        tmp_path, "--git-dir", str(origin), "rev-parse", "refs/heads/axiom"
    ) == upstream_sha
    assert _git(repo, "rev-parse", "HEAD") == upstream_sha
    failed_state = json.loads(state_path.read_text())
    assert failed_state["state"] == "failed"
    assert failed_state["rollback_verified"] is True
    failed_state["state"] = "ready"
    state_path.write_text(json.dumps(failed_state), encoding="utf-8")
    monkeypatch.setattr(axiom_update.subprocess, "run", real_run)

    promoted = axiom_update._promote_ready_reconciliation_candidate(
        git_cmd=["git"],
        repo=repo,
        branch="axiom",
        upstream_sha=upstream_sha,
        pre_update_head=upstream_sha,
        state_path=state_path,
    )

    promoted_sha = _git(
        tmp_path, "--git-dir", str(origin), "rev-parse", "refs/heads/axiom"
    )
    archive_refs = _git(
        tmp_path,
        "--git-dir",
        str(origin),
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads/archive/axiom-pre-*",
    ).splitlines()
    assert promoted == 1
    assert promoted_sha == candidate_sha
    assert _git(repo, "rev-parse", "HEAD") == candidate_sha
    assert len(archive_refs) == 2
    assert all(
        _git(tmp_path, "--git-dir", str(origin), "rev-parse", ref) == upstream_sha
        for ref in archive_refs
    )


def test_update_command_stops_post_update_pipeline_for_queued_reconciliation():
    assert update_cmd._deploy_reconciliation_was_queued(-1) is True
    assert update_cmd._deploy_reconciliation_was_queued(0) is False
    assert update_cmd._deploy_reconciliation_was_queued(3) is False


def test_real_manifest_loader_uses_repository_validator():
    repo = Path(__file__).resolve().parents[2]

    manifest, diagnostics = axiom_reconcile._load_manifest(repo)

    assert diagnostics == []
    assert len([c for c in manifest["carries"] if c["status"] == "active"]) == 17


def test_candidate_path_ownership_rejects_noncarry_delta():
    manifest = {
        "carries": [
            {
                "status": "active",
                "paths": ["hermes_cli/axiom_update.py"],
                "tests": ["tests/hermes_cli/test_update_autostash.py"],
                "contract": {
                    "path": "docs/axiom-fork-contract.md",
                    "heading": "Deploy updates",
                },
                "references": ["docs/refs/axiom-fork-reconciliation-standard.md"],
            }
        ]
    }

    diagnostics = axiom_reconcile.candidate_path_ownership_diagnostics(
        manifest,
        [
            "hermes_cli/axiom_update.py",
            "tests/hermes_cli/test_update_autostash.py",
            "docs/axiom-fork-contract.md",
            "upstream_feature.py",
        ],
    )

    assert diagnostics == ["unowned candidate path: upstream_feature.py"]


def test_candidate_path_ownership_accepts_declared_directory_descendants():
    manifest = {
        "carries": [
            {
                "status": "active",
                "paths": ["plugins/forge"],
                "tests": ["tests/plugins/test_forge.py"],
                "contract": {"path": "FORK.md", "heading": "Forge"},
            }
        ]
    }

    assert axiom_reconcile.candidate_path_ownership_diagnostics(
        manifest, ["plugins/forge/tools/router.py"]
    ) == []
