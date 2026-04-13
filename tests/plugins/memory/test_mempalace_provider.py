import json
import sys
import types
from pathlib import Path

import pytest

from plugins.memory.mempalace import MempalaceMemoryProvider
from plugins.memory.mempalace import cli as mempalace_cli


class FakeMemoryStack:
    init_calls = []
    last_recall = None
    last_search = None

    def __init__(self, palace_path=None, identity_path=None):
        self.__class__.init_calls.append((palace_path, identity_path))
        self.l3 = types.SimpleNamespace(search_raw=self.search_raw)

    def recall(self, wing=None, n_results=8):
        self.__class__.last_recall = (wing, n_results)
        return f"recalled:{wing}"

    def search(self, query, wing=None, room=None, n_results=5):
        self.__class__.last_search = (query, wing, room, n_results)
        return f"searched:{query}:{wing}:{room}:{n_results}"

    def search_raw(self, query, wing=None, room=None, n_results=5):
        self.__class__.last_search = (query, wing, room, n_results)
        return []

    def status(self):
        return {"total_drawers": 1, "L0_identity": {"exists": True, "tokens": 7}}


class FakeKnowledgeGraph:
    init_calls = []

    def __init__(self, db_path=None):
        self.__class__.init_calls.append(db_path)

    def stats(self):
        return {"entities": 0, "current_facts": 0, "expired_facts": 0}

    def add_triple(self, *args, **kwargs):
        return None

    def query_entity(self, *args, **kwargs):
        return []

    def timeline(self, *args, **kwargs):
        return []

    def close(self):
        return None


class FakeConfig:
    def __init__(self):
        self.palace_path = "/default/palace"


@pytest.fixture()
def fake_mempalace(monkeypatch):
    FakeMemoryStack.init_calls = []
    FakeMemoryStack.last_recall = None
    FakeMemoryStack.last_search = None
    FakeKnowledgeGraph.init_calls = []

    pkg = types.ModuleType("mempalace")
    layers = types.ModuleType("mempalace.layers")
    kg = types.ModuleType("mempalace.knowledge_graph")
    config = types.ModuleType("mempalace.config")

    layers.MemoryStack = FakeMemoryStack
    kg.KnowledgeGraph = FakeKnowledgeGraph
    config.MempalaceConfig = FakeConfig

    monkeypatch.setitem(sys.modules, "mempalace", pkg)
    monkeypatch.setitem(sys.modules, "mempalace.layers", layers)
    monkeypatch.setitem(sys.modules, "mempalace.knowledge_graph", kg)
    monkeypatch.setitem(sys.modules, "mempalace.config", config)
    yield


class TestMempalaceProvider:
    def test_initialize_scopes_kg_to_palace_parent(self, tmp_path, fake_mempalace):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        palace_dir = tmp_path / "custom-palace" / "palace"
        palace_dir.parent.mkdir(parents=True)
        (hermes_home / "mempalace.json").write_text(json.dumps({
            "palace_path": str(palace_dir),
            "identity_path": str(tmp_path / "custom-palace" / "identity.txt"),
        }))

        provider = MempalaceMemoryProvider()
        provider.initialize("sess-1", hermes_home=str(hermes_home), agent_identity="victor")
        provider._get_kg()

        assert FakeKnowledgeGraph.init_calls == [str(palace_dir.parent / "knowledge_graph.sqlite3")]

    def test_search_tool_normalizes_legacy_wing_alias(self, tmp_path, fake_mempalace):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        provider = MempalaceMemoryProvider()
        provider.initialize("sess-1", hermes_home=str(hermes_home), agent_identity="victor")
        raw = provider.handle_tool_call("mempalace_search", {"query": "server", "wing": "wing_system"})
        payload = json.loads(raw)

        assert payload["count"] == 0
        assert FakeMemoryStack.last_search == ("server", "system", None, 5)

    def test_prefetch_does_not_match_me_inside_memory(self, tmp_path, fake_mempalace):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        provider = MempalaceMemoryProvider()
        provider.initialize("sess-1", hermes_home=str(hermes_home), agent_identity="victor")
        result = provider.prefetch("show memory layout")

        assert result == "searched:show memory layout:None:None:5"
        assert FakeMemoryStack.last_recall is None
        assert FakeMemoryStack.last_search == ("show memory layout", None, None, 5)

    def test_self_reference_prefetch_uses_active_profile_wing(self, tmp_path, fake_mempalace):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        provider = MempalaceMemoryProvider()
        provider.initialize("sess-1", hermes_home=str(hermes_home), agent_identity="builder")
        result = provider.prefetch("tell me about me")

        assert result == "recalled:wing_builder"
        assert FakeMemoryStack.last_recall == ("wing_builder", 8)

    def test_session_summary_redacts_obvious_secrets(self):
        provider = MempalaceMemoryProvider()
        summary = provider._summarize_messages([
            {"role": "user", "content": "API_KEY=sk-super-secret-value-123456"},
            {"role": "assistant", "content": "Got it"},
        ])
        assert "sk-super-secret-value-123456" not in summary
        assert "<redacted>" in summary

    def test_session_summary_redacts_natural_language_passwords(self):
        provider = MempalaceMemoryProvider()
        summary = provider._summarize_messages([
            {"role": "user", "content": "my password is hunter2"},
            {"role": "assistant", "content": "Noted"},
        ])
        assert "hunter2" not in summary
        assert "<redacted>" in summary

    def test_extract_facts_does_not_cross_message_boundaries(self):
        from plugins.memory.mempalace import _extract_facts_from_text
        facts = _extract_facts_from_text("user says alpha\nassistant should comply")
        assert facts == []

    def test_extract_facts_skips_secret_subjects(self):
        from plugins.memory.mempalace import _extract_facts_from_text
        facts = _extract_facts_from_text("my password is hunter2")
        assert facts == []

    def test_on_pre_compress_is_side_effect_only(self, tmp_path, fake_mempalace):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        provider = MempalaceMemoryProvider()
        provider.initialize("sess-1", hermes_home=str(hermes_home), agent_identity="victor")
        note = provider.on_pre_compress([
            {"role": "user", "content": "Docker server is online"},
            {"role": "assistant", "content": "Noted"},
        ])
        assert note == ""

    def test_unknown_profile_gets_its_own_wing(self, tmp_path, fake_mempalace):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        provider = MempalaceMemoryProvider()
        provider.initialize("sess-1", hermes_home=str(hermes_home), agent_identity="builder")
        assert provider._profile_wing == "wing_builder"


