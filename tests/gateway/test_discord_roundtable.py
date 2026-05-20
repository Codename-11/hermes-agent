"""Tests for Discord multi-agent roundtable safety helpers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.discord import DiscordAdapter


@pytest.fixture(autouse=True)
def _isolate_discord_roundtable_env(monkeypatch):
    """Keep live gateway env from overriding per-test Discord config fixtures."""
    for name in [
        "DISCORD_ALLOW_BOTS",
        "DISCORD_ROUNDTABLE_ENABLED",
        "DISCORD_ROUNDTABLE_INCLUDE_BOT_HISTORY",
        "DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS",
        "DISCORD_ROUNDTABLE_PARTICIPANT_BOT_IDS",
    ]:
        monkeypatch.delenv(name, raising=False)


def _adapter(extra=None):
    return DiscordAdapter(PlatformConfig(enabled=True, token="test-token", extra=extra or {}))


def _user(user_id="111", *, bot=False):
    return SimpleNamespace(id=int(user_id), bot=bot, display_name=f"user-{user_id}")


def _message(*, author=None, mentions=None):
    return SimpleNamespace(author=author or _user(), mentions=mentions or [])


def test_discord_allow_bots_defaults_to_none(monkeypatch):
    monkeypatch.delenv("DISCORD_ALLOW_BOTS", raising=False)

    adapter = _adapter()

    assert adapter._discord_allow_bots() == "none"


@pytest.mark.parametrize("value", ["mentions", "all", "none"])
def test_discord_allow_bots_reads_config_when_env_unset(monkeypatch, value):
    monkeypatch.delenv("DISCORD_ALLOW_BOTS", raising=False)

    adapter = _adapter({"allow_bots": value})

    assert adapter._discord_allow_bots() == value


def test_discord_allow_bots_env_overrides_config(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "all")

    adapter = _adapter({"allow_bots": "none"})

    assert adapter._discord_allow_bots() == "all"


def test_discord_allow_bots_invalid_value_falls_back_to_none(monkeypatch, caplog):
    monkeypatch.delenv("DISCORD_ALLOW_BOTS", raising=False)
    adapter = _adapter({"allow_bots": "mentons"})

    assert adapter._discord_allow_bots() == "none"
    assert any("allow_bots" in record.message and "mentons" in record.message for record in caplog.records)


def test_should_admit_bot_message_requires_self_mention_when_configured(monkeypatch):
    monkeypatch.delenv("DISCORD_ALLOW_BOTS", raising=False)
    adapter = _adapter({"allow_bots": "mentions"})
    self_user = _user("42", bot=True)
    adapter._client = SimpleNamespace(user=self_user)

    other_bot = _user("99", bot=True)

    assert adapter._should_admit_bot_message(_message(author=other_bot, mentions=[])) is False
    assert adapter._should_admit_bot_message(_message(author=other_bot, mentions=[self_user])) is True


def test_roundtable_config_defaults_are_safe(monkeypatch):
    for name in [
        "DISCORD_ROUNDTABLE_ENABLED",
        "DISCORD_ROUNDTABLE_INCLUDE_BOT_HISTORY",
        "DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS",
        "DISCORD_ROUNDTABLE_PARTICIPANT_BOT_IDS",
    ]:
        monkeypatch.delenv(name, raising=False)

    adapter = _adapter()

    assert adapter._discord_roundtable_config() == {
        "enabled": False,
        "include_bot_history": True,
        "outbound_bot_mentions": "escape",
        "participant_bot_ids": set(),
    }


def test_include_bot_history_honors_config_allow_bots(monkeypatch):
    monkeypatch.delenv("DISCORD_ALLOW_BOTS", raising=False)

    adapter = _adapter({"allow_bots": "mentions"})

    assert adapter._discord_include_bot_history() is True


def test_roundtable_can_disable_bot_history_even_when_allow_bots_mentions(monkeypatch):
    monkeypatch.delenv("DISCORD_ALLOW_BOTS", raising=False)

    adapter = _adapter({
        "allow_bots": "mentions",
        "roundtable": {"enabled": True, "include_bot_history": False},
    })

    assert adapter._discord_include_bot_history() is False


def test_escape_outbound_roundtable_bot_mentions(monkeypatch):
    monkeypatch.delenv("DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS", raising=False)
    adapter = _adapter({
        "roundtable": {
            "enabled": True,
            "participant_bot_ids": ["123", "456"],
        }
    })
    adapter._client = SimpleNamespace(user=_user("456", bot=True))

    content = "Ask <@123>, keep self <@456>, and keep human <@789>."

    assert adapter._escape_outbound_roundtable_bot_mentions(content) == (
        "Ask <@\u200b123>, keep self <@456>, and keep human <@789>."
    )


def test_escape_outbound_roundtable_bot_mentions_noops_when_disabled(monkeypatch):
    monkeypatch.delenv("DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS", raising=False)
    adapter = _adapter({
        "roundtable": {
            "enabled": False,
            "participant_bot_ids": ["123"],
        }
    })

    content = "Ask <@123>."

    assert adapter._escape_outbound_roundtable_bot_mentions(content) == content


def test_escape_outbound_roundtable_bot_mentions_can_be_explicitly_allowed(monkeypatch):
    monkeypatch.delenv("DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS", raising=False)
    adapter = _adapter({
        "roundtable": {
            "enabled": True,
            "outbound_bot_mentions": "allow",
            "participant_bot_ids": ["123"],
        }
    })

    content = "Ask <@123>."

    assert adapter._escape_outbound_roundtable_bot_mentions(content) == content


def test_roundtable_status_is_json_safe(monkeypatch):
    for name in [
        "DISCORD_ALLOW_BOTS",
        "DISCORD_ROUNDTABLE_ENABLED",
        "DISCORD_ROUNDTABLE_PARTICIPANT_BOT_IDS",
    ]:
        monkeypatch.delenv(name, raising=False)
    adapter = _adapter({
        "allow_bots": "mentions",
        "roundtable": {
            "enabled": True,
            "participant_bot_ids": ["456", "123"],
        },
    })

    status = adapter._discord_roundtable_status()

    assert status["roundtable"]["participant_bot_ids"] == ["123", "456"]


def test_discord_roundtable_config_yaml_bridges_to_extra_and_env(monkeypatch, tmp_path):
    import os
    from pathlib import Path

    import yaml

    for name in [
        "DISCORD_ALLOW_BOTS",
        "DISCORD_ROUNDTABLE_ENABLED",
        "DISCORD_ROUNDTABLE_INCLUDE_BOT_HISTORY",
        "DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS",
        "DISCORD_ROUNDTABLE_PARTICIPANT_BOT_IDS",
    ]:
        monkeypatch.delenv(name, raising=False)

    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir()
    (hermes_dir / "config.yaml").write_text(yaml.dump({
        "discord": {
            "allow_bots": "mentions",
            "roundtable": {
                "enabled": True,
                "include_bot_history": False,
                "outbound_bot_mentions": "escape",
                "participant_bot_ids": ["456", "123"],
            },
        },
    }))
    monkeypatch.setenv("HERMES_HOME", str(hermes_dir))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from gateway.config import load_gateway_config

    try:
        config = load_gateway_config()

        discord_cfg = config.platforms.get(Platform.DISCORD)
        assert discord_cfg is not None
        assert discord_cfg.extra["allow_bots"] == "mentions"
        assert discord_cfg.extra["roundtable"] == {
            "enabled": True,
            "include_bot_history": False,
            "outbound_bot_mentions": "escape",
            "participant_bot_ids": ["456", "123"],
        }
        assert os.getenv("DISCORD_ALLOW_BOTS") == "mentions"
        assert os.getenv("DISCORD_ROUNDTABLE_ENABLED") == "true"
        assert os.getenv("DISCORD_ROUNDTABLE_INCLUDE_BOT_HISTORY") == "false"
        assert os.getenv("DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS") == "escape"
        assert os.getenv("DISCORD_ROUNDTABLE_PARTICIPANT_BOT_IDS") == "456,123"
    finally:
        for name in [
            "DISCORD_ALLOW_BOTS",
            "DISCORD_ROUNDTABLE_ENABLED",
            "DISCORD_ROUNDTABLE_INCLUDE_BOT_HISTORY",
            "DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS",
            "DISCORD_ROUNDTABLE_PARTICIPANT_BOT_IDS",
        ]:
            os.environ.pop(name, None)
