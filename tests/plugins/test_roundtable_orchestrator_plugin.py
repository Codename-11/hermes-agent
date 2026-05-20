"""Tests for the bundled roundtable-orchestrator plugin."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def roundtable_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ROUNDTABLE_STATE", str(tmp_path / "roundtable_state.json"))
    monkeypatch.delenv("HERMES_ROUNDTABLE_CHANNELS", raising=False)
    monkeypatch.delenv("DISCORD_ROUNDTABLE_CHANNELS", raising=False)
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "roundtable_orchestrator"
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    module_name = "hermes_plugins.roundtable_orchestrator_under_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = module_name
    mod.__path__ = [str(plugin_dir)]
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _event(*, is_bot=True, platform="discord", chat_id="roundtable", parent_chat_id=None):
    source = SimpleNamespace(
        platform=platform,
        is_bot=is_bot,
        chat_id=chat_id,
        parent_chat_id=parent_chat_id,
        thread_id=None,
    )
    return SimpleNamespace(source=source, text="hello")


class TestRoundtableState:
    def test_defaults_fail_closed(self, roundtable_mod):
        state = roundtable_mod._read_state()
        assert state["enabled"] is False
        assert "stopped" in roundtable_mod._format_status(state)

    def test_command_stop_and_start_toggle_shared_state(self, roundtable_mod):
        stopped = roundtable_mod._handle_roundtable_command("stop human said stop", session_id="sess1")
        assert stopped.startswith("Stopped.")
        assert roundtable_mod._read_state()["enabled"] is False
        assert roundtable_mod._read_state()["reason"] == "human said stop"

        started = roundtable_mod._handle_roundtable_command("start controlled test", session_id="sess1")
        assert started.startswith("Roundtable enabled.")
        assert roundtable_mod._read_state()["enabled"] is True
        assert roundtable_mod._read_state()["reason"] == "controlled test"

    def test_unknown_subcommand_returns_help(self, roundtable_mod):
        response = roundtable_mod._handle_roundtable_command("dance")
        assert "Unknown roundtable subcommand" in response
        assert "Usage: /roundtable" in response


class TestRoundtableGate:
    def test_stopped_state_skips_discord_bot_event(self, roundtable_mod):
        roundtable_mod._write_state(False, reason="test-stop")
        result = roundtable_mod._pre_gateway_dispatch(event=_event())
        assert result == {"action": "skip", "reason": "roundtable-stopped"}

    def test_enabled_state_allows_discord_bot_event(self, roundtable_mod):
        roundtable_mod._write_state(True, reason="test-start")
        assert roundtable_mod._pre_gateway_dispatch(event=_event()) is None

    def test_human_events_are_never_blocked(self, roundtable_mod):
        roundtable_mod._write_state(False, reason="test-stop")
        assert roundtable_mod._pre_gateway_dispatch(event=_event(is_bot=False)) is None

    def test_non_discord_bot_events_are_never_blocked(self, roundtable_mod):
        roundtable_mod._write_state(False, reason="test-stop")
        assert roundtable_mod._pre_gateway_dispatch(event=_event(platform="slack")) is None

    def test_channel_filter_limits_circuit_breaker(self, roundtable_mod, monkeypatch):
        monkeypatch.setenv("HERMES_ROUNDTABLE_CHANNELS", "roundtable,parent")
        roundtable_mod._write_state(False, reason="test-stop")
        assert roundtable_mod._pre_gateway_dispatch(event=_event(chat_id="other")) is None
        assert roundtable_mod._pre_gateway_dispatch(event=_event(chat_id="roundtable")) == {
            "action": "skip",
            "reason": "roundtable-stopped",
        }
        assert roundtable_mod._pre_gateway_dispatch(
            event=_event(chat_id="thread", parent_chat_id="parent")
        ) == {"action": "skip", "reason": "roundtable-stopped"}


class TestRegistration:
    def test_register_wires_hook_and_gateway_command(self, roundtable_mod):
        calls = []

        class Ctx:
            def register_hook(self, *args, **kwargs):
                calls.append(("hook", args, kwargs))

            def register_command(self, *args, **kwargs):
                calls.append(("command", args, kwargs))

        roundtable_mod.register(Ctx())
        hook = next(call for call in calls if call[0] == "hook")
        command = next(call for call in calls if call[0] == "command")
        assert hook[1][0] == "pre_gateway_dispatch"
        assert command[1][0] == "roundtable"
        assert command[2]["gateway_only"] is True
        assert command[2]["args_hint"] == "<status|stop|start> [reason]"
