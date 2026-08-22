"""Authoritative backend taxonomy for human-facing session surfaces.

Project navigation is intentionally allowlisted: a new messaging adapter or
system runner must not silently become a project/Home conversation merely
because its source id is new. Search/history APIs remain source-agnostic unless
their own surface applies this policy.
"""

from __future__ import annotations

# Interactive conversations created from local Hermes clients. Empty source is
# retained for legacy rows created before source tagging was consistent.
PROJECT_CONVERSATION_SOURCES: tuple[str, ...] = (
    "",
    "acp",
    "cli",
    "codex",
    "desktop",
    "gateway",
    "local",
    "tui",
    "webui",
)

# Known non-conversation sources. This is exported for tests/documentation and
# for other backend history surfaces that need the same classification. Project
# filtering itself uses PROJECT_CONVERSATION_SOURCES so unknown future sources
# fail closed instead of leaking into Home.
AUTOMATION_SESSION_SOURCES: tuple[str, ...] = (
    "a2a",
    "api_server",
    "cron",
    "kanban",
    "msgraph_webhook",
    "subagent",
    "tool",
    "webhook",
)


def is_project_conversation_source(source: str | None) -> bool:
    """Return whether a session belongs in Projects/Home navigation."""

    return (source or "").strip().lower() in PROJECT_CONVERSATION_SOURCES