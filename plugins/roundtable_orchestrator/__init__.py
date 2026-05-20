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
import tempfile
from datetime import datetime, timezone
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
    "Usage: /roundtable <status|stop|start|call> [target] [message]\n"
    "• stop — disable bot-authored roundtable turns before LLM dispatch\n"
    "• start — re-enable the plugin gate (Discord allow_bots still applies)\n"
    "• status — show shared state\n"
    "• call <agent> <message> — send one controlled Discord mention to an agent"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    state = {
        "version": _STATE_VERSION,
        "enabled": bool(enabled),
        "reason": reason or ("manual-start" if enabled else "manual-stop"),
        "updated_at": _now(),
        "updated_by": updated_by or None,
        "updated_from": updated_from or None,
    }
    _atomic_write_json(_state_path(), state)
    return state


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
    return (
        f"Roundtable is **{status}**.\n"
        f"Channels: {channel_text}\n"
        f"Reason: {reason}\n"
        f"Updated: {updated}\n"
        "Note: Discord `allow_bots` still controls whether bot-authored messages reach this plugin."
    )


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

    metadata = {
        "allowed_mentions_user_ids": [bot_id],
        "allow_roundtable_bot_mentions": True,
    }
    thread_id = getattr(source, "thread_id", None)
    if thread_id:
        metadata["thread_id"] = str(thread_id)

    result = await adapter.send(chat_id, f"<@{bot_id}> {message}", metadata=metadata)
    if not getattr(result, "success", False):
        error = getattr(result, "error", "unknown error")
        return f"✗ Failed to call {target}: {error}"
    message_id = getattr(result, "message_id", None) or "sent"
    return f"Called {target} in <#{chat_id}> (message {message_id})."


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
    return f"Unknown roundtable subcommand: {sub}\n\n{_HELP}"


def _pre_gateway_dispatch(event: Any = None, gateway: Any = None, **_: Any) -> Optional[Dict[str, str]]:
    """Drop admitted Discord bot turns while the shared circuit breaker is stopped."""
    if event is None or not _roundtable_applies(event, gateway):
        return None
    state = _read_state()
    if state.get("enabled"):
        return None
    return {
        "action": "skip",
        "reason": "roundtable-stopped",
    }


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_command(
        "roundtable",
        handler=_handle_roundtable_command,
        description="Control the shared Discord multi-agent roundtable circuit breaker.",
        args_hint="<status|stop|start|call> [target] [message]",
        subcommands=("status", "stop", "start", "call", "summon"),
        category="Gateway",
        gateway_only=True,
    )