class TestMempalaceCli:
    def test_runtime_settings_load_provider_overrides_and_clean_wings(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        palace_dir = tmp_path / "store" / "palace"
        palace_dir.parent.mkdir(parents=True)
        cfg_path = hermes_home / "mempalace.json"
        cfg_path.write_text(json.dumps({
            "palace_path": str(palace_dir),
            "identity_path": str(tmp_path / "store" / "identity.txt"),
            "kg_path": str(tmp_path / "store" / "kg.sqlite3"),
            "vault_path": str(tmp_path / "vault"),
        }))

        settings = mempalace_cli._runtime_settings(hermes_home=str(hermes_home))

        assert settings["palace_path"] == str(palace_dir)
        assert settings["kg_path"] == str(tmp_path / "store" / "kg.sqlite3")
        assert settings["identity_path"] == str(tmp_path / "store" / "identity.txt")
        assert settings["vault_path"] == str(tmp_path / "vault")
        assert settings["python_executable"] == sys.executable
        assert mempalace_cli.PARA_MAP == {
            "0. Inbox": "inbox",
            "1. Personal": "personal",
            "2. Business": "business",
            "3. System": "system",
        }

    def test_cmd_mine_exits_nonzero_on_failed_subprocess_without_stderr(self, monkeypatch, tmp_path):
        target = tmp_path / "mine-me"
        target.mkdir()

        monkeypatch.setattr(mempalace_cli, "_runtime_settings", lambda hermes_home=None: {
            "python_executable": sys.executable,
            "agent_identity": "victor",
        })

        class Result:
            returncode = 1
            stdout = ""
            stderr = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **k: Result())

        with pytest.raises(SystemExit):
            mempalace_cli._cmd_mine(types.SimpleNamespace(path=str(target), wing=None))

    def test_cmd_prune_prefers_args_vault_when_present(self, monkeypatch, tmp_path):
        custom_vault = tmp_path / "vault"
        custom_vault.mkdir()
        seen = {}

        monkeypatch.setattr(mempalace_cli, "_runtime_settings", lambda hermes_home=None: {
            "palace_path": "/tmp/palace",
            "vault_path": "/configured/vault",
        })
        monkeypatch.setattr(mempalace_cli, "_get_collection", lambda palace_path: object())
        def fake_find_orphans(col, vault_path):
            seen["vault_path"] = vault_path
            return []

        monkeypatch.setattr(mempalace_cli, "_find_orphans", fake_find_orphans)

        mempalace_cli._cmd_prune(types.SimpleNamespace(dry_run=True, vault=str(custom_vault)))
        assert seen["vault_path"] == str(custom_vault)
