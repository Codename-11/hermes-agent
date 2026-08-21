"""
Discord ↔ Lucid slash commands (/remember, /recall, /classify).

Registered as *guild-scoped* slash commands so they update instantly on
restart (global commands take up to ~1h to propagate). Default guild is
the Axiom-Labs server (``470347445604188170``), overridable via the env
var ``HERMES_DISCORD_LUCID_GUILD_ID``.

Access-controlled: only Bailey (``470346695587135489``) can invoke them
by default. Expand via the env var ``HERMES_DISCORD_LUCID_ALLOWED_USERS``
(comma-separated Discord user IDs).

All HTTP calls hit the Lucid REST server at ``LUCID_API_URL``
(default ``http://localhost:8443``) and carry the audit-context headers
documented in the Lucid REST README (``X-Lucid-Method``, ``X-Lucid-Actor-Type``,
``X-Lucid-Actor-Id``, ``X-Lucid-Session-Id``).

This module is intentionally *decoupled* from the main ``discord.py``
adapter file so the Lucid integration is easy to reason about, enable,
or rip out. The adapter only calls ``register_lucid_commands`` once on
startup and optionally ``sync_lucid_guild`` once after connect.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Optional

try:  # Module-level import so ``typing.get_type_hints`` can resolve the
      # discord.py symbols used in slash-command annotations. If the
      # library is missing the adapter file won't import us anyway.
    import discord  # type: ignore
    DISCORD_AVAILABLE = True
except ImportError:  # pragma: no cover - defensive
    discord = None  # type: ignore
    DISCORD_AVAILABLE = False

logger = logging.getLogger(__name__)

# ─── Constants / defaults ────────────────────────────────────────────────

DEFAULT_GUILD_ID = 470347445604188170  # Axiom-Labs Discord server
DEFAULT_LUCID_API_URL = "http://localhost:8443"
BAILEY_DISCORD_ID = "470346695587135489"
BAILEY_OWNER_ID = "bailey"

MEMORY_TYPES = ("observation", "fact", "idea", "decision", "preference", "event")
STATES = ("active", "incubating", "crystallized", "contested", "actionable", "dormant")


# ─── Helpers ─────────────────────────────────────────────────────────────


def _guild_id() -> int:
    raw = os.getenv("HERMES_DISCORD_LUCID_GUILD_ID", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "HERMES_DISCORD_LUCID_GUILD_ID=%r is not an int — using default %d",
                raw, DEFAULT_GUILD_ID,
            )
    return DEFAULT_GUILD_ID


def _lucid_url() -> str:
    return os.getenv("LUCID_API_URL", DEFAULT_LUCID_API_URL).rstrip("/")


def _allowed_user_ids() -> set[str]:
    """Discord user IDs permitted to invoke Lucid commands."""
    base = {BAILEY_DISCORD_ID}
    extra = os.getenv("HERMES_DISCORD_LUCID_ALLOWED_USERS", "")
    for entry in extra.split(","):
        entry = entry.strip()
        if entry:
            base.add(entry)
    return base


def _resolve_owner_id(discord_user_id: str) -> str:
    """Map a Discord user id to a Lucid ``owner_id``.

    Bailey → ``bailey`` (canonical Lucid identity). Everyone else gets a
    namespaced ``discord:<id>`` value — they won't typically hit these
    commands anyway because of the access allowlist, but keeping the
    mapping explicit future-proofs it for when we open access up.
    """
    if discord_user_id == BAILEY_DISCORD_ID:
        return BAILEY_OWNER_ID
    return f"discord:{discord_user_id}"


def _is_authorized(user_id: str) -> bool:
    return user_id in _allowed_user_ids()


def _test_mode() -> bool:
    return os.getenv("DISCORD_REMEMBER_TEST_MODE", "").lower() in ("1", "true", "yes")


def _profile_is_allowed() -> bool:
    """Is the current Hermes profile allowed to own the Lucid commands?

    All profiles register by default. Each gateway (Victor on
    ``~/.hermes``, Mizu on ``~/.hermes/profiles/mizu``) owns its own
    Discord bot, so identically named commands never collide inside a
    single channel — Discord only surfaces the commands of bots present
    in the user's current channel. Audit context stays clean because
    each gateway emits its own ``X-Lucid-Session-Id`` and resolves the
    invoker to the correct Lucid ``owner_id`` (e.g. Bailey → ``bailey``).

    Set ``HERMES_DISCORD_LUCID_ENABLED=0`` to opt a specific profile
    out entirely. The legacy ``HERMES_DISCORD_LUCID_ALLOW_ALL_PROFILES``
    env var is accepted for backward compat but no longer required.
    """
    return True


def _audit_headers(owner_id: str, channel_id: str) -> dict[str, str]:
    headers = {
        "X-Lucid-Method": "discord",
        "X-Lucid-Actor-Type": "human",
        "X-Lucid-Actor-Id": owner_id,
        "X-Lucid-Session-Id": f"discord:{channel_id}",
    }
    if _test_mode():
        headers["X-Lucid-Test-Mode"] = "1"
    return headers


async def _http_request(
    method: str,
    path: str,
    *,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 15.0,
) -> tuple[int, Any]:
    """Minimal aiohttp wrapper. Returns (status, json-or-text)."""
    import aiohttp
    url = f"{_lucid_url()}{path}"
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.request(
                method, url, json=json_body, params=params, headers=headers or {},
            ) as resp:
                status = resp.status
                # Best-effort JSON parse; fall back to text.
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = await resp.text()
                return status, data
    except aiohttp.ClientConnectorError as e:
        return 0, f"connection failed: {e}"
    except Exception as e:  # pragma: no cover - defensive
        return 0, f"request failed: {e}"


def _truncate(text: str, n: int = 100) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ─── Command registration ────────────────────────────────────────────────


def register_lucid_commands(client, tree) -> bool:
    """Attach /remember, /recall, /classify to ``tree`` scoped to the guild.

    Returns True if commands were registered, False if the discord.py
    library was unavailable (in which case the adapter shouldn't have
    gotten this far anyway).

    Safe to call once on adapter startup. Commands are guild-scoped so
    repeated registrations across restarts are idempotent (Discord
    de-duplicates by name within a guild on sync).
    """
    if not DISCORD_AVAILABLE:
        logger.warning("[Lucid] discord.py not available; skipping command registration")
        return False

    if os.getenv("HERMES_DISCORD_LUCID_ENABLED", "1").lower() in ("0", "false", "no"):
        logger.info("[Lucid] HERMES_DISCORD_LUCID_ENABLED=0 — skipping command registration")
        return False

    # Only register on the main Hermes profile (Victor) by default — two
    # bots claiming the same /remember would create duplicate entries in
    # the Discord UI.  Other profiles (e.g. Mizu) are opted-in via
    # HERMES_DISCORD_LUCID_ALLOW_ALL_PROFILES=1.
    if not _profile_is_allowed():
        logger.info(
            "[Lucid] Profile is not the primary Hermes home — skipping command "
            "registration (set HERMES_DISCORD_LUCID_ALLOW_ALL_PROFILES=1 to override)"
        )
        return False

    guild_obj = discord.Object(id=_guild_id())

    # ── Choice lists ─────────────────────────────────────────────────
    memory_type_choices = [
        discord.app_commands.Choice(name=t, value=t) for t in MEMORY_TYPES
    ]
    state_choices = [
        discord.app_commands.Choice(name=s, value=s) for s in STATES
    ]

    # ─────────────────── DELETE button (ephemeral) ───────────────────

    class DeleteMemoryView(discord.ui.View):
        """One-shot button to delete the just-created memory."""

        def __init__(self, memory_id: int, owner_id: str, channel_id: str,
                     invoker_id: str):
            super().__init__(timeout=300)  # 5 min
            self.memory_id = memory_id
            self.owner_id = owner_id
            self.channel_id = channel_id
            self.invoker_id = invoker_id

        @discord.ui.button(
            label="Delete", style=discord.ButtonStyle.red, emoji="🗑️",
        )
        async def delete_button(
            self, interaction: discord.Interaction, button: discord.ui.Button,
        ):
            # Only the original invoker can delete; Discord shouldn't ever
            # route someone else's click here on an ephemeral but double-check.
            if str(interaction.user.id) != self.invoker_id:
                await interaction.response.send_message(
                    "Only the original invoker can delete this memory.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            status, data = await _http_request(
                "DELETE",
                f"/api/memory/{self.memory_id}",
                headers=_audit_headers(self.owner_id, self.channel_id),
            )
            if 200 <= status < 300:
                button.disabled = True
                button.label = "Deleted"
                try:
                    await interaction.edit_original_response(
                        content=f"Memory #{self.memory_id} deleted.",
                        embed=None,
                        view=self,
                    )
                except Exception:
                    await interaction.followup.send(
                        f"Memory #{self.memory_id} deleted.",
                        ephemeral=True,
                    )
            else:
                await interaction.followup.send(
                    f"Delete failed ({status}): {data}", ephemeral=True,
                )

    # ─────────────────── /remember ───────────────────

    @tree.command(
        name="remember",
        description="Save a memory to Lucid",
        guild=guild_obj,
    )
    @discord.app_commands.describe(
        text="The memory to save",
        type="Archetype (default: observation)",
        state="Override auto-default state for this archetype",
        workspace="Optional workspace/tenant override",
    )
    @discord.app_commands.choices(type=memory_type_choices, state=state_choices)
    async def slash_remember(
        interaction: discord.Interaction,
        text: str,
        type: Optional[discord.app_commands.Choice[str]] = None,
        state: Optional[discord.app_commands.Choice[str]] = None,
        workspace: Optional[str] = None,
    ):
        uid = str(interaction.user.id)
        if not _is_authorized(uid):
            await interaction.response.send_message(
                "Not authorized to use Lucid commands.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        owner_id = _resolve_owner_id(uid)
        channel_id = str(interaction.channel_id or "")
        memory_type = (type.value if type else "observation")
        body: dict[str, Any] = {
            "text": text,
            "memory_type": memory_type,
            "source": "human",
            "authored_by": "human",
            "owner_id": owner_id,
        }
        if state:
            body["state"] = state.value
        if workspace:
            body["workspace"] = workspace

        status, data = await _http_request(
            "POST", "/api/memory",
            json_body=body,
            headers=_audit_headers(owner_id, channel_id),
        )

        if not (200 <= status < 300) or not isinstance(data, dict) or not data.get("ok", True):
            err = data if isinstance(data, str) else (data.get("message") or data.get("error") or data)
            await interaction.edit_original_response(
                content=f"Failed to save memory ({status}): `{err}`",
            )
            return

        # Response shape varies by Lucid version; extract memory id
        # defensively.
        mem_id = (
            data.get("memory_id")
            or data.get("id")
            or (data.get("memory") or {}).get("id")
        )
        effective_state = (
            data.get("state")
            or (data.get("memory") or {}).get("state")
            or body.get("state")
            or "(default)"
        )

        embed = discord.Embed(
            title=f"Memory #{mem_id}" if mem_id else "Memory saved",
            description=_truncate(text, 200),
            color=discord.Color.green(),
        )
        embed.add_field(name="Archetype", value=memory_type, inline=True)
        embed.add_field(name="State", value=str(effective_state), inline=True)
        if workspace:
            embed.add_field(name="Workspace", value=workspace, inline=True)
        embed.set_footer(text=f"owner: {owner_id} · source: human")

        view: Optional[discord.ui.View] = None
        if mem_id:
            try:
                view = DeleteMemoryView(
                    int(mem_id), owner_id, channel_id, uid,
                )
            except (TypeError, ValueError):
                view = None

        await interaction.edit_original_response(
            content=None, embed=embed, view=view,
        )

    # ─────────────────── /recall ───────────────────

    @tree.command(
        name="recall",
        description="Search Lucid memories",
        guild=guild_obj,
    )
    @discord.app_commands.describe(
        query="What to search for",
        limit="Max results (1–10, default 5)",
    )
    async def slash_recall(
        interaction: discord.Interaction,
        query: str,
        limit: int = 5,
    ):
        uid = str(interaction.user.id)
        if not _is_authorized(uid):
            await interaction.response.send_message(
                "Not authorized to use Lucid commands.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        owner_id = _resolve_owner_id(uid)
        channel_id = str(interaction.channel_id or "")
        limit = max(1, min(10, int(limit or 5)))

        status, data = await _http_request(
            "GET", "/api/search",
            params={"q": query},
            headers=_audit_headers(owner_id, channel_id),
        )

        if not (200 <= status < 300) or not isinstance(data, dict):
            err = data if isinstance(data, str) else str(data)
            await interaction.edit_original_response(
                content=f"Search failed ({status}): `{err}`",
            )
            return

        results = data.get("results") or []
        results = results[:limit]

        embed = discord.Embed(
            title=f"Lucid recall: {_truncate(query, 80)}",
            color=discord.Color.blue(),
        )
        if not results:
            embed.description = "_No matches._"
        else:
            for r in results:
                mid = r.get("id")
                label = r.get("label") or ""
                snippet = r.get("snippet") or label or "_(no snippet)_"
                embed.add_field(
                    name=f"#{mid}" + (f" — {_truncate(label, 60)}" if label else ""),
                    value=_truncate(snippet, 300),
                    inline=False,
                )
        embed.set_footer(text=f"{len(results)} result(s) · owner: {owner_id}")
        await interaction.edit_original_response(content=None, embed=embed)

    # ─────────────────── /classify ───────────────────

    @tree.command(
        name="classify",
        description="Reclassify a Lucid memory's archetype and/or state",
        guild=guild_obj,
    )
    @discord.app_commands.describe(
        memory_id="Numeric memory id (from /remember or /recall)",
        type="New archetype",
        state="New state",
        reason="Optional reason for the change",
    )
    @discord.app_commands.choices(type=memory_type_choices, state=state_choices)
    async def slash_classify(
        interaction: discord.Interaction,
        memory_id: int,
        type: Optional[discord.app_commands.Choice[str]] = None,
        state: Optional[discord.app_commands.Choice[str]] = None,
        reason: Optional[str] = None,
    ):
        uid = str(interaction.user.id)
        if not _is_authorized(uid):
            await interaction.response.send_message(
                "Not authorized to use Lucid commands.", ephemeral=True,
            )
            return

        if not type and not state:
            await interaction.response.send_message(
                "Provide at least one of `type` or `state`.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        owner_id = _resolve_owner_id(uid)
        channel_id = str(interaction.channel_id or "")

        body: dict[str, Any] = {}
        if type:
            body["memory_type"] = type.value
        if state:
            body["state"] = state.value
        if reason:
            body["reason"] = reason

        status, data = await _http_request(
            "PUT", f"/api/memory/{memory_id}/classify",
            json_body=body,
            headers=_audit_headers(owner_id, channel_id),
        )

        if not (200 <= status < 300) or (
            isinstance(data, dict) and data.get("ok") is False
        ):
            err = data if isinstance(data, str) else (
                data.get("message") or data.get("error") or data
            )
            await interaction.edit_original_response(
                content=f"Classify failed ({status}): `{err}`",
            )
            return

        embed = discord.Embed(
            title=f"Reclassified #{memory_id}",
            color=discord.Color.gold(),
        )
        if type:
            embed.add_field(name="Archetype", value=type.value, inline=True)
        if state:
            embed.add_field(name="State", value=state.value, inline=True)
        if reason:
            embed.add_field(name="Reason", value=_truncate(reason, 200), inline=False)
        if isinstance(data, dict) and data.get("noop"):
            embed.set_footer(text="noop: values already matched")
        await interaction.edit_original_response(content=None, embed=embed)

    logger.info(
        "[Lucid] Registered /remember, /recall, /classify (guild=%s, api=%s)",
        guild_obj.id, _lucid_url(),
    )
    return True


async def sync_lucid_guild(client) -> None:
    """Sync the Lucid guild-scoped command set.

    The main adapter already does a global ``tree.sync()``; guild-scoped
    commands live in a separate command space and need their own sync
    call. Called from ``_run_post_connect_initialization``.
    """
    if not DISCORD_AVAILABLE:
        return
    if os.getenv("HERMES_DISCORD_LUCID_ENABLED", "1").lower() in ("0", "false", "no"):
        return
    if not _profile_is_allowed():
        return
    if not client or not getattr(client, "tree", None):
        return
    import asyncio
    guild_obj = discord.Object(id=_guild_id())
    try:
        synced = await asyncio.wait_for(
            client.tree.sync(guild=guild_obj), timeout=30,
        )
        logger.info(
            "[Lucid] Synced %d guild-scoped slash command(s) to guild %s",
            len(synced), guild_obj.id,
        )
    except asyncio.TimeoutError:
        logger.warning("[Lucid] Guild slash command sync timed out after 30s")
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[Lucid] Guild slash command sync failed: %s", e)
