from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from gateway.config import Platform, PlatformConfig
from gateway.platforms.slack import SlackAdapter
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource


class TestSlackTopLevelChannelSessionSetting:
    def test_disabled_by_default_for_backwards_compatibility(self):
        adapter = SlackAdapter(PlatformConfig(extra={}))

        assert adapter._top_level_messages_use_channel_session() is False

    def test_accepts_canonical_setting(self):
        adapter = SlackAdapter(
            PlatformConfig(extra={"top_level_messages_use_channel_session": True})
        )

        assert adapter._top_level_messages_use_channel_session() is True

    def test_accepts_legacy_alias(self):
        adapter = SlackAdapter(
            PlatformConfig(extra={"top_level_channel_session": "yes"})
        )

        assert adapter._top_level_messages_use_channel_session() is True


class TestStopFallbackCandidates:
    def _runner(self):
        runner = GatewayRunner.__new__(GatewayRunner)
        runner._running_agents = {}
        runner._running_agents_ts = {}
        runner._session_sources = OrderedDict()
        runner._session_sources_max = 512
        return runner

    def test_matches_same_slack_channel_and_user(self):
        runner = self._runner()
        source = SessionSource(
            platform=Platform.SLACK,
            chat_id="C-accounting",
            chat_type="group",
            user_id="U-bailey",
        )
        sibling = SessionSource(
            platform=Platform.SLACK,
            chat_id="C-accounting",
            chat_type="group",
            user_id="U-bailey",
            thread_id="1717000000.000100",
        )
        other_user = SessionSource(
            platform=Platform.SLACK,
            chat_id="C-accounting",
            chat_type="group",
            user_id="U-other",
            thread_id="1717000000.000200",
        )
        runner._running_agents = {
            "agent:main:slack:group:C-accounting:1717000000.000100": object(),
            "agent:main:slack:group:C-accounting:1717000000.000200": object(),
        }
        runner._running_agents_ts = {
            "agent:main:slack:group:C-accounting:1717000000.000100": 20,
            "agent:main:slack:group:C-accounting:1717000000.000200": 30,
        }
        runner._cache_session_source(
            "agent:main:slack:group:C-accounting:1717000000.000100",
            sibling,
        )
        runner._cache_session_source(
            "agent:main:slack:group:C-accounting:1717000000.000200",
            other_user,
        )

        candidates = runner._stop_candidates_for_source(source, same_user=True)

        assert [key for key, _ in candidates] == [
            "agent:main:slack:group:C-accounting:1717000000.000100"
        ]

    def test_sorts_candidates_newest_first(self):
        runner = self._runner()
        source = SessionSource(
            platform=Platform.SLACK,
            chat_id="C-accounting",
            chat_type="group",
            user_id="U-bailey",
        )
        old_key = "agent:main:slack:group:C-accounting:old"
        new_key = "agent:main:slack:group:C-accounting:new"
        runner._running_agents = {old_key: object(), new_key: object()}
        runner._running_agents_ts = {old_key: 10, new_key: 50}
        for key, thread_id in [(old_key, "old"), (new_key, "new")]:
            runner._cache_session_source(
                key,
                SessionSource(
                    platform=Platform.SLACK,
                    chat_id="C-accounting",
                    chat_type="group",
                    user_id="U-bailey",
                    thread_id=thread_id,
                ),
            )

        candidates = runner._stop_candidates_for_source(source, same_user=True)

        assert [key for key, _ in candidates] == [new_key, old_key]


class TestSlackPreviousTopLevelSessionRecall:
    def _runner(self, entries, transcripts=None):
        class Store:
            def __init__(self):
                self._entries = {entry.session_key: entry for entry in entries}
                self.transcripts = transcripts or {}
                self.switched = []

            def list_sessions(self):
                return list(self._entries.values())

            def load_transcript(self, session_id):
                return list(self.transcripts.get(session_id, []))

            def switch_session(self, session_key, target_session_id):
                self.switched.append((session_key, target_session_id))
                entry = self._entries[session_key]
                entry.session_id = target_session_id
                return entry

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.session_store = Store()
        return runner

    def _entry(self, key, sid, source, *, minutes_ago=0):
        now = datetime.now(timezone.utc)
        return SessionEntry(
            session_key=key,
            session_id=sid,
            created_at=now - timedelta(minutes=minutes_ago + 1),
            updated_at=now - timedelta(minutes=minutes_ago),
            origin=source,
            platform=Platform.SLACK,
            chat_type=source.chat_type,
        )

    def test_finds_recent_legacy_timestamp_session_for_same_slack_channel_user(self):
        source = SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U1")
        current = self._entry("agent:main:slack:group:C0:U1", "current", source)
        legacy = self._entry(
            "agent:main:slack:group:C0:1717000000.000100",
            "legacy",
            SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U1", thread_id="1717000000.000100"),
            minutes_ago=5,
        )
        other_user = self._entry(
            "agent:main:slack:group:C0:1717000000.000200",
            "other",
            SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U2", thread_id="1717000000.000200"),
            minutes_ago=1,
        )
        runner = self._runner([current, legacy, other_user], transcripts={"legacy": [{"role": "user", "content": "old ask"}]})

        matches = runner._find_slack_previous_top_level_sessions(source, current)

        assert [entry.session_id for entry in matches] == ["legacy"]

    def test_migrates_empty_new_channel_session_to_latest_legacy_transcript(self):
        source = SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U1")
        current = self._entry("agent:main:slack:group:C0:U1", "current", source)
        legacy = self._entry(
            "agent:main:slack:group:C0:1717000000.000100",
            "legacy",
            SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U1", thread_id="1717000000.000100"),
        )
        runner = self._runner([current, legacy], transcripts={"legacy": [{"role": "user", "content": "old ask"}]})

        migrated, history = runner._maybe_migrate_slack_top_level_session(source, current, [])

        assert migrated.session_id == "legacy"
        assert history == [{"role": "user", "content": "old ask"}]
        assert runner.session_store.switched == [("agent:main:slack:group:C0:U1", "legacy")]

    def test_builds_previous_recall_note_without_switching_non_empty_session(self):
        source = SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U1")
        current = self._entry("agent:main:slack:group:C0:U1", "current", source)
        legacy = self._entry(
            "agent:main:slack:group:C0:1717000000.000100",
            "legacy",
            SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U1", thread_id="1717000000.000100"),
        )
        runner = self._runner(
            [current, legacy],
            transcripts={"legacy": [
                {"role": "user", "content": "Do you remember the bridge patch?"},
                {"role": "assistant", "content": "Yes, it was about Slack session keys."},
            ]},
        )

        note = runner._build_slack_previous_session_recall_note(source, current, current_history=[{"role": "user", "content": "new"}])

        assert note is not None
        assert "previous Slack top-level session" in note["content"]
        assert "Do you remember the bridge patch?" in note["content"]
        assert runner.session_store.switched == []

    def test_builds_previous_recall_note_with_current_session_id(self):
        source = SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U1")
        current = self._entry("agent:main:slack:group:C0:U1", "current", source)
        legacy = self._entry(
            "agent:main:slack:group:C0:1717000000.000100",
            "legacy",
            SessionSource(platform=Platform.SLACK, chat_id="C0", chat_type="group", user_id="U1", thread_id="1717000000.000100"),
        )
        runner = self._runner(
            [current, legacy],
            transcripts={"legacy": [{"role": "user", "content": "old ask"}]},
        )

        note = runner._build_slack_previous_session_recall_note(source, "current", current_history=[{"role": "user", "content": "new"}])

        assert note is not None
        assert "old ask" in note["content"]
