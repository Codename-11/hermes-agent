"""#84185: a Windows gateway cold-started after update that dies immediately
(e.g. a job object denying breakaway) must not be reported as started.

``_cold_start_windows_gateway_after_update`` used to print the success line
straight off a successful ``Popen`` return, which only proves the process was
created, not that it survived. This asserts the observable output: the
success line is gated on the process actually being found alive afterwards,
same as every other ``_spawn_detached`` caller.
"""

from __future__ import annotations

from hermes_cli import gateway as hermes_gateway
from hermes_cli import gateway_windows
from hermes_cli import main as cli_main
from hermes_cli import update_cmd


def _run_cold_start(monkeypatch, capsys, *, surviving_pids, launcher_alive=False):
    monkeypatch.setattr(cli_main, "_is_windows", lambda: True)

    # The pre-spawn re-check (``all_profiles=True``) must find nothing
    # running so the cold-start path proceeds and actually spawns.
    monkeypatch.setattr(
        hermes_gateway,
        "find_gateway_pids",
        lambda all_profiles=False: [] if all_profiles else surviving_pids,
    )
    monkeypatch.setattr(gateway_windows, "_spawn_detached", lambda: 4242)
    # Avoid the real 30s/0.4s poll loop in _report_gateway_start.
    monkeypatch.setattr(
        gateway_windows, "_wait_for_gateway_ready", lambda *a, **k: surviving_pids
    )
    monkeypatch.setattr(
        "gateway.status._pid_exists", lambda pid: launcher_alive and pid == 4242
    )

    update_cmd._cold_start_windows_gateway_after_update()

    return capsys.readouterr().out


def test_cold_start_reports_failure_when_process_does_not_survive(monkeypatch, capsys):
    out = _run_cold_start(monkeypatch, capsys, surviving_pids=[])

    assert "✓ Starting Windows gateway after update" not in out
    assert "exited before the gateway became ready" in out


def test_cold_start_reports_success_when_process_survives(monkeypatch, capsys):
    out = _run_cold_start(monkeypatch, capsys, surviving_pids=[4242])

    assert "✓ Gateway started via cold-start after update" in out
    assert out.count("4242") == 1
    assert "gateway PID: 4242" in out


def test_cold_start_labels_different_launcher_and_gateway_pids(monkeypatch, capsys):
    out = _run_cold_start(monkeypatch, capsys, surviving_pids=[5252])

    assert "gateway PID: 5252" in out
    assert "launcher PID: 4242" in out


def test_cold_start_reports_live_launcher_as_still_starting(monkeypatch, capsys):
    out = _run_cold_start(
        monkeypatch, capsys, surviving_pids=[], launcher_alive=True
    )

    assert "still starting" in out
    assert "launcher PID: 4242" in out
    assert "no process detected" not in out


def test_gateway_ready_prefers_control_socket_identity(monkeypatch):
    monkeypatch.setattr(
        "gateway.control_socket.identify_gateway",
        lambda _home, **_kwargs: {"pid": 7777},
    )
    monkeypatch.setattr(
        hermes_gateway, "find_gateway_pids", lambda **_kwargs: []
    )

    assert gateway_windows._wait_for_gateway_ready(
        timeout_s=0.01, interval_s=0
    ) == [7777]


def test_gateway_ready_does_not_treat_process_scan_as_readiness(monkeypatch):
    monkeypatch.setattr(
        "gateway.control_socket.identify_gateway",
        lambda _home, **_kwargs: None,
    )
    monkeypatch.setattr(
        hermes_gateway, "find_gateway_pids", lambda **_kwargs: [4242]
    )

    assert gateway_windows._wait_for_gateway_ready(
        timeout_s=0.01, interval_s=0
    ) == []
