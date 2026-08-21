"""Behavioral source-policy tests for Projects/Home navigation."""

from __future__ import annotations

from hermes_cli.session_source_policy import (
    AUTOMATION_SESSION_SOURCES,
    PROJECT_CONVERSATION_SOURCES,
    is_project_conversation_source,
)
from tui_gateway import server


class _SessionDB:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.kwargs: dict | None = None

    def list_sessions_rich(self, **kwargs):
        self.kwargs = kwargs
        allowed = set(kwargs["sources"])
        return [row for row in self.rows if (row.get("source") or "") in allowed]


def _row(session_id: str, source: str, cwd: str = "") -> dict:
    return {
        "id": session_id,
        "source": source,
        "cwd": cwd,
        "message_count": 1,
        "started_at": 1,
        "last_active": 1,
    }


def test_project_tree_admits_local_conversations_and_rejects_other_surfaces():
    rows = [
        _row("desktop-project", "desktop", "/repo"),
        _row("local-home", "cli"),
        _row("legacy-home", ""),
        _row("discord", "discord"),
        _row("telegram", "telegram"),
        _row("a2a", "a2a"),
        _row("webhook", "webhook"),
        _row("api", "api_server"),
        _row("cron", "cron"),
        _row("kanban", "kanban"),
        _row("subagent", "subagent"),
        _row("unknown-system", "future_runner"),
    ]
    db = _SessionDB(rows)

    listed = server._list_project_tree_sessions(db)

    assert [row["id"] for row in listed] == ["desktop-project", "local-home", "legacy-home"]
    assert db.kwargs is not None
    assert set(db.kwargs["sources"]) == set(PROJECT_CONVERSATION_SOURCES)
    assert "exclude_sources" not in db.kwargs
    assert db.kwargs["limit"] == -1
    assert db.kwargs["offset"] == 0
    assert db.kwargs["compact_rows"] is True


def test_project_source_policy_normalizes_and_fails_closed():
    assert is_project_conversation_source(" DESKTOP ")
    assert is_project_conversation_source(None)  # legacy untagged human chat
    assert not is_project_conversation_source("discord")
    assert not is_project_conversation_source("a2a")
    assert not is_project_conversation_source("future_runner")
    assert {"a2a", "api_server", "cron", "kanban", "subagent", "webhook"}.issubset(
        AUTOMATION_SESSION_SOURCES
    )
