"""Focused contract tests for the fork-owned Lucid/neural memory provider."""

from __future__ import annotations

import json
import sys
import types


def test_plugin_import_and_registration():
    from plugins.memory.neural import NeuralMemoryProvider, register

    registered = []
    context = types.SimpleNamespace(register_memory_provider=registered.append)

    register(context)

    assert len(registered) == 1
    assert isinstance(registered[0], NeuralMemoryProvider)
    assert registered[0].name == "neural"
    assert {schema["name"] for schema in registered[0].get_tool_schemas()} == {
        "neural_remember",
        "neural_recall",
        "neural_think",
        "neural_graph",
    }


def test_prefetch_recall_does_not_touch_access_counts():
    from plugins.memory.neural import NeuralMemoryProvider

    calls = []

    class FakeMemory:
        def recall(self, query, *, k, touch):
            calls.append((query, k, touch))
            return [{"content": "Bailey prefers concise carry reports", "similarity": 0.91}]

    provider = NeuralMemoryProvider()
    provider._memory = FakeMemory()
    provider._config = {"prefetch_limit": 2}

    provider.queue_prefetch("report preference")
    provider._prefetch_thread.join(timeout=2)

    assert calls == [("report preference", 4, False)]
    assert "Bailey prefers concise carry reports" in provider.prefetch("next turn")


def test_sqlite_client_round_trip_and_touch_control(tmp_path, monkeypatch):
    embed_provider = types.ModuleType("embed_provider")

    class FakeEmbeddingProvider:
        dim = 2
        backend = object()

        def __init__(self, backend="auto"):
            self.backend_name = backend

        def embed(self, text):
            return [1.0, 0.0] if "alpha" in text.lower() else [0.0, 1.0]

    embed_provider.EmbeddingProvider = FakeEmbeddingProvider
    monkeypatch.setitem(sys.modules, "embed_provider", embed_provider)

    from plugins.memory.neural.memory_client import NeuralMemory

    memory = NeuralMemory(db_path=tmp_path / "memory.db", use_cpp=False)
    try:
        memory_id = memory.remember(
            "Alpha is the selected project",
            label="project",
            detect_conflicts=False,
        )

        untouched = memory.recall("alpha", k=1, temporal_weight=0, touch=False)
        assert untouched[0]["id"] == memory_id
        assert memory.store.get(memory_id)["access_count"] == 0

        touched = memory.recall("alpha", k=1, temporal_weight=0, touch=True)
        assert touched[0]["content"] == "Alpha is the selected project"
        assert memory.store.get(memory_id)["access_count"] == 1
    finally:
        memory.close()


def test_explicit_tool_calls_use_local_memory_api():
    from plugins.memory.neural import NeuralMemoryProvider

    class FakeMemory:
        def remember(self, content, *, label):
            assert (content, label) == ("Keep this decision", "decision")
            return 41

        def recall(self, query, *, k):
            assert (query, k) == ("decision", 2)
            return [{"id": 41, "content": "Keep this decision"}]

    provider = NeuralMemoryProvider()
    provider._memory = FakeMemory()

    stored = json.loads(
        provider.handle_tool_call(
            "neural_remember", {"content": "Keep this decision", "label": "decision"}
        )
    )
    recalled = json.loads(
        provider.handle_tool_call("neural_recall", {"query": "decision", "limit": 2})
    )

    assert stored == {"id": 41, "status": "stored"}
    assert recalled == {
        "results": [{"id": 41, "content": "Keep this decision"}],
        "count": 1,
    }
