"""Per-run host tool policy helpers.

External control planes such as Forge can send a coarse host-tool allowlist
with a structured run request. Hermes still owns the actual tool registry, so
the gateway must translate that allowlist into Hermes toolsets and enforce it
when building model schemas and when dispatching tool calls.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set


TERMINAL_EQUIVALENT_TOOLSETS = {
    "terminal",
    "code_execution",
}

FILE_EQUIVALENT_TOOLSETS = {"file"}

MIXED_LOCAL_TOOLSETS = {
    # Hermes Relay desktop tools mix local file, process, and shell
    # capabilities under one toolset. Disable the whole surface whenever a
    # run is missing either local terminal or local filesystem permission.
    "desktop",
}

HOST_TOOL_TO_TOOLSETS: Dict[str, Set[str]] = {
    "terminal": TERMINAL_EQUIVALENT_TOOLSETS,
    "shell": TERMINAL_EQUIVALENT_TOOLSETS,
    "git": TERMINAL_EQUIVALENT_TOOLSETS,
    "filesystem": FILE_EQUIVALENT_TOOLSETS,
    "file": FILE_EQUIVALENT_TOOLSETS,
    "files": FILE_EQUIVALENT_TOOLSETS,
}

ENFORCED_HOST_TOOLSETS = (
    TERMINAL_EQUIVALENT_TOOLSETS
    | FILE_EQUIVALENT_TOOLSETS
    | MIXED_LOCAL_TOOLSETS
)


def _as_list(value: Any) -> Optional[List[Any]]:
    if value is None:
        return None
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return None


def extract_host_tool_allowlist(
    tool_allowlist: Any = None,
    runtime_policy: Any = None,
) -> Optional[List[str]]:
    """Return normalized host-tool names, or None when no policy was supplied."""
    raw = _as_list(tool_allowlist)
    if raw is None and isinstance(runtime_policy, dict):
        raw = _as_list(
            runtime_policy.get("allowed_host_tools")
            or runtime_policy.get("allowedHostTools")
        )
    if raw is None:
        return None

    normalized: List[str] = []
    seen: Set[str] = set()
    for item in raw:
        name = str(item or "").strip().lower()
        if not name:
            continue
        if name == "*":
            return ["*"]
        if name == "fs":
            name = "filesystem"
        if name not in seen:
            normalized.append(name)
            seen.add(name)
    return normalized


def denied_toolsets_for_allowlist(allowlist: Optional[Iterable[str]]) -> List[str]:
    """Translate a host-tool allowlist into Hermes toolsets to disable."""
    if allowlist is None:
        return []

    allowed_names = {str(item).strip().lower() for item in allowlist if str(item).strip()}
    if "*" in allowed_names:
        return []

    allowed_toolsets: Set[str] = set()
    for name in allowed_names:
        allowed_toolsets.update(HOST_TOOL_TO_TOOLSETS.get(name, set()))
    terminal_allowed = bool({"terminal", "shell", "git"} & allowed_names)
    filesystem_allowed = bool({"filesystem", "file", "files"} & allowed_names)
    if terminal_allowed and filesystem_allowed:
        allowed_toolsets.update(MIXED_LOCAL_TOOLSETS)

    return sorted(ENFORCED_HOST_TOOLSETS - allowed_toolsets)


def merge_disabled_toolsets(
    base: Optional[Iterable[str]],
    extra: Optional[Iterable[str]],
) -> Optional[List[str]]:
    """Merge disabled toolset lists while preserving stable order."""
    merged: List[str] = []
    seen: Set[str] = set()
    for values in (base, extra):
        for item in values or []:
            name = str(item or "").strip()
            if not name or name in seen:
                continue
            merged.append(name)
            seen.add(name)
    return merged or None


def build_run_tool_policy(
    *,
    tool_allowlist: Any = None,
    runtime_policy: Any = None,
) -> Dict[str, Any]:
    """Build a serializable policy summary for a structured run."""
    allowlist = extract_host_tool_allowlist(tool_allowlist, runtime_policy)
    denied_toolsets = denied_toolsets_for_allowlist(allowlist)
    return {
        "provided": allowlist is not None,
        "allowed_host_tools": allowlist,
        "denied_toolsets": denied_toolsets,
        "runtime_policy": runtime_policy if isinstance(runtime_policy, dict) else None,
    }
