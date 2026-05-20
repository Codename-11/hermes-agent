# Discord Roundtable Mode Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make multi-profile Discord collaboration first-class enough for Victor/Mizu/Sentinel-style roundtables while keeping Hermes' safe defaults and upstream alignment.

**Architecture:** Keep the existing model of one gateway/profile per agent. Do not build a central Discord multiplexer in this patch. Instead, turn the current safety primitives into a tested admission policy, add config parity for bot-message handling, include safe bot context in history backfill, and prevent accidental bot-to-bot mention cascades on outbound messages.

**Tech Stack:** Python, `discord.py`, existing Hermes gateway adapters, existing `SessionSource` / `MessageEvent` / history-backfill machinery, pytest.

---

## Current Repo Findings

- `gateway/platforms/discord.py` already has the core primitives:
  - `DISCORD_ALLOW_BOTS=none|mentions|all` admission for bot-authored messages.
  - Bot-aware mention filtering that ignores messages addressed to a different bot.
  - `discord.thread_require_mention` / `DISCORD_THREAD_REQUIRE_MENTION`.
  - `discord.history_backfill` / `DISCORD_HISTORY_BACKFILL` and `history_backfill_limit`.
  - Backfill labels bot authors as `[Name [bot]]` when bot messages are included.
- Gaps worth patching:
  - Discord `allow_bots` is env-only in the hot path, unlike Slack/Feishu-style config normalization.
  - Bot admission logic is inline in `on_message`; the current tests mirror the logic instead of exercising adapter helpers directly.
  - History backfill decides whether to include other bots from env only, not config.
  - Outbound Discord sends can accidentally wake another Hermes bot if the LLM emits a live bot mention and `DISCORD_ALLOW_BOTS=mentions` is enabled on that target.
  - There is no explicit `roundtable` preset/capability shape that tells operators which knobs are active and what behavior to expect.

---

## Product Stance

Roundtable mode should mean:

1. **Human-facilitated by default.** Bailey can pull each agent in with an explicit mention. Agents can see each other's prior messages through backfill, but they should not automatically summon each other.
2. **No ambient chatter.** `free_response_channels` stays empty in shared rooms.
3. **No cascades.** Bot-authored messages are ignored unless directly mentioning the receiving bot, and outbound bot mentions are escaped unless explicitly allowed.
4. **No parallel session database or shared-memory hack.** Existing Discord sessions, `SessionSource`, `MessageEvent.channel_context`, and per-profile memory remain the boundaries.
5. **Fork-local but upstreamable.** The patch should be small, isolated to Discord gateway/config/tests/docs, and useful even without Axiom-specific profile names.

---

## Proposed User-Facing Config

Add these keys under the existing `discord:` block in `hermes_cli/config.py` defaults and adapter docs/comments:

```yaml
discord:
  require_mention: true
  thread_require_mention: true
  history_backfill: true
  history_backfill_limit: 50
  free_response_channels: ''

  # New: config parity with env. Env remains supported and can override when present.
  allow_bots: none          # none | mentions | all

  # New: a safe macro/preset for shared multi-agent rooms.
  roundtable:
    enabled: false
    include_bot_history: true
    outbound_bot_mentions: escape   # escape | allow
    participant_bot_ids: []         # Discord user IDs for known Hermes agents in this room
```

Environment equivalents:

```bash
DISCORD_ALLOW_BOTS=none|mentions|all
DISCORD_ROUNDTABLE_ENABLED=false|true
DISCORD_ROUNDTABLE_INCLUDE_BOT_HISTORY=true|false
DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS=escape|allow
DISCORD_ROUNDTABLE_PARTICIPANT_BOT_IDS=123,456,789
```

Recommended roundtable profile config:

```yaml
discord:
  require_mention: true
  thread_require_mention: true
  history_backfill: true
  history_backfill_limit: 80
  free_response_channels: ''
  allowed_channels: '<parent-channel-id>'
  allow_bots: mentions
  roundtable:
    enabled: true
    include_bot_history: true
    outbound_bot_mentions: escape
    participant_bot_ids:
      - '<victor-bot-user-id>'
      - '<mizu-bot-user-id>'
      - '<sentinel-bot-user-id>'
```

Important: `roundtable.enabled: true` should not make the bot respond without mention. It should only tighten/clarify the safe multi-agent behavior.

---

## Task 1: Add Discord config helpers for roundtable and allow_bots

**Objective:** Move Discord bot-admission settings into small helper methods so both message admission and history backfill use one normalized source.

**Files:**
- Modify: `gateway/platforms/discord.py`
- Test: `tests/gateway/test_discord_bot_filter.py` or new `tests/gateway/test_discord_roundtable.py`

**Implementation sketch:**

