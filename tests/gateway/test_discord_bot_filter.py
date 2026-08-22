"""Tests for Discord bot message filtering (DISCORD_ALLOW_BOTS)."""

import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from gateway.config import PlatformConfig
from hermes_cli.config_defaults import DEFAULT_CONFIG
from plugins.platforms.discord.adapter import DiscordAdapter, _apply_yaml_config, discord


def _make_author(*, bot: bool = False, is_self: bool = False):
    """Create a mock Discord author."""
    author = MagicMock()
    author.bot = bot
    author.id = 99999 if is_self else 12345
    author.name = "TestBot" if bot else "TestUser"
    author.display_name = author.name
    return author


def _make_message(*, author=None, content="hello", mentions=None, is_dm=False):
    """Create a mock Discord message."""
    msg = MagicMock()
    msg.author = author or _make_author()
    msg.content = content
    msg.attachments = []
    msg.mentions = mentions or []
    if is_dm:
        import discord
        msg.channel = MagicMock(spec=discord.DMChannel)
        msg.channel.id = 111
    else:
        msg.channel = MagicMock()
        msg.channel.id = 222
        msg.channel.name = "test-channel"
        msg.channel.guild = MagicMock()
        msg.channel.guild.name = "TestServer"
        # Make isinstance checks fail for DMChannel and Thread
        type(msg.channel).__name__ = "TextChannel"
    return msg


class TestDiscordBotFilter(unittest.TestCase):
    """Test the DISCORD_ALLOW_BOTS filtering logic."""

    @staticmethod
    def _self_is_explicitly_mentioned(message, client_user):
        """Mirror adapter._self_is_explicitly_mentioned: resolved or raw mention."""
        if not client_user:
            return False
        if client_user in message.mentions:
            return True
        raw_ids = {
            m.group(1)
            for m in re.finditer(r"<@!?(\d+)>", getattr(message, "content", "") or "")
        }
        return str(client_user.id) in raw_ids

    @staticmethod
    def _self_is_raw_mentioned(message, client_user):
        """Mirror adapter._self_is_raw_mentioned: raw inline token only."""
        if not client_user:
            return False
        raw_ids = {
            m.group(1)
            for m in re.finditer(r"<@!?(\d+)>", getattr(message, "content", "") or "")
        }
        return str(client_user.id) in raw_ids

    def _run_filter(
        self,
        message,
        allow_bots="none",
        client_user=None,
        bots_require_inline_mention=False,
    ):
        """Simulate the on_message filter logic and return whether message was accepted."""
        # Replicate the exact filter logic from discord.py on_message
        if message.author == client_user:
            return False  # own messages always ignored

        if getattr(message.author, "bot", False):
            allow = allow_bots.lower().strip()
            if allow == "none":
                return False
            elif allow == "mentions":
                if not self._self_is_explicitly_mentioned(message, client_user):
                    return False
            if (
                bots_require_inline_mention
                and not self._self_is_raw_mentioned(message, client_user)
            ):
                return False
            # "all" falls through
        
        return True  # message accepted

    def test_own_messages_always_ignored(self):
        """Bot's own messages are always ignored regardless of allow_bots."""
        bot_user = _make_author(is_self=True)
        msg = _make_message(author=bot_user)
        self.assertFalse(self._run_filter(msg, "all", bot_user))

    def test_human_messages_always_accepted(self):
        """Human messages are always accepted regardless of allow_bots."""
        human = _make_author(bot=False)
        msg = _make_message(author=human)
        self.assertTrue(self._run_filter(msg, "none"))
        self.assertTrue(self._run_filter(msg, "mentions"))
        self.assertTrue(self._run_filter(msg, "all"))


    def test_allow_bots_mentions_rejects_without_mention(self):
        """With allow_bots=mentions, bot messages without @mention are rejected."""
        our_user = _make_author(is_self=True)
        bot = _make_author(bot=True)
        msg = _make_message(author=bot, mentions=[])
        self.assertFalse(self._run_filter(msg, "mentions", our_user))


    def test_inline_mention_requirement_accepts_body_mention(self):
        """Opt-in guard still admits intentional inline cross-bot mentions."""
        our_user = _make_author(is_self=True)
        bot = _make_author(bot=True)
        msg = _make_message(
            author=bot,
            content=f"<@{our_user.id}> intentional handoff",
            mentions=[our_user],
        )

        self.assertTrue(
            self._run_filter(
                msg,
                "all",
                our_user,
                bots_require_inline_mention=True,
            )
        )


    def test_default_is_none(self):
        """Default behavior (no env var) should be 'none'."""
        default = os.getenv("DISCORD_ALLOW_BOTS", "none")
        self.assertEqual(default, "none")


def _real_adapter(*, extra=None):
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***", extra=extra or {}))
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=999, bot=True))
    return adapter


def _real_bot_message(*, content="hello", mentions=None):
    return SimpleNamespace(
        id=42,
        author=SimpleNamespace(id=123, bot=True, display_name="PeerBot"),
        content=content,
        mentions=list(mentions or []),
        channel=SimpleNamespace(id=456, name="bots", guild=SimpleNamespace(id=789)),
        guild=SimpleNamespace(id=789),
        type=discord.MessageType.default,
    )


def test_invalid_allow_bots_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "yes-please")
    adapter = _real_adapter()

    assert adapter._get_allow_bots() == "none"
    admitted, _ = adapter._discord_message_admission(_real_bot_message(), claim=False)
    assert admitted is False


def test_real_admission_rejects_reply_ping_only_when_inline_guard_enabled(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "all")
    adapter = _real_adapter(extra={"bots_require_inline_mention": True})
    message = _real_bot_message(content="reply chip only", mentions=[adapter._client.user])

    admitted, _ = adapter._discord_message_admission(message, claim=False)

    assert admitted is False


def test_inline_guard_env_overrides_config_default(monkeypatch):
    monkeypatch.setenv("DISCORD_BOTS_REQUIRE_INLINE_MENTION", "true")
    adapter = _real_adapter(extra={"bots_require_inline_mention": False})

    assert adapter._discord_bots_require_inline_mention() is True


def test_discord_defaults_manifest_safe_bot_and_mention_policy():
    discord_defaults = DEFAULT_CONFIG["discord"]

    assert discord_defaults["allow_bots"] == "none"
    assert discord_defaults["bots_require_inline_mention"] is False
    assert discord_defaults["allow_mentions"] == {
        "everyone": False,
        "roles": False,
        "users": True,
        "replied_user": True,
    }


def test_yaml_bot_admission_policy_is_seeded_per_adapter(monkeypatch):
    monkeypatch.delenv("DISCORD_ALLOW_BOTS", raising=False)
    monkeypatch.delenv("DISCORD_BOTS_REQUIRE_INLINE_MENTION", raising=False)

    seeded = _apply_yaml_config(
        {},
        {
            "allow_bots": "mentions",
            "bots_require_inline_mention": True,
            "allow_mentions": {"everyone": False, "roles": False},
        },
    )

    assert seeded["allow_bots"] == "mentions"
    assert seeded["bots_require_inline_mention"] is True
    assert seeded["allow_mentions"] == {"everyone": False, "roles": False}


if __name__ == "__main__":
    unittest.main()
