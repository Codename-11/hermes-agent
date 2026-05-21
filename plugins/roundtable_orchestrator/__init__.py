"""Roundtable orchestration plugin.

Provides a shared circuit breaker for Discord multi-agent rooms.  The plugin is
intentionally small: it does not replace Discord adapter bot-admission policy,
it adds an operator-controlled gate *after* adapter admission and before Hermes
LLM dispatch.

Commands
--------
/roundtable stop [reason]
    Disable bot-authored roundtable turns immediately across profiles that share
    the state file.
/roundtable start [reason]
    Re-enable bot-authored roundtable turns.  This only affects the plugin gate;
    Discord ``allow_bots`` still must be configured safely (typically
    ``mentions``) before bot messages can reach Hermes.
/roundtable status
    Show the current shared state.

Configuration
-------------
State file defaults to ``~/.hermes/roundtable_state.json`` so Victor/Mizu/
Sentinel share it even when their ``HERMES_HOME`` differs.  Override with
``HERMES_ROUNDTABLE_STATE``.

Roundtable channels are optional.  Configure with ``HERMES_ROUNDTABLE_CHANNELS``
(or ``DISCORD_ROUNDTABLE_CHANNELS``) as CSV.  When omitted, the circuit breaker
applies to all Discord bot-authored messages that have already passed adapter
admission — conservative and safe.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

logger = logging.getLogger(__name__)

_STATE_VERSION = 1
_DEFAULT_STATE = {
    "version": _STATE_VERSION,
    "enabled": False,
    "reason": "default-safe-disabled",
    "updated_at": None,
    "updated_by": None,
    "updated_from": None,
}
_HELP = (
    "Usage: /roundtable <status|stop|start|call|debate> [args]\n"
    "• stop — disable bot-authored roundtable turns before LLM dispatch\n"
    "• start — re-enable the plugin gate (Discord allow_bots still applies)\n"
    "• status — show shared state\n"
    "• call <agent> <message> — send one controlled Discord mention to an agent\n"
    "• debate <agent1,agent2[,agent3]> [--rounds N] <topic> — run a bounded debate that auto-stops on consensus"
)
_MAX_DEBATE_ROUNDS = 5
_DEFAULT_DEBATE_ROUNDS = 3
_CALL_TTL_SECONDS = 300
_CONSENSUS_RE = re.compile(r"ROUND[_ -]?TABLE[_ -]?DECISION\s*:\s*(CONSENSUS|AGREE|AGREEMENT)", re.I)
_DECISION_LINE_RE = re.compile(r"\s*ROUND[_ -]?TABLE[_ -]?DECISION\s*:\s*(CONSENSUS|AGREE|AGREEMENT|CONTINUE)\s*", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_in(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _state_path() -> Path:
    configured = os.getenv("HERMES_ROUNDTABLE_STATE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".hermes" / "roundtable_state.json"


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _read_state() -> Dict[str, Any]:
    path = _state_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError("state root must be an object")
    except FileNotFoundError:
        raw = {}
    except Exception as exc:
        logger.warning("Roundtable state at %s is unreadable; failing closed: %s", path, exc)
        raw = {}

    state = dict(_DEFAULT_STATE)
    state.update(raw)
    state["version"] = _STATE_VERSION
    state["enabled"] = bool(state.get("enabled"))
    return state


def _write_state(enabled: bool, *, reason: str = "", updated_by: str = "", updated_from: str = "") -> Dict[str, Any]:
    existing = _read_state()
    state = {
        "version": _STATE_VERSION,
        "enabled": bool(enabled),
        "reason": reason or ("manual-start" if enabled else "manual-stop"),
        "updated_at": _now(),
        "updated_by": updated_by or None,
        "updated_from": updated_from or None,
    }
    debate = existing.get("debate")
    if isinstance(debate, dict):
        debate = dict(debate)
        if not enabled and debate.get("active"):
            debate["active"] = False
            debate["stop_reason"] = "roundtable-stopped"
            debate["stopped_at"] = state["updated_at"]
        state["debate"] = debate
    pending_call = existing.get("pending_call")
    if isinstance(pending_call, dict):
        pending_call = dict(pending_call)
        if not enabled and pending_call.get("active"):
            pending_call["active"] = False
            pending_call["stop_reason"] = "roundtable-stopped"
            pending_call["stopped_at"] = state["updated_at"]
        state["pending_call"] = pending_call
    _write_full_state(state)
    return state


def _write_full_state(state: Dict[str, Any]) -> Dict[str, Any]:
    full = dict(_DEFAULT_STATE)
    full.update(state)
    full["version"] = _STATE_VERSION
    full["enabled"] = bool(full.get("enabled"))
    _atomic_write_json(_state_path(), full)
    return full


def _csv_set(value: Any) -> Set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(part).strip() for part in value if str(part).strip()}
    return {part.strip() for part in str(value).split(",") if part.strip()}


def _configured_channels(gateway: Any = None) -> Set[str]:
    """Return roundtable channel ids; empty means apply to all Discord bot turns."""
    env_channels = os.getenv("HERMES_ROUNDTABLE_CHANNELS") or os.getenv("DISCORD_ROUNDTABLE_CHANNELS")
    channels = _csv_set(env_channels)
    if channels:
        return channels

    # Optional config shapes.  Keep this defensive so the plugin works across
    # GatewayConfig objects and plain dict tests.
    candidates: list[Any] = []
    config = getattr(gateway, "config", None) if gateway is not None else None
    if isinstance(config, dict):
        candidates.append(config)
    elif config is not None:
        candidates.append(getattr(config, "raw", None))
        candidates.append(getattr(config, "extra", None))

    try:
        from hermes_cli.config import load_config
        loaded = load_config()
        if isinstance(loaded, dict):
            candidates.append(loaded)
    except Exception:
        pass

    for cfg in candidates:
        if not isinstance(cfg, dict):
            continue
        root_candidate = cfg.get("roundtable")
        root_rt = root_candidate if isinstance(root_candidate, dict) else {}
        channels |= _csv_set(root_rt.get("channels") or root_rt.get("channel_ids"))

        discord_candidate = cfg.get("discord")
        discord_cfg = discord_candidate if isinstance(discord_candidate, dict) else {}
        discord_rt_candidate = discord_cfg.get("roundtable")
        discord_rt = discord_rt_candidate if isinstance(discord_rt_candidate, dict) else {}
        channels |= _csv_set(discord_rt.get("channels") or discord_rt.get("channel_ids"))

    return channels


def _source_channel_ids(source: Any) -> Set[str]:
    ids = set()
    for attr in ("chat_id", "parent_chat_id", "thread_id"):
        value = getattr(source, attr, None)
        if value is not None and str(value).strip():
            ids.add(str(value).strip())
    return ids


def _is_discord_bot_event(event: Any) -> bool:
    source = getattr(event, "source", None)
    platform = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", ""))
    return str(platform).lower() == "discord" and bool(getattr(source, "is_bot", False))


def _roundtable_applies(event: Any, gateway: Any = None) -> bool:
    if not _is_discord_bot_event(event):
        return False
    channels = _configured_channels(gateway)
    if not channels:
        return True
    source = getattr(event, "source", None)
    return bool(_source_channel_ids(source) & channels)


def _format_status(state: Dict[str, Any], gateway: Any = None) -> str:
    status = "enabled" if state.get("enabled") else "stopped"
    channels = sorted(_configured_channels(gateway))
    channel_text = ", ".join(channels) if channels else "all admitted Discord bot turns"
    updated = state.get("updated_at") or "never"
    reason = state.get("reason") or "none"
    lines = [
        f"Roundtable is **{status}**.",
        f"Channels: {channel_text}",
        f"Reason: {reason}",
        f"Updated: {updated}",
    ]
    debate = state.get("debate") if isinstance(state.get("debate"), dict) else None
    if debate:
        debate_status = "active" if debate.get("active") else f"stopped ({debate.get('stop_reason') or 'unknown'})"
        participants = " → ".join(debate.get("participants") or []) or "unknown"
        lines.append(
            f"Debate: {debate_status}; participants={participants}; "
            f"turn={int(debate.get('turn_index') or 0)}/{int(debate.get('rounds') or 1) * max(1, len(debate.get('participants') or []))}; "
            f"topic={debate.get('topic') or 'n/a'}"
        )
    pending_call = state.get("pending_call") if isinstance(state.get("pending_call"), dict) else None
    if pending_call and pending_call.get("active"):
        lines.append(
            "Pending call: "
            f"{pending_call.get('target') or pending_call.get('target_bot_id')} "
            f"until {pending_call.get('expires_at') or 'unknown'}"
        )
    lines.append("Note: Discord `allow_bots` still controls whether bot-authored messages reach this plugin.")
    return "\n".join(lines)


def _source_user_id(source: Any) -> str:
    for attr in ("user_id", "author_id", "sender_id"):
        value = getattr(source, attr, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _event_text(event: Any) -> str:
    for attr in ("text", "raw_message"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _mentions_bot(text: str, bot_id: str) -> bool:
    return f"<@{bot_id}>" in (text or "") or f"<@!{bot_id}>" in (text or "")


def _consume_pending_call_if_matches(state: Dict[str, Any], event: Any) -> bool:
    """Allow exactly one stopped-gate bot turn for /roundtable call.

    The incoming event is authored by the *calling* bot and mentions the target
    bot. Adapter-level ``allow_bots: mentions`` already ensures only the
    mentioned target bot receives the event, so this plugin gate verifies the
    shared call token and consumes it before LLM dispatch.
    """
    pending = state.get("pending_call") if isinstance(state.get("pending_call"), dict) else None
    if not pending or not pending.get("active"):
        return False

    expires_at = _parse_time(pending.get("expires_at"))
    if expires_at and expires_at < datetime.now(timezone.utc):
        pending["active"] = False
        pending["expired_at"] = _now()
        pending["stop_reason"] = "expired"
        state["pending_call"] = pending
        _write_full_state(state)
        return False

    source = getattr(event, "source", None)
    source_ids = _source_channel_ids(source)
    expected_channels = {str(pending.get("channel_id") or "").strip()}
    thread_id = str(pending.get("thread_id") or "").strip()
    if thread_id:
        expected_channels.add(thread_id)
    expected_channels.discard("")
    if expected_channels and not (source_ids & expected_channels):
        return False

    caller_bot_id = str(pending.get("caller_bot_id") or "").strip()
    if caller_bot_id and _source_user_id(source) != caller_bot_id:
        return False

    target_bot_id = str(pending.get("target_bot_id") or "").strip()
    if target_bot_id and not _mentions_bot(_event_text(event), target_bot_id):
        return False

    pending["active"] = False
    pending["accepted_at"] = _now()
    pending["accepted_by"] = target_bot_id or None
    state["pending_call"] = pending
    state["updated_at"] = _now()
    state["updated_from"] = "roundtable-call"
    _write_full_state(state)
    return True


def _parse_command(args: str) -> tuple[str, str]:
    parts = (args or "").strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1].strip() if len(parts) > 1 else ""
    return sub, rest


def _config_candidates(gateway: Any = None) -> list[Dict[str, Any]]:
    candidates: list[Any] = []
    config = getattr(gateway, "config", None) if gateway is not None else None
    if isinstance(config, dict):
        candidates.append(config)
    elif config is not None:
        candidates.append(getattr(config, "raw", None))
        candidates.append(getattr(config, "extra", None))
    try:
        from hermes_cli.config import load_config
        loaded = load_config()
        if isinstance(loaded, dict):
            candidates.append(loaded)
    except Exception:
        pass
    return [cfg for cfg in candidates if isinstance(cfg, dict)]


def _parse_agent_map(value: Any) -> Dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        return {
            str(name).strip().lower(): str(bot_id).strip()
            for name, bot_id in value.items()
            if str(name).strip() and str(bot_id).strip()
        }
    raw = str(value).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return _parse_agent_map(parsed)
    except Exception:
        pass
    mapping: Dict[str, str] = {}
    for item in raw.split(","):
        if ":" not in item:
            continue
        name, bot_id = item.split(":", 1)
        name = name.strip().lower()
        bot_id = bot_id.strip()
        if name and bot_id:
            mapping[name] = bot_id
    return mapping


def _configured_agents(gateway: Any = None) -> Dict[str, str]:
    agents: Dict[str, str] = {}
    env_agents = os.getenv("HERMES_ROUNDTABLE_AGENTS") or os.getenv("DISCORD_ROUNDTABLE_AGENTS")
    agents.update(_parse_agent_map(env_agents))
    for cfg in _config_candidates(gateway):
        root_candidate = cfg.get("roundtable")
        root_rt = root_candidate if isinstance(root_candidate, dict) else {}
        agents.update(_parse_agent_map(root_rt.get("agents")))
        discord_candidate = cfg.get("discord")
        discord_cfg = discord_candidate if isinstance(discord_candidate, dict) else {}
        discord_rt_candidate = discord_cfg.get("roundtable")
        discord_rt = discord_rt_candidate if isinstance(discord_rt_candidate, dict) else {}
        agents.update(_parse_agent_map(discord_rt.get("agents")))
    return agents


def _discord_adapter(gateway: Any = None) -> Any:
    adapters = getattr(gateway, "adapters", {}) if gateway is not None else {}
    if not isinstance(adapters, dict):
        return None
    for key, adapter in adapters.items():
        value = getattr(key, "value", key)
        if str(value).lower() == "discord":
            return adapter
    return None


async def _handle_roundtable_call(rest: str, *, gateway: Any = None, event: Any = None) -> str:
    parts = (rest or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        return "Usage: /roundtable call <agent> <message>"
    target, message = parts[0].strip().lower(), parts[1].strip()
    if not message:
        return "Usage: /roundtable call <agent> <message>"

    source = getattr(event, "source", None)
    platform = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", ""))
    if str(platform).lower() != "discord":
        return "/roundtable call is only available from Discord."

    channels = _configured_channels(gateway)
    source_ids = _source_channel_ids(source)
    if channels and not (source_ids & channels):
        return "/roundtable call is only available in configured roundtable channels."

    agents = _configured_agents(gateway)
    bot_id = agents.get(target)
    if not bot_id:
        known = ", ".join(sorted(agents)) or "none configured"
        return f"Unknown roundtable agent: {target}. Known agents: {known}"

    adapter = _discord_adapter(gateway)
    if adapter is None:
        return "Discord adapter is not available for /roundtable call."

    chat_id = str(getattr(source, "chat_id", "") or "").strip()
    if not chat_id:
        return "Could not determine the current Discord channel for /roundtable call."
    thread_id = getattr(source, "thread_id", None)
    caller_bot_id = _current_bot_id(adapter)
    result = await _send_controlled_mention(adapter, chat_id, bot_id, message, thread_id=thread_id)
    if not getattr(result, "success", False):
        error = getattr(result, "error", "unknown error")
        return f"✗ Failed to call {target}: {error}"
    message_id = getattr(result, "message_id", None) or "sent"

    state = _read_state()
    state["pending_call"] = {
        "id": f"call-{uuid.uuid4().hex[:10]}",
        "active": True,
        "target": target,
        "target_bot_id": str(bot_id),
        "caller_bot_id": caller_bot_id,
        "channel_id": chat_id,
        "thread_id": str(thread_id) if thread_id else None,
        "message": message,
        "mention_message_id": message_id,
        "created_at": _now(),
        "expires_at": _iso_in(_CALL_TTL_SECONDS),
        "created_by": getattr(source, "user_id", None),
    }
    state["enabled"] = False
    state["reason"] = "single-call-pending"
    state["updated_at"] = _now()
    state["updated_from"] = "roundtable-call"
    _write_full_state(state)

    return (
        f"📣 Called **{target}** in <#{chat_id}> (message {message_id}).\n"
        "One reply from the target bot is authorized; the roundtable remains stopped for every other bot-authored turn."
    )


def _parse_debate_args(rest: str) -> tuple[list[str], int, str] | tuple[None, None, str]:
    raw = (rest or "").strip()
    if not raw:
        return None, None, "Usage: /roundtable debate <agent1,agent2[,agent3]> [--rounds N] <topic>"
    parts = raw.split()
    participants_raw = parts.pop(0)
    participants = [p.strip().lower() for p in participants_raw.replace("+", ",").split(",") if p.strip()]
    rounds = _DEFAULT_DEBATE_ROUNDS
    topic_parts: list[str] = []
    i = 0
    while i < len(parts):
        token = parts[i]
        if token == "--rounds" and i + 1 < len(parts):
            try:
                rounds = max(1, min(_MAX_DEBATE_ROUNDS, int(parts[i + 1])))
            except ValueError:
                return None, None, "Invalid --rounds value; use an integer."
            i += 2
            continue
        if token.startswith("--rounds="):
            try:
                rounds = max(1, min(_MAX_DEBATE_ROUNDS, int(token.split("=", 1)[1])))
            except ValueError:
                return None, None, "Invalid --rounds value; use an integer."
            i += 1
            continue
        topic_parts.extend(parts[i:])
        break
    topic = " ".join(topic_parts).strip()
    if len(participants) < 2:
        return None, None, "Debate needs at least two agents, e.g. `/roundtable debate victor,mizu <topic>`."
    if not topic:
        return None, None, "Debate needs a topic/message after the participant list."
    return participants, rounds, topic


def _current_bot_id(adapter: Any) -> Optional[str]:
    client = getattr(adapter, "_client", None)
    user = getattr(client, "user", None)
    bot_id = getattr(user, "id", None)
    return str(bot_id) if bot_id is not None else None


def _next_turn_agent(debate: Dict[str, Any]) -> Optional[str]:
    participants = list(debate.get("participants") or [])
    if not participants:
        return None
    turn_index = int(debate.get("turn_index") or 0)
    max_turns = int(debate.get("rounds") or 1) * len(participants)
    if turn_index >= max_turns:
        return None
    return str(participants[turn_index % len(participants)]).lower()


def _truncate_for_prompt(text: str, limit: int = 900) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "… [truncated]"


def _build_debate_prompt(debate: Dict[str, Any], target: str) -> str:
    participants = list(debate.get("participants") or [])
    turn_index = int(debate.get("turn_index") or 0)
    round_no = (turn_index // max(1, len(participants))) + 1
    rounds = int(debate.get("rounds") or 1)
    topic = debate.get("topic") or "(no topic)"
    transcript = list(debate.get("transcript") or [])[-3:]
    if transcript:
        context_lines = "\n".join(
            f"- {entry.get('agent')}: {_truncate_for_prompt(str(entry.get('content') or ''), 420)}"
            for entry in transcript
        )
    else:
        context_lines = "- First turn; no prior agent response yet."
    return (
        f"Roundtable debate — {target}, round {round_no}/{rounds}.\n"
        f"Topic: {topic}\n\n"
        f"Recent context:\n{context_lines}\n\n"
        "Give your position concisely. If you believe the group has reached actionable consensus, "
        "end your message with `ROUND_TABLE_DECISION: CONSENSUS`. Otherwise end with "
        "`ROUND_TABLE_DECISION: CONTINUE`. Do not mention or summon other bots directly; the "
        "roundtable orchestrator will route the next turn."
    )


async def _send_controlled_mention(
    adapter: Any,
    chat_id: str,
    bot_id: str,
    message: str,
    *,
    thread_id: Any = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    metadata = {
        "allowed_mentions_user_ids": [str(bot_id)],
        "allow_roundtable_bot_mentions": True,
    }
    if thread_id:
        metadata["thread_id"] = str(thread_id)
    if extra_metadata:
        metadata.update(extra_metadata)
    return await adapter.send(str(chat_id), f"<@{bot_id}> {message}", metadata=metadata)


async def _send_debate_turn(gateway: Any, debate: Dict[str, Any], target: str, adapter: Any = None) -> Any:
    adapter = adapter or _discord_adapter(gateway)
    if adapter is None:
        return None
    bot_id = (debate.get("agent_ids") or {}).get(target)
    if not bot_id:
        return None
    prompt = _build_debate_prompt(debate, target)
    return await _send_controlled_mention(
        adapter,
        str(debate.get("channel_id")),
        str(bot_id),
        prompt,
        thread_id=debate.get("thread_id"),
        extra_metadata={"roundtable_debate_id": debate.get("id")},
    )


def _clean_decision_text(content: str) -> str:
    text = _DECISION_LINE_RE.sub("\n", content or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or "(No final text beyond the decision marker.)"


def _format_debate_completion_notice(debate: Dict[str, Any], *, reason: str) -> str:
    transcript = list(debate.get("transcript") or [])
    turns_done = len(transcript)
    total_turns = int(debate.get("rounds") or 1) * max(1, len(debate.get("participants") or []))
    topic = debate.get("topic") or "n/a"
    if reason == "consensus":
        last = transcript[-1] if transcript else {}
        agent = last.get("agent") or "unknown"
        final = _truncate_for_prompt(_clean_decision_text(str(last.get("content") or "")), 1200)
        quoted = "\n".join(f"> {line}" if line else ">" for line in final.splitlines())
        return (
            "✅ **Roundtable consensus reached — gate closed.**\n"
            f"**Topic:** {topic}\n"
            f"**Turns:** {turns_done}/{total_turns}\n"
            f"**Final result ({agent}):**\n{quoted}\n\n"
            "No further bot-authored turns will be admitted unless you start a new debate, use `/roundtable call`, or run `/roundtable start`."
        )

    last = transcript[-1] if transcript else {}
    last_agent = last.get("agent") or "unknown"
    last_text = _truncate_for_prompt(_clean_decision_text(str(last.get("content") or "")), 900)
    quoted = "\n".join(f"> {line}" if line else ">" for line in last_text.splitlines())
    return (
        "🏁 **Roundtable round limit reached — gate closed.**\n"
        f"**Topic:** {topic}\n"
        f"**Turns:** {turns_done}/{total_turns}\n"
        f"**Last turn ({last_agent}):**\n{quoted}\n\n"
        "If the result is unclear, restart with a narrower topic."
    )


async def _send_debate_notice(gateway: Any, debate: Dict[str, Any], content: str, adapter: Any = None) -> None:
    adapter = adapter or _discord_adapter(gateway)
    if adapter is None:
        return
    metadata = {"allow_roundtable_bot_mentions": False, "roundtable_debate_id": debate.get("id")}
    if debate.get("thread_id"):
        metadata["thread_id"] = str(debate.get("thread_id"))
    await adapter.send(str(debate.get("channel_id")), content, metadata=metadata)


async def _handle_roundtable_debate(rest: str, *, gateway: Any = None, event: Any = None) -> str:
    parsed_participants, rounds, topic_or_error = _parse_debate_args(rest)
    if parsed_participants is None:
        return str(topic_or_error)
    participants = parsed_participants
    topic = str(topic_or_error)

    source = getattr(event, "source", None)
    platform = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", ""))
    if str(platform).lower() != "discord":
        return "/roundtable debate is only available from Discord."
    channels = _configured_channels(gateway)
    source_ids = _source_channel_ids(source)
    if channels and not (source_ids & channels):
        return "/roundtable debate is only available in configured roundtable channels."

    agents = _configured_agents(gateway)
    unknown = [name for name in participants if name not in agents]
    if unknown:
        known = ", ".join(sorted(agents)) or "none configured"
        return f"Unknown roundtable agent(s): {', '.join(unknown)}. Known agents: {known}"

    adapter = _discord_adapter(gateway)
    if adapter is None:
        return "Discord adapter is not available for /roundtable debate."
    chat_id = str(getattr(source, "chat_id", "") or "").strip()
    if not chat_id:
        return "Could not determine the current Discord channel for /roundtable debate."

    state = _read_state()
    existing = state.get("debate") if isinstance(state.get("debate"), dict) else None
    if existing and existing.get("active") and str(existing.get("channel_id")) == chat_id:
        return "A roundtable debate is already active in this channel. Use `/roundtable stop` to cancel it first."

    # If the command was invoked on a participant's own bot, rotate so the first
    # Discord mention wakes another bot rather than trying to summon itself.
    own_bot_id = _current_bot_id(adapter)
    if own_bot_id and agents.get(participants[0]) == own_bot_id and len(participants) > 1:
        participants = participants[1:] + participants[:1]

    debate = {
        "id": f"debate-{uuid.uuid4().hex[:10]}",
        "active": True,
        "channel_id": chat_id,
        "thread_id": str(getattr(source, "thread_id", "") or "") or None,
        "participants": participants,
        "agent_ids": {name: agents[name] for name in participants},
        "topic": topic,
        "rounds": rounds,
        "turn_index": 0,
        "transcript": [],
        "started_at": _now(),
        "started_by": getattr(source, "user_id", None),
    }
    state["enabled"] = True
    state["reason"] = "debate-active"
    state["updated_at"] = _now()
    state["updated_from"] = "roundtable-debate"
    state["debate"] = debate
    _write_full_state(state)

    first = _next_turn_agent(debate)
    result = await _send_debate_turn(gateway, debate, first) if first else None
    if result is not None and not getattr(result, "success", False):
        debate["active"] = False
        debate["stop_reason"] = "send-failed"
        state["debate"] = debate
        _write_full_state(state)
        return f"✗ Failed to start debate: {getattr(result, 'error', 'unknown error')}"
    return (
        f"Debate started ({debate['id']}): {' → '.join(participants)}; "
        f"rounds={rounds}; topic={topic}"
    )


async def _post_gateway_send(
    *,
    platform: str = "",
    chat_id: str = "",
    content: str = "",
    sender_bot_id: str = "",
    message_id: str = "",
    gateway: Any = None,
    adapter: Any = None,
    **_: Any,
) -> None:
    if str(platform).lower() != "discord" or not sender_bot_id:
        return
    state = _read_state()
    debate = state.get("debate") if isinstance(state.get("debate"), dict) else None
    if not debate or not debate.get("active"):
        return
    if str(debate.get("channel_id")) != str(chat_id):
        return
    expected = _next_turn_agent(debate)
    agent_ids = debate.get("agent_ids") or {}
    if not expected or str(agent_ids.get(expected)) != str(sender_bot_id):
        return

    transcript = list(debate.get("transcript") or [])
    transcript.append({
        "agent": expected,
        "content": content,
        "message_id": message_id or None,
        "at": _now(),
    })
    debate["transcript"] = transcript
    debate["turn_index"] = int(debate.get("turn_index") or 0) + 1
    debate["last_agent"] = expected
    debate["last_message_id"] = message_id or None

    if _CONSENSUS_RE.search(content or ""):
        debate["active"] = False
        debate["stop_reason"] = "consensus"
        debate["stopped_at"] = _now()
        state["enabled"] = False
        state["reason"] = "debate-consensus"
        state["updated_at"] = _now()
        state["updated_from"] = "roundtable-orchestrator"
        state["debate"] = debate
        _write_full_state(state)
        await _send_debate_notice(
            gateway,
            debate,
            _format_debate_completion_notice(debate, reason="consensus"),
            adapter=adapter,
        )
        return

    next_agent = _next_turn_agent(debate)
    if not next_agent:
        debate["active"] = False
        debate["stop_reason"] = "max-turns"
        debate["stopped_at"] = _now()
        state["enabled"] = False
        state["reason"] = "debate-max-turns"
        state["updated_at"] = _now()
        state["updated_from"] = "roundtable-orchestrator"
        state["debate"] = debate
        _write_full_state(state)
        await _send_debate_notice(
            gateway,
            debate,
            _format_debate_completion_notice(debate, reason="max-turns"),
            adapter=adapter,
        )
        return

    state["debate"] = debate
    _write_full_state(state)
    await _send_debate_turn(gateway, debate, next_agent, adapter=adapter)



def _handle_roundtable_command(
    args: str = "", *, session_id: str = "", gateway: Any = None, event: Any = None, **_: Any
) -> Any:
    sub, reason = _parse_command(args)
    if sub in {"help", "-h", "--help"}:
        return _HELP
    if sub in {"status", ""}:
        return _format_status(_read_state(), gateway)
    updated_from = f"session:{session_id}" if session_id else "gateway-command"
    if sub in {"stop", "off", "disable", "pause"}:
        state = _write_state(False, reason=reason or "operator-stop", updated_from=updated_from)
        return "Stopped.\n" + _format_status(state, gateway)
    if sub in {"start", "on", "enable", "resume"}:
        state = _write_state(True, reason=reason or "operator-start", updated_from=updated_from)
        return "Roundtable enabled.\n" + _format_status(state, gateway)
    if sub in {"call", "summon", "page"}:
        return _handle_roundtable_call(reason, gateway=gateway, event=event)
    if sub in {"debate", "discuss"}:
        return _handle_roundtable_debate(reason, gateway=gateway, event=event)
    return f"Unknown roundtable subcommand: {sub}\n\n{_HELP}"


def _pre_gateway_dispatch(event: Any = None, gateway: Any = None, **_: Any) -> Optional[Dict[str, str]]:
    """Drop admitted Discord bot turns while the shared circuit breaker is stopped."""
    if event is None or not _roundtable_applies(event, gateway):
        return None
    state = _read_state()
    if state.get("enabled"):
        return None
    if _consume_pending_call_if_matches(state, event):
        return None
    return {
        "action": "skip",
        "reason": "roundtable-stopped",
    }


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_hook("post_gateway_send", _post_gateway_send)
    ctx.register_command(
        "roundtable",
        handler=_handle_roundtable_command,
        description="Control the shared Discord multi-agent roundtable circuit breaker.",
        args_hint="<status|stop|start|call|debate> [args]",
        subcommands=("status", "stop", "start", "call", "summon", "debate", "discuss"),
        category="Gateway",
        gateway_only=True,
    )
