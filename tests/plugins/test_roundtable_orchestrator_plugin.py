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


class TestRoundtableCall:
    @pytest.mark.asyncio
    async def test_call_sends_single_fire_discord_mention_with_precise_allowed_mentions(
        self, roundtable_mod, monkeypatch
    ):
        monkeypatch.setenv("HERMES_ROUNDTABLE_CHANNELS", "roundtable")

        sent = []

        class Adapter:
            async def send(self, chat_id, content, metadata=None):
                sent.append((chat_id, content, metadata))
                return SimpleNamespace(success=True, message_id="msg-123")

        event = _event(is_bot=False, chat_id="roundtable")
        gateway = SimpleNamespace(
            adapters={"discord": Adapter()},
            config={
                "discord": {
                    "roundtable": {
                        "agents": {"mizu": "1489797340448428202"},
                    }
                }
            },
        )

        result = roundtable_mod._handle_roundtable_command(
            "call mizu Please give product perspective.",
            session_id="sess1",
            gateway=gateway,
            event=event,
        )
        if hasattr(result, "__await__"):
            result = await result

        assert result == "Called mizu in <#roundtable> (message msg-123)."
        assert sent == [
            (
                "roundtable",
                "<@1489797340448428202> Please give product perspective.",
                {
                    "allowed_mentions_user_ids": ["1489797340448428202"],
                    "allow_roundtable_bot_mentions": True,
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_call_rejects_non_roundtable_channel(self, roundtable_mod, monkeypatch):
        monkeypatch.setenv("HERMES_ROUNDTABLE_CHANNELS", "roundtable")
        event = _event(is_bot=False, chat_id="elsewhere")
        gateway = SimpleNamespace(
            adapters={},
            config={"discord": {"roundtable": {"agents": {"mizu": "1489797340448428202"}}}},
        )

        result = roundtable_mod._handle_roundtable_command(
            "call mizu hello", gateway=gateway, event=event
        )
        if hasattr(result, "__await__"):
            result = await result

        assert "only available in configured roundtable channels" in result


class TestRoundtableDebate:
    @pytest.mark.asyncio
    async def test_debate_starts_bounded_turns_and_mentions_first_agent(self, roundtable_mod, monkeypatch):
        monkeypatch.setenv("HERMES_ROUNDTABLE_CHANNELS", "roundtable")
        sent = []

        class Adapter:
            async def send(self, chat_id, content, metadata=None):
                sent.append((chat_id, content, metadata))
                return SimpleNamespace(success=True, message_id=f"msg-{len(sent)}")

        event = _event(is_bot=False, chat_id="roundtable")
        gateway = SimpleNamespace(
            adapters={"discord": Adapter()},
            config={
                "discord": {
                    "roundtable": {
                        "agents": {
                            "victor": "111",
                            "mizu": "222",
                        },
                    }
                }
            },
        )

        result = roundtable_mod._handle_roundtable_command(
            "debate victor,mizu --rounds 2 Should we ship this?",
            session_id="sess1",
            gateway=gateway,
            event=event,
        )
        if hasattr(result, "__await__"):
            result = await result

        assert "Debate started" in result
        assert "victor → mizu" in result
        assert sent[0][0] == "roundtable"
        assert sent[0][1].startswith("<@111> Roundtable debate")
        assert "Should we ship this?" in sent[0][1]
        assert sent[0][2]["allowed_mentions_user_ids"] == ["111"]
        assert sent[0][2]["allow_roundtable_bot_mentions"] is True
        debate = roundtable_mod._read_state()["debate"]
        assert debate["active"] is True
        assert debate["participants"] == ["victor", "mizu"]
        assert debate["rounds"] == 2
        assert debate["turn_index"] == 0

    @pytest.mark.asyncio
    async def test_debate_routes_next_turn_after_expected_agent_response(self, roundtable_mod, monkeypatch):
        monkeypatch.setenv("HERMES_ROUNDTABLE_CHANNELS", "roundtable")
        sent = []

        class Adapter:
            async def send(self, chat_id, content, metadata=None):
                sent.append((chat_id, content, metadata))
                return SimpleNamespace(success=True, message_id=f"msg-{len(sent)}")

        gateway = SimpleNamespace(adapters={"discord": Adapter()}, config={})
        roundtable_mod._write_state(True, reason="test")
        state = roundtable_mod._read_state()
        state["debate"] = {
            "id": "debate-1",
            "active": True,
            "channel_id": "roundtable",
            "participants": ["victor", "mizu"],
            "agent_ids": {"victor": "111", "mizu": "222"},
            "topic": "Ship it?",
            "rounds": 1,
            "turn_index": 0,
            "transcript": [],
            "started_at": "now",
        }
        roundtable_mod._write_full_state(state)

        result = roundtable_mod._post_gateway_send(
            platform="discord",
            chat_id="roundtable",
            content="Engineering view: yes, with tests. ROUND_TABLE_DECISION: CONTINUE",
            sender_bot_id="111",
            message_id="victor-msg",
            gateway=gateway,
        )
        if hasattr(result, "__await__"):
            await result

        debate = roundtable_mod._read_state()["debate"]
        assert debate["turn_index"] == 1
        assert debate["transcript"][0]["agent"] == "victor"
        assert len(sent) == 1
        assert sent[0][0] == "roundtable"
        assert "<@222> Roundtable debate" in sent[0][1]
        assert sent[0][2] == {
            "allowed_mentions_user_ids": ["222"],
            "allow_roundtable_bot_mentions": True,
            "roundtable_debate_id": "debate-1",
        }

    @pytest.mark.asyncio
    async def test_debate_consensus_marker_stops_and_announces_summary(self, roundtable_mod):
        sent = []

        class Adapter:
            async def send(self, chat_id, content, metadata=None):
                sent.append((chat_id, content, metadata))
                return SimpleNamespace(success=True, message_id=f"msg-{len(sent)}")

        gateway = SimpleNamespace(adapters={"discord": Adapter()}, config={})
        roundtable_mod._write_state(True, reason="test")
        state = roundtable_mod._read_state()
        state["debate"] = {
            "id": "debate-1",
            "active": True,
            "channel_id": "roundtable",
            "participants": ["victor", "mizu"],
            "agent_ids": {"victor": "111", "mizu": "222"},
            "topic": "Ship it?",
            "rounds": 2,
            "turn_index": 1,
            "transcript": [{"agent": "victor", "content": "yes with tests"}],
            "started_at": "now",
        }
        roundtable_mod._write_full_state(state)

        result = roundtable_mod._post_gateway_send(
            platform="discord",
            chat_id="roundtable",
            content="Product agrees. ROUND_TABLE_DECISION: CONSENSUS",
            sender_bot_id="222",
            message_id="mizu-msg",
            gateway=gateway,
        )
        if hasattr(result, "__await__"):
            await result

        debate = roundtable_mod._read_state()["debate"]
        assert debate["active"] is False
        assert debate["stop_reason"] == "consensus"
        assert len(sent) == 1
        assert sent[0][0] == "roundtable"
        assert "Consensus reached" in sent[0][1]
        assert sent[0][2] == {"allow_roundtable_bot_mentions": False, "roundtable_debate_id": "debate-1"}


class TestRegistration:
    def test_register_wires_hook_and_gateway_command(self, roundtable_mod):
        calls = []

        class Ctx:
            def register_hook(self, *args, **kwargs):
                calls.append(("hook", args, kwargs))

            def register_command(self, *args, **kwargs):
                calls.append(("command", args, kwargs))

        roundtable_mod.register(Ctx())
        hooks = [call for call in calls if call[0] == "hook"]
        command = next(call for call in calls if call[0] == "command")
        assert [hook[1][0] for hook in hooks] == ["pre_gateway_dispatch", "post_gateway_send"]
        assert command[1][0] == "roundtable"
        assert command[2]["gateway_only"] is True
        assert command[2]["args_hint"] == "<status|stop|start|call|debate> [args]"
