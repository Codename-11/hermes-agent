from types import SimpleNamespace


def _tool_def(required):
    return {
        "type": "function",
        "function": {
            "name": "mcp_glasstrax_bridge_flip_order_to_quote",
            "description": "Convert an order to a quote",
            "parameters": {
                "type": "object",
                "properties": {
                    "so_no": {"type": "integer"},
                    "quote_no": {"type": "integer", "default": None},
                },
                "required": required,
            },
        },
    }


def test_live_agent_tool_list_refreshes_when_registry_generation_changes(monkeypatch):
    """Long-lived agents should not keep stale MCP required-arg schemas."""
    from agent import chat_completion_helpers as helpers
    from tools.registry import registry

    stale_tool = _tool_def(["so_no", "quote_no"])
    fresh_tool = _tool_def(["so_no"])
    original_generation = registry._generation

    agent = SimpleNamespace(
        tools=[stale_tool],
        valid_tool_names={"mcp_glasstrax_bridge_flip_order_to_quote"},
        enabled_toolsets=["mcp-glasstrax_bridge"],
        disabled_toolsets=None,
        _memory_manager=None,
        _tool_registry_generation=original_generation,
        log_prefix="",
    )

    monkeypatch.setattr(
        helpers,
        "_ra",
        lambda: SimpleNamespace(get_tool_definitions=lambda **_kwargs: [fresh_tool]),
    )

    try:
        registry._generation = original_generation + 1
        helpers._refresh_agent_tools_if_registry_changed(agent)
    finally:
        registry._generation = original_generation

    params = agent.tools[0]["function"]["parameters"]
    assert params["required"] == ["so_no"]
    assert "quote_no" not in params["required"]
    assert agent.valid_tool_names == {"mcp_glasstrax_bridge_flip_order_to_quote"}
    assert agent._tool_registry_generation == original_generation + 1
