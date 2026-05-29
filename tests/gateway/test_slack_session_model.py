from collections import OrderedDict

from gateway.config import Platform, PlatformConfig
from gateway.platforms.slack import SlackAdapter
from gateway.run import GatewayRunner
from gateway.session import SessionSource


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
