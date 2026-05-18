"""Regression tests for the Forge platform plugin.

Forge chat dispatch enters Hermes through the generic webhook adapter, then
uses a plugin platform adapter as the outbound delivery target.  These tests
keep that adapter discoverable and verify the no-op semantics for [SILENT]
echo events without touching the live Forge API.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from gateway.platforms.base import PlatformConfig


_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "platforms" / "forge"
_ADAPTER_PATH = _PLUGIN_DIR / "adapter.py"


def _load_adapter_module():
    spec = importlib.util.spec_from_file_location("forge_plugin_adapter_test", _ADAPTER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config() -> PlatformConfig:
    return PlatformConfig(enabled=True, api_key="test-key", token="", extra={"base_url": "https://forge.example.test"})


def test_register_calls_register_platform() -> None:
    module = _load_adapter_module()
    ctx = Mock()

    module.register(ctx)

    ctx.register_platform.assert_called_once()
    kwargs = ctx.register_platform.call_args.kwargs
    assert kwargs["name"] == "forge"
    assert kwargs["label"] == "Forge"
    assert kwargs["required_env"] == ["FORGE_API_KEY"]
    assert callable(kwargs["adapter_factory"])


def test_adapter_uses_configured_base_url_and_chat_info() -> None:
    module = _load_adapter_module()
    adapter = module.ForgeAdapter(_config())

    assert adapter.base_url == "https://forge.example.test"
    assert adapter.rpc_url == "https://forge.example.test/api/mcp/rpc"


@pytest.mark.asyncio
async def test_silent_response_is_treated_as_success_without_network() -> None:
    module = _load_adapter_module()
    adapter = module.ForgeAdapter(_config())
    adapter._call_tool = Mock(side_effect=AssertionError("network should not be called"))

    result = await adapter.send("thread-1", "[SILENT]")

    assert result.success is True
    adapter._call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_send_appends_message_via_forge_mcp_tool() -> None:
    module = _load_adapter_module()
    adapter = module.ForgeAdapter(_config())
    adapter._call_tool = Mock(return_value={"id": "msg-1"})

    result = await adapter.send("thread-1", "hello forge")

    assert result.success is True
    assert result.message_id == "msg-1"
    adapter._call_tool.assert_called_once_with(
        "chat.appendMessage",
        {"threadId": "thread-1", "body": "hello forge"},
    )
