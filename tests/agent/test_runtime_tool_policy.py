from agent.runtime_tool_policy import (
    build_run_tool_policy,
    denied_toolsets_for_allowlist,
    extract_host_tool_allowlist,
    merge_disabled_toolsets,
)


def test_extracts_top_level_or_nested_allowlist():
    assert extract_host_tool_allowlist(["terminal", "filesystem"]) == [
        "terminal",
        "filesystem",
    ]
    assert extract_host_tool_allowlist(
        runtime_policy={"allowed_host_tools": ["git"]}
    ) == ["git"]


def test_empty_allowlist_disables_local_execution_toolsets():
    denied = set(denied_toolsets_for_allowlist([]))
    assert {"terminal", "file", "code_execution", "desktop"} <= denied
    assert "delegation" not in denied


def test_git_maps_to_shell_capable_toolsets():
    denied = set(denied_toolsets_for_allowlist(["git"]))
    assert "terminal" not in denied
    assert "code_execution" not in denied
    assert "file" in denied


def test_desktop_requires_terminal_and_filesystem():
    assert "desktop" in denied_toolsets_for_allowlist(["terminal"])
    assert "desktop" in denied_toolsets_for_allowlist(["filesystem"])
    assert "desktop" not in denied_toolsets_for_allowlist(
        ["terminal", "filesystem"]
    )


def test_build_policy_summary_is_serializable():
    policy = build_run_tool_policy(
        tool_allowlist=[],
        runtime_policy={"allowed_host_tools": [], "contract_version": "test"},
    )
    assert policy["provided"] is True
    assert policy["allowed_host_tools"] == []
    assert "terminal" in policy["denied_toolsets"]
    assert policy["runtime_policy"]["contract_version"] == "test"


def test_merge_disabled_toolsets_preserves_order_and_dedupes():
    assert merge_disabled_toolsets(["memory", "file"], ["file", "terminal"]) == [
        "memory",
        "file",
        "terminal",
    ]