```python
_VALID_DISCORD_ALLOW_BOTS = {"none", "mentions", "all"}
_VALID_DISCORD_OUTBOUND_BOT_MENTIONS = {"escape", "allow"}

def _discord_allow_bots(self) -> str:
    raw = os.getenv("DISCORD_ALLOW_BOTS")
    if raw is None:
        raw = self.config.extra.get("allow_bots", "none")
    value = str(raw or "none").strip().lower()
    if value not in _VALID_DISCORD_ALLOW_BOTS:
        logger.warning("[%s] Invalid discord.allow_bots value %r; using 'none'", self.name, raw)
        return "none"
    return value

def _discord_roundtable_config(self) -> dict:
    raw = self.config.extra.get("roundtable")
    cfg = raw if isinstance(raw, dict) else {}
    enabled = _bool_from_env_or_config("DISCORD_ROUNDTABLE_ENABLED", cfg.get("enabled"), False)
    include_bot_history = _bool_from_env_or_config(
        "DISCORD_ROUNDTABLE_INCLUDE_BOT_HISTORY",
        cfg.get("include_bot_history"),
        True,
    )
    outbound = os.getenv("DISCORD_ROUNDTABLE_OUTBOUND_BOT_MENTIONS") or cfg.get("outbound_bot_mentions", "escape")
    outbound = str(outbound or "escape").strip().lower()
    if outbound not in _VALID_DISCORD_OUTBOUND_BOT_MENTIONS:
        outbound = "escape"
    participant_ids = _csv_from_env_or_config(
        "DISCORD_ROUNDTABLE_PARTICIPANT_BOT_IDS",
        cfg.get("participant_bot_ids", []),
    )
    return {
        "enabled": enabled,
        "include_bot_history": include_bot_history,
        "outbound_bot_mentions": outbound,
        "participant_bot_ids": set(participant_ids),
    }
```

Keep helpers private to the Discord adapter; do not add global config complexity unless tests prove it is needed.

**Tests:**
- Defaults to `allow_bots == 'none'`.
- `discord.allow_bots: mentions` is honored when env is unset.
- `DISCORD_ALLOW_BOTS` overrides config.
- Unknown values warn and fall back to `none`.
- `roundtable` defaults are safe: disabled, include bot history true, outbound bot mentions escape.

---

## Task 2: Replace inline bot-message admission with a tested helper

**Objective:** Make admission behavior readable and test the actual adapter path instead of copying logic into tests.

**Files:**
- Modify: `gateway/platforms/discord.py`
- Modify: `tests/gateway/test_discord_bot_filter.py`

**Implementation sketch:**

```python
def _message_mentions_self(self, message: Any) -> bool:
    return bool(self._client and self._client.user and self._client.user in getattr(message, "mentions", []))

def _should_admit_bot_message(self, message: Any) -> bool:
    allow_bots = self._discord_allow_bots()
    if allow_bots == "none":
        return False
    if allow_bots == "mentions" and not self._message_mentions_self(message):
        return False
    return True
```

Then in `on_message`:

```python
if getattr(message.author, "bot", False):
    if not adapter_self._should_admit_bot_message(message):
        return
else:
    # existing human allowlist path
```

**Tests:**
- Own messages still ignored before helper runs.
- Bot messages rejected by default.
- Bot messages accepted only with self mention when `allow_bots=mentions`.
- Bot messages accepted with `allow_bots=all`.
- Human allowlists remain untouched.

---

## Task 3: Make history backfill use normalized bot-history policy

**Objective:** Ensure roundtable context includes prior bot messages when safe, without requiring env-only configuration.

**Files:**
- Modify: `gateway/platforms/discord.py`
- Test: add cases near existing history-backfill tests, or create `tests/gateway/test_discord_roundtable.py`

**Behavior:**
- If `roundtable.enabled` and `roundtable.include_bot_history`, include other bots in backfill regardless of whether `allow_bots` came from env or config.
- Otherwise preserve current behavior: include other bots when `allow_bots != 'none'`.

**Implementation sketch:**

```python
def _discord_include_bot_history(self) -> bool:
    rt = self._discord_roundtable_config()
    if rt["enabled"]:
        return bool(rt["include_bot_history"])
    return self._discord_allow_bots() != "none"
```

Use this in `_fetch_channel_context()` instead of direct `os.getenv("DISCORD_ALLOW_BOTS", "none")`.

**Tests:**
- Config-only `allow_bots: mentions` includes bot lines in backfill.
- Roundtable enabled + include false excludes bot lines.
- Default excludes bot lines.

---

## Task 4: Escape outbound bot mentions by default in roundtable rooms

**Objective:** Prevent LLM output from accidentally waking another Hermes bot when that bot accepts bot-authored mention messages.

