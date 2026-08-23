import hashlib
import inspect
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import axiom_reconcile
from hermes_cli import axiom_update
from hermes_cli import main as hermes_main
from hermes_cli import update_cmd


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def test_run_checks_resolves_windows_command_shims(monkeypatch, tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    observed = []

    monkeypatch.setattr(axiom_reconcile.os, "name", "nt")
    monkeypatch.setattr(
        axiom_reconcile.shutil,
        "which",
        lambda command: "C:/Program Files/nodejs/npx.CMD"
        if command == "npx"
        else None,
    )

    def fake_run(argv, **_kwargs):
        observed.append(argv)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(axiom_reconcile.subprocess, "run", fake_run)

    reports, complete = axiom_reconcile._run_checks(
        worktree,
        [{"id": "desktop", "cwd": ".", "argv": ["npx", "vitest", "run"]}],
    )

    assert complete is True
    assert reports[0]["returncode"] == 0
    assert observed == [["C:/Program Files/nodejs/npx.CMD", "vitest", "run"]]


@pytest.mark.parametrize("reparse_component", ["root", "runs", "run", "file"])
def test_confined_evidence_reader_rejects_reparse_component(
    reparse_component, monkeypatch, tmp_path
):
    state_root = tmp_path / "state"
    runs = state_root / "runs"
    root = runs / "run-id"
    root.mkdir(parents=True)
    evidence = root / "report.json"
    evidence.write_text('{"state": "ready"}\n', encoding="utf-8")
    marked = {
        "root": state_root,
        "runs": runs,
        "run": root,
        "file": evidence,
    }[reparse_component]
    original_lstat = os.lstat

    def fake_lstat(path):
        result = original_lstat(path)
        if Path(path) != marked:
            return result
        return SimpleNamespace(
            st_mode=result.st_mode,
            st_dev=result.st_dev,
            st_ino=result.st_ino,
            st_file_attributes=0x400,
        )

    monkeypatch.setattr(axiom_update.os, "lstat", fake_lstat)

    with pytest.raises(OSError, match="link or reparse"):
        axiom_update._read_confined_regular_file(state_root, evidence)


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


def test_reconciliation_lock_reclaims_dead_owner(monkeypatch, tmp_path):
    lock_path = tmp_path / "axiom.lock"
    lock_path.write_text("999999999\n", encoding="utf-8")
    monkeypatch.setattr(
        axiom_update,
        "_pid_is_running",
        lambda pid: int(pid) == 12345,
    )

    descriptor = axiom_update._claim_reconciliation_lock(lock_path)

    assert descriptor is not None
    axiom_update._release_reconciliation_lock(lock_path, descriptor)
    assert lock_path.read_text(encoding="utf-8") == f"{os.getpid()}\n"


@pytest.mark.parametrize(
    ("module", "claim_name", "release_name"),
    [
        (axiom_update, "_claim_reconciliation_lock", "_release_reconciliation_lock"),
        (axiom_reconcile, "_claim_state_lock", "_release_state_lock"),
    ],
)
def test_stale_lock_takeover_never_unlinks_path(
    module, claim_name, release_name, monkeypatch, tmp_path
):
    lock_path = tmp_path / "axiom.lock"
    lock_path.write_text("999999999\n", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda _path: (_ for _ in ()).throw(AssertionError("lock path was unlinked")),
    )

    descriptor = getattr(module, claim_name)(lock_path)

    assert descriptor is not None
    getattr(module, release_name)(lock_path, descriptor)


@pytest.mark.parametrize(
    ("module", "claim_name", "release_name"),
    [
        (
            axiom_update,
            "_claim_reconciliation_lock",
            "_release_reconciliation_lock",
        ),
        (
            axiom_reconcile,
            "_claim_state_lock",
            "_release_state_lock",
        ),
    ],
)
def test_lock_release_does_not_remove_replacement(
    module, claim_name, release_name, monkeypatch, tmp_path
):
    del claim_name
    release = getattr(module, release_name)
    lock_path = tmp_path / "axiom.lock"
    closed = []
    unlinked = []
    monkeypatch.setattr(
        module.os,
        "fstat",
        lambda _descriptor: SimpleNamespace(st_dev=1, st_ino=1),
    )
    monkeypatch.setattr(
        Path,
        "stat",
        lambda _path: SimpleNamespace(st_dev=1, st_ino=2),
    )
    monkeypatch.setattr(module.os, "close", lambda descriptor: closed.append(descriptor))
    monkeypatch.setattr(Path, "unlink", lambda path: unlinked.append(path))

    release(lock_path, 42)

    assert closed == [42]
    assert unlinked == []


def test_stale_worker_cannot_overwrite_newer_canonical_state(tmp_path):
    canonical = tmp_path / "axiom.json"
    canonical.write_text(
        json.dumps({"run_id": "new-run", "input_digest": "new-digest", "state": "queued"}),
        encoding="utf-8",
    )

    updated = axiom_reconcile._update_canonical_state_if_current(
        canonical,
        run_id="old-run",
        input_digest="old-digest",
        state="ready",
        candidate_sha="a" * 40,
    )

    assert updated is False
    assert json.loads(canonical.read_text(encoding="utf-8")) == {
        "run_id": "new-run",
        "input_digest": "new-digest",
        "state": "queued",
    }


def test_stale_worker_waits_for_queue_publication_lock(tmp_path):
    canonical = tmp_path / "axiom.json"
    canonical.write_text(
        json.dumps({"run_id": "old-run", "input_digest": "old-digest", "state": "running"}),
        encoding="utf-8",
    )
    lock_path = canonical.with_suffix(".lock")
    descriptor = axiom_update._claim_reconciliation_lock(lock_path)
    assert descriptor is not None
    results = []

    worker = threading.Thread(
        target=lambda: results.append(
            axiom_reconcile._update_canonical_state_if_current(
                canonical,
                run_id="old-run",
                input_digest="old-digest",
                state="ready",
            )
        )
    )
    worker.start()
    time.sleep(0.05)
    canonical.write_text(
        json.dumps({"run_id": "new-run", "input_digest": "new-digest", "state": "queued"}),
        encoding="utf-8",
    )
    axiom_update._release_reconciliation_lock(lock_path, descriptor)
    worker.join(timeout=5)

    assert results == [False]
    assert json.loads(canonical.read_text(encoding="utf-8"))["run_id"] == "new-run"


def test_promotion_holds_canonical_lock_for_entire_transaction(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "state" / "axiom.json"
    state_path.parent.mkdir()
    entered = threading.Event()
    release = threading.Event()

    def fake_locked(**_kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return 1

    monkeypatch.setattr(
        axiom_update,
        "_promote_ready_reconciliation_candidate_locked",
        fake_locked,
    )
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            axiom_update._promote_ready_reconciliation_candidate(
                git_cmd=["git"],
                repo=tmp_path,
                branch="axiom",
                upstream_sha="a" * 40,
                pre_update_head="b" * 40,
                state_path=state_path,
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    contender = axiom_update._claim_reconciliation_lock(
        state_path.with_suffix(".lock")
    )
    assert contender is None
    release.set()
    thread.join(timeout=5)

    assert result == [1]


def test_stale_worker_does_not_query_or_publish_candidate_ref(
    monkeypatch, tmp_path
):
    canonical = tmp_path / "axiom.json"
    canonical.write_text(
        json.dumps({"run_id": "new-run", "input_digest": "new-digest"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        axiom_reconcile,
        "_remote_branch_sha",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale worker queried candidate ref")
        ),
    )

    with pytest.raises(RuntimeError, match="stale reconciliation worker"):
        axiom_reconcile._publish_candidate_if_current(
            repo=tmp_path,
            worktree=tmp_path,
            branch="axiom",
            candidate_sha="a" * 40,
            canonical_state_path=canonical,
            run_id="old-run",
            input_digest="old-digest",
        )


def test_candidate_publish_accepts_ambiguous_push_after_exact_readback(
    monkeypatch, tmp_path
):
    candidate_sha = "a" * 40
    reads = iter(["b" * 40, candidate_sha])
    monkeypatch.setattr(
        axiom_reconcile,
        "_remote_branch_sha",
        lambda *_args, **_kwargs: next(reads),
    )
    monkeypatch.setattr(
        axiom_reconcile,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="transport disconnected"
        ),
    )

    axiom_reconcile._publish_candidate_if_current(
        repo=tmp_path,
        worktree=tmp_path,
        branch="axiom",
        candidate_sha=candidate_sha,
        canonical_state_path=None,
        run_id="run-id",
        input_digest="digest",
    )


def test_fetch_replay_source_makes_commit_available_in_single_branch_clone(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "-q", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    _git(seed, "config", "user.name", "Source Fetch Test")
    _git(seed, "config", "user.email", "source-fetch@example.invalid")
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "base.txt")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "HEAD:axiom")
    (seed / "carry.txt").write_text("carry\n", encoding="utf-8")
    _git(seed, "add", "carry.txt")
    _git(seed, "commit", "-q", "-m", "carry")
    carry_sha = _git(seed, "rev-parse", "HEAD")
    _git(seed, "push", "-q", "origin", "HEAD:carry/test-source")
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--single-branch",
            "--no-local",
            "--branch",
            "axiom",
            str(origin),
            str(clone),
        ],
        check=True,
    )
    assert axiom_reconcile._resolve(clone, carry_sha) == ""
    carries = [
        {
            "id": "test-source",
            "replay": {
                "source_ref": "origin/carry/test-source",
                "commits": [carry_sha],
            },
        }
    ]

    private_refs = axiom_reconcile._fetch_replay_sources(
        clone, carries, run_id="test-run"
    )
    try:
        assert axiom_reconcile._resolve(clone, carry_sha) == carry_sha
        assert len(private_refs) == 1
        assert axiom_reconcile._resolve(clone, private_refs[0]) == carry_sha
    finally:
        axiom_reconcile._delete_private_refs(clone, private_refs)


def test_fetch_replay_sources_rejects_leading_dash_remote(monkeypatch, tmp_path):
    monkeypatch.setattr(
        axiom_reconcile,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("git invoked for invalid source_ref")
        ),
    )
    carries = [
        {
            "id": "invalid-source",
            "replay": {
                "source_ref": "-origin/carry/source",
                "commits": ["a" * 40],
            },
        }
    ]

    with pytest.raises(RuntimeError, match="invalid replay source_ref"):
        axiom_reconcile._fetch_replay_sources(
            tmp_path, carries, run_id="b" * 24
        )


