"""Gateway-level tests for the roundtable orchestrator plugin."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


@pytest.fixture()
def installed_roundtable_plugin(tmp_path, monkeypatch):
    """Install the bundled roundtable plugin into the global plugin manager.

    GatewayRunner dispatch uses the module-level hermes_cli.plugins singleton, so
    tests replace it with an isolated manager and restore both it and the command
    registry afterward.
    """
    monkeypatch.setenv("HERMES_ROUNDTABLE_STATE", str(tmp_path / "roundtable_state.json"))
    monkeypatch.delenv("HERMES_ROUNDTABLE_CHANNELS", raising=False)
    monkeypatch.delenv("DISCORD_ROUNDTABLE_CHANNELS", raising=False)

    from hermes_cli import commands as commands_mod
    from hermes_cli import plugins as plugins_mod

    command_snapshot = list(commands_mod.COMMAND_REGISTRY)
    old_manager = plugins_mod._plugin_manager

    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "roundtable_orchestrator"
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    module_name = "hermes_plugins.roundtable_orchestrator_dispatch_under_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = module_name
    mod.__path__ = [str(plugin_dir)]
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)

    manager = PluginManager()
    manager._discovered = True  # prevent unrelated local plugins from loading
    manifest = PluginManifest(name="roundtable-orchestrator", source="bundled")
    ctx = PluginContext(manifest, manager)
    mod.register(ctx)
    plugins_mod._plugin_manager = manager

    try:
        yield mod, manager
    finally:
        plugins_mod._plugin_manager = old_manager
        commands_mod.COMMAND_REGISTRY[:] = command_snapshot
        commands_mod.rebuild_lookups()
        sys.modules.pop(module_name, None)


class _Hooks:
    async def emit_collect(self, _name, _ctx):
        return []


class _SessionStore:
    def __init__(self):
        self.calls = []

    def get_or_create_session(self, source):
        self.calls.append(source)
        return SimpleNamespace(session_id="session-123")


def _source(*, is_bot=False, chat_id="1506727103587029082", parent_chat_id=None, user_id="human-1"):
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id=chat_id,
        chat_type="channel",
        user_id=user_id,
        user_name="sender",
        is_bot=is_bot,
        parent_chat_id=parent_chat_id,
    )


def _event(text, *, is_bot=False, chat_id="1506727103587029082", parent_chat_id=None, internal=False):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_source(is_bot=is_bot, chat_id=chat_id, parent_chat_id=parent_chat_id),
        message_id="msg-1",
        internal=internal,
    )


def _runner():
    runner = cast(Any, object.__new__(GatewayRunner))
    runner.session_store = _SessionStore()
    runner.config = {"quick_commands": {}}
    runner.hooks = _Hooks()
    runner.adapters = {}
    runner.pairing_store = SimpleNamespace()
    runner._update_prompt_pending = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._queued_events = {}
    runner._draining = False
    runner._busy_input_mode = "interrupt"
    runner._session_run_generations = {}
    runner._check_slash_access = lambda source, canonical_cmd: None
    runner._is_user_authorized = lambda source: True
    runner._get_unauthorized_dm_behavior = lambda platform: "ignore"
    runner._is_telegram_topic_root_lobby = lambda source: False
    runner._begin_session_run_generation = lambda session_key: 1
    runner._release_running_agent_state = lambda session_key, **kwargs: runner._running_agents.pop(session_key, None)

    async def _no_goal_continuation(**kwargs):
        return None

    runner._post_turn_goal_continuation = _no_goal_continuation
    return runner


@pytest.mark.asyncio
async def test_roundtable_command_dispatch_stops_shared_state(installed_roundtable_plugin):
    """Regression: /roundtable stop must be handled as a plugin gateway command."""
    roundtable_mod, _manager = installed_roundtable_plugin
    roundtable_mod._write_state(True, reason="before-command")
    runner = _runner()

    response = await runner._handle_message(_event("/roundtable stop live-loop"))

    assert response is not None
    assert response.startswith("Stopped.")
    assert "live-loop" in response
    state = roundtable_mod._read_state()
    assert state["enabled"] is False
    assert state["reason"] == "live-loop"
    assert state["updated_from"] == "session:session-123"
    assert len(runner.session_store.calls) == 1


@pytest.mark.asyncio
async def test_roundtable_pre_dispatch_skip_blocks_bot_turn_before_auth_or_agent(
    installed_roundtable_plugin,
):
    """Stopped roundtable must drop admitted Discord bot messages before LLM dispatch."""
    roundtable_mod, _manager = installed_roundtable_plugin
    roundtable_mod._write_state(False, reason="circuit-breaker")
    runner = _runner()
    runner._is_user_authorized = lambda source: (_ for _ in ()).throw(AssertionError("auth should not run"))
    runner._handle_message_with_agent = lambda event, source, quick_key, run_generation: (_ for _ in ()).throw(AssertionError("agent should not run"))

    response = await runner._handle_message(_event("<@victor> ping", is_bot=True))

    assert response is None
    assert runner.session_store.calls == []


@pytest.mark.asyncio
async def test_roundtable_pre_dispatch_allows_human_stop_while_stopped(installed_roundtable_plugin):
    """The circuit breaker blocks bot turns only; humans can always issue /roundtable stop/status."""
    roundtable_mod, _manager = installed_roundtable_plugin
    roundtable_mod._write_state(False, reason="already-stopped")
    runner = _runner()

    response = await runner._handle_message(_event("/roundtable status", is_bot=False))

    assert response is not None
    assert "Roundtable is **stopped**" in response
    assert len(runner.session_store.calls) == 1


@pytest.mark.asyncio
async def test_roundtable_pre_dispatch_channel_scope_skips_only_configured_room(
    installed_roundtable_plugin,
    monkeypatch,
):
    """Channel scoping keeps the emergency brake focused on the roundtable room."""
    roundtable_mod, _manager = installed_roundtable_plugin
    monkeypatch.setenv("HERMES_ROUNDTABLE_CHANNELS", "1506727103587029082")
    roundtable_mod._write_state(False, reason="roundtable-only")
    runner = _runner()
    agent_calls = []

    async def fake_agent(event, source, quick_key, run_generation):
        agent_calls.append(event)
        return "agent response"

    runner._handle_message_with_agent = fake_agent

    blocked = await runner._handle_message(
        _event("bot mention in roundtable", is_bot=True, chat_id="1506727103587029082")
    )
    allowed = await runner._handle_message(
        _event("bot mention elsewhere", is_bot=True, chat_id="other-channel")
    )

    assert blocked is None
    assert allowed == "agent response"
    assert [call.source.chat_id for call in agent_calls] == ["other-channel"]


@pytest.mark.asyncio
async def test_roundtable_pre_dispatch_does_not_block_internal_events(installed_roundtable_plugin):
    """Internal gateway notifications bypass pre-dispatch hooks like other system events."""
    roundtable_mod, _manager = installed_roundtable_plugin
    roundtable_mod._write_state(False, reason="stopped")
    runner = _runner()
    agent_calls = []

    async def fake_agent(event, source, quick_key, run_generation):
        agent_calls.append(event)
        return "internal response"

    runner._handle_message_with_agent = fake_agent

    response = await runner._handle_message(_event("synthetic", is_bot=True, internal=True))

    assert response == "internal response"
    assert len(agent_calls) == 1
