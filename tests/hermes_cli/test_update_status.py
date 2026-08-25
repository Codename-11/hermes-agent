import argparse
import json
from types import SimpleNamespace

from hermes_cli import axiom_update
from hermes_cli import main as hermes_main
from hermes_cli.subcommands.update import build_update_parser


def test_update_parser_accepts_status_and_wait():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_update_parser(subparsers, cmd_update=lambda _args: None)

    status = parser.parse_args(["update", "--status"])
    wait = parser.parse_args(["update", "--wait"])

    assert status.status is True
    assert status.wait is False
    assert wait.wait is True
    assert wait.status is False


def test_status_formats_live_check_progress(monkeypatch, tmp_path, capsys):
    state_path = tmp_path / "axiom.json"
    state_path.write_text(
        json.dumps(
            {
                "state": "running",
                "phase": "checks",
                "detail": "deploy-update",
                "check_index": 3,
                "check_total": 19,
                "pid": 4242,
                "started_at": "2026-08-25T16:04:03",
                "log_path": str(tmp_path / "worker.log"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(axiom_update, "_reconciliation_state_path", lambda _branch: state_path)

    code = axiom_update.show_reconciliation_status("axiom")

    assert code == 0
    out = capsys.readouterr().out
    assert "Reconciliation: running" in out
    assert "checks 3/19" in out
    assert "deploy-update" in out
    assert "PID: 4242" in out


def test_wait_streams_log_and_stops_when_ready(monkeypatch, tmp_path, capsys):
    state_path = tmp_path / "axiom.json"
    log_path = tmp_path / "worker.log"
    states = iter(
        [
            {
                "state": "running",
                "phase": "checks",
                "detail": "candidate-hydration",
                "check_index": 1,
                "check_total": 2,
                "log_path": str(log_path),
            },
            {
                "state": "ready",
                "phase": "ready",
                "detail": "abc123def456",
                "candidate_sha": "abc123def4567890",
                "log_path": str(log_path),
            },
        ]
    )
    monkeypatch.setattr(axiom_update, "_reconciliation_state_path", lambda _branch: state_path)
    monkeypatch.setattr(axiom_update, "_read_reconciliation_state", lambda _path: next(states))

    def fake_sleep(_seconds):
        log_path.write_text("visible verifier output\n", encoding="utf-8")

    monkeypatch.setattr(axiom_update.time, "sleep", fake_sleep)

    code = axiom_update.show_reconciliation_status(
        "axiom", wait=True, poll_interval=0
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "checks 1/2" in out
    assert "visible verifier output" in out
    assert "Reconciliation: ready" in out
    assert "Candidate: abc123def456" in out


def test_cmd_update_status_bypasses_update_mutation(monkeypatch):
    monkeypatch.setattr("hermes_cli.config.is_managed", lambda: False)
    observed = []
    monkeypatch.setattr(
        axiom_update,
        "show_reconciliation_status",
        lambda branch, **kwargs: observed.append((branch, kwargs)) or 0,
    )
    monkeypatch.setattr(
        hermes_main,
        "_cmd_update_impl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("mutated")),
    )

    hermes_main.cmd_update(
        SimpleNamespace(
            status=True,
            wait=False,
            branch="axiom",
            plan=False,
            check=False,
        )
    )

    assert observed == [("axiom", {"wait": False})]


def test_cmd_update_status_defaults_to_current_deploy_branch(monkeypatch):
    monkeypatch.setattr("hermes_cli.config.is_managed", lambda: False)
    monkeypatch.setattr(
        hermes_main.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="axiom\n"),
    )
    observed = []
    monkeypatch.setattr(
        axiom_update,
        "show_reconciliation_status",
        lambda branch, **kwargs: observed.append((branch, kwargs)) or 0,
    )

    hermes_main.cmd_update(
        SimpleNamespace(
            status=True,
            wait=False,
            branch=None,
            plan=False,
            check=False,
        )
    )

    assert observed == [("axiom", {"wait": False})]