def test_fetch_replay_sources_cleans_private_ref_after_readback_failure(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(_repo, *args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=1 if args[:3] == ("show-ref", "--verify", "--quiet") else 0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(axiom_reconcile, "_run", fake_run)
    monkeypatch.setattr(axiom_reconcile, "_resolve", lambda *_args: "")
    carries = [
        {
            "id": "missing-readback",
            "replay": {
                "source_ref": "origin/carry/source",
                "commits": ["a" * 40],
            },
        }
    ]

    with pytest.raises(RuntimeError, match="read-back failed"):
        axiom_reconcile._fetch_replay_sources(
            tmp_path, carries, run_id="b" * 24
        )

    assert any(args[:2] == ("update-ref", "-d") for args in calls)


def test_private_ref_cleanup_fails_when_ref_remains(monkeypatch, tmp_path):
    monkeypatch.setattr(
        axiom_reconcile,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="delete refused"
        ),
    )
    monkeypatch.setattr(axiom_reconcile, "_resolve", lambda *_args: "a" * 40)

    with pytest.raises(RuntimeError, match="could not delete private replay ref"):
        axiom_reconcile._delete_private_refs(
            tmp_path, ["refs/axiom-reconcile/run/sources/source"]
        )


def test_private_ref_cleanup_rejects_ambiguous_absence_query(monkeypatch, tmp_path):
    def fake_run(_repo, *args, **_kwargs):
        if args[:2] == ("update-ref", "-d"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=128, stdout="", stderr="repository unreadable")

    monkeypatch.setattr(axiom_reconcile, "_run", fake_run)

    with pytest.raises(RuntimeError, match="could not verify private replay ref absence"):
        axiom_reconcile._delete_private_refs(
            tmp_path, ["refs/axiom-reconcile/run/sources/source"]
        )


def test_private_ref_cleanup_attempts_all_refs_before_raising(monkeypatch, tmp_path):
    first = "refs/axiom-reconcile/run/sources/first"
    second = "refs/axiom-reconcile/run/sources/second"
    deleted = []

    def fake_run(_repo, *args, **_kwargs):
        if args[:2] == ("update-ref", "-d"):
            deleted.append(args[2])
            return SimpleNamespace(
                returncode=1 if args[2] == first else 0,
                stdout="",
                stderr="delete refused" if args[2] == first else "",
            )
        return SimpleNamespace(
            returncode=0 if args[-1] == first else 1,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(axiom_reconcile, "_run", fake_run)

    with pytest.raises(RuntimeError, match="could not clean private replay refs"):
        axiom_reconcile._delete_private_refs(tmp_path, [first, second])

    assert deleted == [first, second]


def test_replay_commit_must_descend_from_declared_source(monkeypatch, tmp_path):
    carry = {
        "id": "bounded-carry",
        "replay": {
            "source_ref": "origin/carry/bounded-carry",
            "commits": ["a" * 40],
        },
    }
    monkeypatch.setattr(
        axiom_reconcile,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError, match="not reachable from declared source"):
        axiom_reconcile._validate_replay_commit_sources(
            tmp_path, [carry], run_id="run-id"
        )


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


@pytest.mark.parametrize("module", [axiom_update, axiom_reconcile])
def test_windows_wait_failure_is_not_treated_as_dead(module, monkeypatch):
    import ctypes

    class FakeCall:
        def __init__(self, result):
            self.result = result
            self.restype = None

        def __call__(self, *_args):
            return self.result

    kernel32 = SimpleNamespace(
        OpenProcess=FakeCall(42),
        WaitForSingleObject=FakeCall(0xFFFFFFFF),
        CloseHandle=FakeCall(1),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32)

    assert module._pid_is_running(12345) is True


def test_promotion_rebinds_worker_manifest_validator_and_report(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / "run-id"
    run_dir.mkdir(parents=True)
    branch = "axiom"
    upstream_sha = "b" * 40
    candidate_sha = "a" * 40
    worker_bytes = b"# immutable worker\n"
    manifest_bytes = b'{"carries": []}\n'
    validator_bytes = b"# immutable validator\n"
    (run_dir / "axiom_reconcile.py").write_bytes(worker_bytes)
    (run_dir / "fork-carries.json").write_bytes(manifest_bytes)
    (run_dir / "fork_carry_manifest.py").write_bytes(validator_bytes)
    (repo / "fork-carries.json").write_bytes(manifest_bytes)
    digest = hashlib.sha256()
    digest.update(branch.encode())
    digest.update(b"\0")
    digest.update(upstream_sha.encode())
    for name, payload in sorted(
        {
            "worker": worker_bytes,
            "manifest": manifest_bytes,
            "validator": validator_bytes,
        }.items()
    ):
        digest.update(b"\0" + name.encode() + b"\0" + payload)
    input_digest = digest.hexdigest()
    report_path = run_dir / "report.json"
    report = {
        "state": "ready",
        "branch": branch,
        "candidate_branch": "axiom-next",
        "upstream_sha": upstream_sha,
        "candidate_sha": candidate_sha,
        "input_digest": input_digest,
        "worker_sha256": hashlib.sha256(worker_bytes).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "validator_sha256": hashlib.sha256(validator_bytes).hexdigest(),
        "replay_sha256": "c" * 64,
        "report_path": str(report_path.resolve()),
        "published": True,
        "ownership_diagnostics": [],
        "upstream_survival": {"noncarry_paths_equal": True},
        "checks_complete": True,
        "checks": [{"id": "verified", "returncode": 0}],
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    state_path = tmp_path / "axiom.json"
    state_path.write_text(
        json.dumps(
            {
                **report,
                "run_id": input_digest[:24],
                "run_dir": str(run_dir),
                "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "axiom_reconcile.py").write_bytes(worker_bytes + b"# mutated\n")
    monkeypatch.setattr(
        axiom_update,
        "_remote_head_sha",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("remote state queried before immutable evidence validation")
        ),
    )

    refused = axiom_update._promote_ready_reconciliation_candidate(
        git_cmd=["git"],
        repo=repo,
        branch=branch,
        upstream_sha=upstream_sha,
        pre_update_head="d" * 40,
        state_path=state_path,
    )

    assert refused == 0


def test_promotion_rejects_run_directory_outside_canonical_root(
    tmp_path, capsys
):
    state_path = tmp_path / "state" / "axiom.json"
    state_path.parent.mkdir()
    input_digest = "a" * 64
    run_id = input_digest[:24]
    external_run_dir = tmp_path / "external" / run_id
    external_run_dir.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "state": "ready",
                "upstream_sha": "b" * 40,
                "run_id": run_id,
                "input_digest": input_digest,
                "run_dir": str(external_run_dir),
                "state_path": str(external_run_dir / "state.json"),
                "report_path": str(external_run_dir / "report.json"),
            }
        ),
        encoding="utf-8",
    )

    refused = axiom_update._promote_ready_reconciliation_candidate(
        git_cmd=["git"],
        repo=tmp_path,
        branch="axiom",
        upstream_sha="b" * 40,
        pre_update_head="c" * 40,
        state_path=state_path,
    )

    assert refused == 0
    assert "outside the canonical reconciliation root" in capsys.readouterr().out


def test_push_ref_uses_remote_readback_after_ambiguous_transport_failure(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        axiom_update.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="connection closed"
        ),
    )
    monkeypatch.setattr(
        axiom_update,
        "_remote_head_sha",
        lambda *_args, **_kwargs: "a" * 40,
    )

    assert axiom_update._push_ref_and_verify(
        git_cmd=["git"],
        repo=tmp_path,
        push_args=["push", "origin", "source:refs/heads/axiom"],
        branch="axiom",
        expected_sha="a" * 40,
    )


def test_reset_and_verify_rejects_successful_reset_with_wrong_head(
    monkeypatch, tmp_path
):
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="b" * 40 + "\n", stderr=""),
        ]
    )
    monkeypatch.setattr(
        axiom_update.subprocess,
        "run",
        lambda *_args, **_kwargs: next(responses),
    )

    assert not axiom_update._reset_and_verify_local_head(
        git_cmd=["git"], repo=tmp_path, expected_sha="a" * 40
    )


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
    promotion_calls = []
    events = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "merge", "--ff-only", "origin/axiom"]:
            events.append("merge")
        responses = {
            ("git", "fetch", "upstream", "--quiet"): "",
            ("git", "fetch", "origin", "axiom:refs/remotes/origin/axiom", "--quiet"): "",
            ("git", "rev-list", "--count", "HEAD..origin/axiom"): "2\n",
            ("git", "rev-list", "--count", "origin/axiom..HEAD"): "0\n",
            ("git", "rev-list", "--count", "origin/axiom..upstream/main"): "3\n",
            ("git", "merge", "--ff-only", "origin/axiom"): "Updating\n",
            ("git", "rev-list", "--count", "oldhead..HEAD"): "2\n",
            ("git", "rev-parse", "--verify", "upstream/main^{commit}"): f"{'b' * 40}\n",
            ("git", "rev-parse", "--verify", "origin/axiom^{commit}"): f"{'d' * 40}\n",
        }
        key = tuple(cmd)
        if key in responses:
            return SimpleNamespace(stdout=responses[key], stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    def record_queue(**kwargs):
        events.append("queue")
        queue_call_counts.append(len(calls))
        queued.append(kwargs)
        return {"state": "queued", "pid": 43}

    def record_promotion(**kwargs):
        events.append("promote")
        promotion_calls.append((len(calls), kwargs))
        return None

    monkeypatch.setattr(axiom_update, "_queue_fork_reconciliation", record_queue)
    monkeypatch.setattr(
        axiom_update,
        "_promote_ready_reconciliation_candidate",
        record_promotion,
    )

    changed = hermes_main._run_deploy_branch_update(
        ["git"], tmp_path, "axiom", "oldhead"
    )

    assert changed == 2
    assert events[:3] == ["merge", "promote", "queue"]
    assert promotion_calls[0][1]["pre_update_head"] == "d" * 40
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
                "paths": ["owned.txt", "fork-carries.json"],
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
    state_root = tmp_path / "state"
    state_root.mkdir()
    worker_bytes = Path(axiom_reconcile.__file__).read_bytes()
    validator_bytes = (repo / "validator.py").read_bytes()
    evidence = axiom_update._reconciliation_input_evidence(
        "axiom",
        upstream_sha,
        {
            "worker": worker_bytes,
            "manifest": manifest_bytes,
            "validator": validator_bytes,
        },
    )
    input_digest = evidence["input_digest"]
    run_id = input_digest[:24]
    run_dir = state_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    worker_path = run_dir / "axiom_reconcile.py"
    manifest_path = run_dir / "fork-carries.json"
    validator_path = run_dir / "fork_carry_manifest.py"
    worker_path.write_bytes(worker_bytes)
    manifest_path.write_bytes(manifest_bytes)
    validator_path.write_bytes(validator_bytes)
    state_path = run_dir / "state.json"
    report_path = run_dir / "report.json"
    canonical_state_path = state_root / "axiom.json"
    queued_state = {
        "state": "queued",
        "branch": "axiom",
        "upstream_sha": upstream_sha,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "input_digest": input_digest,
        "worker_sha256": evidence["worker_sha256"],
        "manifest_sha256": evidence["manifest_sha256"],
        "validator_sha256": evidence["validator_sha256"],
        "state_path": str(state_path),
        "report_path": str(report_path),
    }
    state_path.write_text(json.dumps(queued_state), encoding="utf-8")
    canonical_state_path.write_text(json.dumps(queued_state), encoding="utf-8")

    report = axiom_reconcile.generate_candidate(
        repo=repo,
        branch="axiom",
        upstream_sha=upstream_sha,
        state_path=state_path,
        canonical_state_path=canonical_state_path,
        report_path=report_path,
        manifest_path=manifest_path,
        validator_path=validator_path,
        input_digest=input_digest,
        worker_path=worker_path,
        run_checks=True,
        publish=True,
    )
    state_path = canonical_state_path

    deploy_sha = _git(tmp_path, "--git-dir", str(origin), "rev-parse", "refs/heads/axiom")
    candidate_sha = _git(
        tmp_path, "--git-dir", str(origin), "rev-parse", "refs/heads/axiom-next"
    )
    assert deploy_sha == upstream_sha
    assert report["state"] == "ready"
    assert report["published"] is True
    assert report["candidate_sha"] == candidate_sha
    assert report["input_digest"] == input_digest
    assert report["worker_sha256"] == evidence["worker_sha256"]
    assert report["replay_sha256"]
    assert report["report_path"] == str(report_path)
    assert report["run_dir"] == str(run_dir)
    assert report["state_path"] == str(run_dir / "state.json")
    assert json.loads(canonical_state_path.read_text())["state"] == "ready"
    assert report["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    candidate_manifest = subprocess.run(
        ["git", "show", f"{candidate_sha}:fork-carries.json"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert candidate_manifest == manifest_bytes
    generated_committer_date = _git(repo, "show", "-s", "--format=%cI", upstream_sha)
    candidate_committer = _git(
        repo, "show", "-s", "--format=%cn|%ce|%cI", candidate_sha
    )
    assert candidate_committer == (
        f"Axiom Carry Replay|axiom-carry-replay@localhost|{generated_committer_date}"
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
    assert promoted == 2
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
    assert len([c for c in manifest["carries"] if c["status"] == "active"]) == 16
    assert next(
        c for c in manifest["carries"] if c["id"] == "desktop-registered-source-routing"
    )["status"] == "retired"


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
