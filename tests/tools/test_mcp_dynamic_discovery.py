"""Tests for MCP dynamic tool discovery (notifications/tools/list_changed)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.mcp_tool import MCPServerTask, _register_server_tools
from tools.registry import ToolRegistry


def _make_mcp_tool(name: str, desc: str = ""):
    return SimpleNamespace(name=name, description=desc, inputSchema=None)


class TestRegisterServerTools:
    """Tests for the extracted _register_server_tools helper."""

    @pytest.fixture
    def mock_registry(self):
        return ToolRegistry()

    def test_exposes_live_server_aliases(self, mock_registry):
        """Registered MCP tools are reachable via live raw-server aliases."""
        server = MCPServerTask("my_srv")
        server._tools = [_make_mcp_tool("my_tool", "desc")]
        server.session = MagicMock()
        from toolsets import resolve_toolset, validate_toolset

        with patch("tools.registry.registry", mock_registry):
            registered = _register_server_tools("my_srv", server, {})
            assert "mcp__my_srv__my_tool" in registered
            assert "mcp__my_srv__my_tool" in mock_registry.get_all_tool_names()
            assert validate_toolset("my_srv") is True
            assert "mcp__my_srv__my_tool" in resolve_toolset("my_srv")

    def test_large_catalog_server_defaults_to_catalog_only(self, mock_registry):
        server = MCPServerTask("forge")
        server._tools = [
            _make_mcp_tool("catalog.search", "Discover operations"),
            _make_mcp_tool("catalog.describe", "Describe operations"),
            _make_mcp_tool("catalog.call", "Invoke an operation"),
            *[_make_mcp_tool(f"issues.operation_{i}", "Issue workflow") for i in range(25)],
        ]
        server.session = MagicMock()

        with patch("tools.registry.registry", mock_registry):
            registered = _register_server_tools("forge", server, {})

        catalog_names = set(mock_registry.get_tool_names_for_toolset("mcp-forge"))
        direct_names = set(mock_registry.get_tool_names_for_toolset("mcp-forge-direct"))
        assert len(registered) == 28  # connected/discovered, even when not exposed by default
        assert catalog_names == {
            "mcp__forge__catalog_search",
            "mcp__forge__catalog_describe",
            "mcp__forge__catalog_call",
        }
        assert len(direct_names) == 25
        assert mock_registry.get_toolset_alias_target("forge") == "mcp-forge"
        assert mock_registry.get_toolset_alias_target("forge-direct") == "mcp-forge-direct"

    def test_catalog_exposure_can_be_explicit_for_small_server(self, mock_registry):
        server = MCPServerTask("small")
        server._tools = [
            _make_mcp_tool("catalog.search"),
            _make_mcp_tool("catalog.describe"),
            _make_mcp_tool("catalog.call"),
            _make_mcp_tool("items.list"),
        ]
        server.session = MagicMock()

        with patch("tools.registry.registry", mock_registry):
            _register_server_tools("small", server, {"exposure": "catalog"})

        assert len(mock_registry.get_tool_names_for_toolset("mcp-small")) == 3
        assert len(mock_registry.get_tool_names_for_toolset("mcp-small-direct")) == 1

    def test_direct_exposure_preserves_legacy_single_toolset(self, mock_registry):
        server = MCPServerTask("forge")
        server._tools = [
            _make_mcp_tool("catalog.search"),
            _make_mcp_tool("catalog.describe"),
            _make_mcp_tool("catalog.call"),
            _make_mcp_tool("runs.open"),
        ]
        server.session = MagicMock()

        with patch("tools.registry.registry", mock_registry):
            _register_server_tools("forge", server, {"exposure": "direct"})

        directly_exposed = set(mock_registry.get_tool_names_for_toolset("mcp-forge"))
        assert {
            "mcp__forge__catalog_search",
            "mcp__forge__catalog_describe",
            "mcp__forge__catalog_call",
            "mcp__forge__runs_open",
        }.issubset(directly_exposed)
        assert mock_registry.get_tool_names_for_toolset("mcp-forge-direct") == []

    def test_off_exposure_keeps_connection_but_registers_no_schemas(self, mock_registry):
        server = MCPServerTask("forge")
        server._tools = [_make_mcp_tool("catalog.search"), _make_mcp_tool("runs.open")]
        server.session = MagicMock()

        with patch("tools.registry.registry", mock_registry):
            registered = _register_server_tools("forge", server, {"exposure": "off"})

        assert registered == []
        assert mock_registry.get_all_tool_names() == []

    def test_catalog_profile_reduces_real_model_tool_definitions(self, mock_registry):
        """The provider-facing schema assembly sees only catalog tools by default."""
        import json
        import model_tools

        server = MCPServerTask("forge")
        server._tools = [
            _make_mcp_tool("catalog.search", "Discover operations"),
            _make_mcp_tool("catalog.describe", "Describe operations"),
            _make_mcp_tool("catalog.call", "Invoke an operation"),
            *[
                _make_mcp_tool(
                    f"runs.operation_{i}",
                    "AgentRun mode: EXECUTE | RESEARCH | REVIEW | DISCUSS",
                )
                for i in range(25)
            ],
        ]
        server.session = MagicMock()

        with (
            patch("tools.registry.registry", mock_registry),
            patch("model_tools.registry", mock_registry),
            patch("tools.mcp_tool._make_check_fn", return_value=lambda: True),
        ):
            _register_server_tools("forge", server, {})
            model_tools._clear_tool_defs_cache()
            ordinary = model_tools.get_tool_definitions(
                enabled_toolsets=["forge"], quiet_mode=True
            )
            explicit = model_tools.get_tool_definitions(
                enabled_toolsets=["forge", "forge-direct"], quiet_mode=True
            )

        ordinary_text = json.dumps(ordinary, sort_keys=True)
        explicit_text = json.dumps(explicit, sort_keys=True)
        ordinary_names = {tool["function"]["name"] for tool in ordinary}
        assert ordinary_names == {
            "tool_search",
            "tool_describe",
            "tool_call",
        }
        assert "AgentRun" not in ordinary_text
        assert "EXECUTE" not in ordinary_text
        assert "mcp__forge__runs_operation_0" in explicit_text
        assert len(ordinary_text.encode()) < len(explicit_text.encode())


class TestRefreshTools:
    """Tests for MCPServerTask._refresh_tools nuke-and-repave cycle."""

    @pytest.fixture
    def mock_registry(self):
        return ToolRegistry()

    @pytest.mark.asyncio
    async def test_nuke_and_repave(self, mock_registry):
        """Old tools are removed and new tools registered on refresh."""
        server = MCPServerTask("live_srv")
        server._refresh_lock = asyncio.Lock()
        server._config = {}
        from toolsets import resolve_toolset

        # Seed initial state: one old tool registered
        mock_registry.register(
            name="mcp__live_srv__old_tool", toolset="mcp-live_srv", schema={},
            handler=lambda x: x, check_fn=lambda: True, is_async=False,
            description="", emoji="",
        )
        server._registered_tool_names = ["mcp__live_srv__old_tool"]

        # New tool list from server
        new_tool = _make_mcp_tool("new_tool", "new behavior")
        server.session = SimpleNamespace(
            list_tools=AsyncMock(
                return_value=SimpleNamespace(tools=[new_tool])
            )
        )

        with patch("tools.registry.registry", mock_registry):
            await server._refresh_tools()
            assert "mcp__live_srv__old_tool" not in mock_registry.get_all_tool_names()
            assert "mcp__live_srv__old_tool" not in resolve_toolset("live_srv")
            assert "mcp__live_srv__new_tool" in mock_registry.get_all_tool_names()
            assert "mcp__live_srv__new_tool" in resolve_toolset("live_srv")
            assert server._registered_tool_names == ["mcp__live_srv__new_tool"]


class TestMessageHandler:
    """Tests for MCPServerTask._make_message_handler dispatch."""

    @pytest.mark.asyncio
    async def test_dispatches_tool_list_changed(self):
        from tools.mcp_tool import _MCP_NOTIFICATION_TYPES
        if not _MCP_NOTIFICATION_TYPES:
            pytest.skip("MCP SDK ToolListChangedNotification not available")

        from mcp.types import ServerNotification, ToolListChangedNotification

        server = MCPServerTask("notif_srv")
        # Product now schedules the refresh as a background task (see
        # _schedule_tools_refresh in mcp_tool.py ~L918) rather than awaiting
        # it directly, to avoid wedging the stdio JSON-RPC stream. Patch at
        # the scheduler seam so we can still assert dispatch happened without
        # reaching into asyncio.create_task internals.
        with patch.object(MCPServerTask, "_schedule_tools_refresh") as mock_schedule:
            handler = server._make_message_handler()
            notification = ToolListChangedNotification(
                method="notifications/tools/list_changed"
            )
            if hasattr(ServerNotification, "model_validate"):
                # mcp < 2.0 wrapped notifications in a RootModel; 2.0 made
                # ServerNotification a plain union of the concrete types, which
                # has no constructor to wrap with.
                notification = ServerNotification(root=notification)
            await handler(notification)
            mock_schedule.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_exceptions_and_other_messages(self):
        server = MCPServerTask("notif_srv")
        with patch.object(MCPServerTask, "_schedule_tools_refresh") as mock_schedule:
            handler = server._make_message_handler()
            # Exceptions should not trigger refresh
            await handler(RuntimeError("connection dead"))
            # Unknown message types should not trigger refresh
            await handler({"jsonrpc": "2.0", "result": "ok"})
            mock_schedule.assert_not_called()


class TestDeregister:
    """Tests for ToolRegistry.deregister."""

    def test_removes_tool(self):
        reg = ToolRegistry()
        reg.register(name="foo", toolset="ts1", schema={}, handler=lambda x: x)
        assert "foo" in reg.get_all_tool_names()
        reg.deregister("foo")
        assert "foo" not in reg.get_all_tool_names()


    def test_noop_for_unknown_tool(self):
        reg = ToolRegistry()
        reg.deregister("nonexistent")  # Should not raise