**Files:**
- Modify: `gateway/platforms/discord.py`
- Test: `tests/gateway/test_discord_roundtable.py`

**Behavior:**
- Only active when `roundtable.enabled` and `outbound_bot_mentions == 'escape'`.
- Escape mentions for configured `participant_bot_ids` except the current bot's own ID.
- Do not alter human mentions.
- Do not strip visible text; make the mention non-pinging by injecting a zero-width space after `<@`.

**Implementation sketch:**

```python
def _escape_outbound_roundtable_bot_mentions(self, content: str) -> str:
    rt = self._discord_roundtable_config()
    if not rt["enabled"] or rt["outbound_bot_mentions"] != "escape":
        return content
    ids = set(rt["participant_bot_ids"])
    if self._client and self._client.user:
        ids.discard(str(self._client.user.id))
    for bot_id in ids:
        content = content.replace(f"<@{bot_id}>", f"<@\u200b{bot_id}>")
        content = content.replace(f"<@!{bot_id}>", f"<@!\u200b{bot_id}>")
    return content
```

Call this after `format_message(content)` and before chunking in `send()` and `_send_to_forum()`.

**Tests:**
- Escapes configured bot IDs in roundtable mode.
- Does not escape when roundtable disabled.
- Does not escape non-participant user mentions.
- Does not escape current bot ID.

---

## Task 5: Add a compact roundtable status/capability surface

**Objective:** Give operators a quick way to verify that a profile is safe for a shared room without reading env/config manually.

**Files:**
- Modify: `gateway/platforms/discord.py` if there is an existing channel-control/status command hook suitable for Discord.
- Otherwise defer code and document config in `website/docs/user-guide/messaging/discord.md` or local skill docs.

**Preferred minimal version:** add no new slash command. Instead, expose a private method used by tests and logs:

```python
def _discord_roundtable_status(self) -> dict:
    return {
        "allow_bots": self._discord_allow_bots(),
        "require_mention": self._discord_require_mention(),
        "thread_require_mention": self._discord_thread_require_mention(),
        "history_backfill": self._discord_history_backfill(),
        "include_bot_history": self._discord_include_bot_history(),
        "roundtable": self._discord_roundtable_config(),
    }
```

Log this once on connect when `roundtable.enabled` is true, redacting nothing because it contains only IDs/config modes, not secrets.

---

## Task 6: Document the safe operating pattern

**Objective:** Make the feature understandable for users and future maintainers.

**Files:**
- Modify: `website/docs/user-guide/messaging/discord.md` if present.
- Modify: `~/.hermes/skills/hermes-agent/references/discord-multi-agent-threads.md` after code behavior is final.
- Update: `DEVLOG.md` after implementation/verification.

**Docs should say:**

```text
For shared agent rooms:
- Use parent channel allowlists.
- Keep require_mention true.
- Keep thread_require_mention true.
- Keep free_response_channels empty.
- Set allow_bots to mentions only if you want agents to see direct bot handoffs.
- Enable roundtable mode to escape accidental bot pings in outbound replies.
- Human controls turn-taking by mentioning the next agent.
```

---

## Verification Commands

Run from `~/.hermes/hermes-agent`:

```bash
source venv/bin/activate
python -m py_compile gateway/platforms/discord.py hermes_cli/config.py
python -m pytest tests/gateway/test_discord_bot_filter.py -q -o 'addopts='
python -m pytest tests/gateway/test_discord_thread_persistence.py tests/gateway/test_discord_allowed_channels.py -q -o 'addopts='
python -m pytest tests/gateway/test_discord_roundtable.py -q -o 'addopts='  # if new file added
```

Optional broader smoke:

```bash
python -m pytest tests/gateway/test_discord_*.py -q -o 'addopts='
```

---

## Cut Lines / Deferred Work

Do not include these in the first patch:

- A central single-daemon multi-agent router.
- Shared memory between profiles.
- Automatic agent-to-agent debate loops.
- A `/roundtable` orchestrator that shells out to `hermes -p <profile> chat -q ...`.
- Agent-managed Discord role/channel membership.
- Anything that requires storing Discord bot tokens or profile secrets in shared config.

If we want a richer later version, build it as a separate orchestrator after this safety layer lands.

---

## Acceptance Criteria

- Existing solo Discord bot behavior remains unchanged by default.
- `discord.allow_bots` config works without env vars.
- Bot-authored messages only wake a profile under the configured policy.
- Roundtable mode lets profiles see bot context but prevents accidental outbound bot pings.
- Tests exercise real adapter helpers rather than duplicated inline logic.
- Docs describe a safe Victor/Mizu/Sentinel operating pattern without Axiom-only assumptions.
