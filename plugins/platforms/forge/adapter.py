"""Forge platform adapter for Hermes webhook chat delivery.

Forge chat messages arrive through the generic webhook adapter.  This platform
adapter is the outbound half: when the webhook run finishes, Hermes calls
``send(chat_id=<ChatThread.id>, content=<final response>)`` and this adapter
appends the final AGENT message to Forge via the MCP ``chat.appendMessage``
tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, PlatformConfig, SendResult

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://forge.axiom-labs.dev"


def _clean_base_url(value: str | None) -> str:
    return (value or DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL


def check_requirements() -> bool:
    """Return True when enough credentials exist to deliver Forge chat replies."""
    return bool(os.getenv("FORGE_API_KEY"))


def _api_key_from_config(config: PlatformConfig) -> str:
    return (
        str(getattr(config, "api_key", None) or "").strip()
        or str(getattr(config, "token", None) or "").strip()
        or os.getenv("FORGE_API_KEY", "").strip()
    )


def _base_url_from_config(config: PlatformConfig) -> str:
    extra = getattr(config, "extra", {}) or {}
    return _clean_base_url(
        str(extra.get("url") or "").strip()
        or str(extra.get("base_url") or "").strip()
        or os.getenv("FORGE_BASE_URL")
    )


def validate_config(config: PlatformConfig) -> bool:
    return bool(_api_key_from_config(config))


def is_connected(config: PlatformConfig) -> bool:
    return bool(getattr(config, "enabled", False)) and validate_config(config)


def _env_enablement() -> Dict[str, Any]:
    if not os.getenv("FORGE_API_KEY"):
        return {}
    return {
        "url": _clean_base_url(os.getenv("FORGE_BASE_URL")),
        "streaming": True,
        "handle_chat_message_posted": True,
    }


class ForgeAdapter(BasePlatformAdapter):
    """Outbound Forge platform adapter with streaming draft support.

    Incoming Forge events are handled by the webhook adapter, not here. This
    adapter is the outbound half — three call shapes:

      - ``send(chat_id, content)``: when there's no active draft for this
        thread, posts via ``chat.appendMessage``. When a draft *is* active
        (because send_draft was called during streaming), the final ``send``
        commits the accumulated body via ``chat.finalizeDraft``, which both
        clears the streaming-preview bubble client-side and persists the
        final AGENT message in a single round-trip.
      - ``send_draft(chat_id, draft_id, content)``: incremental streaming
        path. First call per ``(chat_id, draft_id)`` opens a Forge draft via
        ``chat.startDraft``; subsequent calls compute the delta against the
        last-sent content and broadcast it via ``chat.appendDraftChunk`` so
        the Forge UI animates a typewriter preview.
      - Fallback: if streaming ever raises, we silently degrade to
        ``chat.appendMessage`` so the operator still gets the reply.
    """

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("forge"))
        self.api_key = _api_key_from_config(config)
        self.base_url = _base_url_from_config(config)
        self.rpc_url = f"{self.base_url}/api/mcp/rpc"
        # Active draft state, keyed by (chat_id, draft_id) → {forge_draft_id, last_body}.
        # Cleared on finalize OR on a fallback to appendMessage.
        self._drafts: Dict[tuple, Dict[str, Any]] = {}

    async def connect(self) -> bool:
        if not self.api_key:
            self._set_fatal_error("missing_api_key", "FORGE_API_KEY is required", retryable=False)
            return False
        self._mark_connected()
        logger.info("[Forge] Connected for outbound chat delivery (%s)", self.base_url)
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()
        logger.info("[Forge] Disconnected")

    def supports_draft_streaming(
        self,
        chat_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Forge supports the chat.startDraft/appendDraftChunk/finalizeDraft
        streaming protocol on every ChatThread. There is no per-thread
        capability gate, so we always return True.
        """
        return True

    async def send_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        thread_id = str(chat_id or "").strip()
        body = str(content or "")
        if not thread_id:
            return SendResult(success=False, error="Missing Forge thread id")
        key = (thread_id, int(draft_id))
        state = self._drafts.get(key)

        try:
            if state is None:
                # First chunk for this (chat, draft_id) — open the draft.
                start = await asyncio.to_thread(self._call_tool, "chat.startDraft", {
                    "threadId": thread_id,
                })
                if not isinstance(start, dict) or not start.get("draftId"):
                    raise RuntimeError(f"chat.startDraft returned no draftId: {start!r}")
                forge_draft_id = str(start["draftId"])
                state = {"forge_draft_id": forge_draft_id, "last_body": ""}
                self._drafts[key] = state
            # appendDraftChunk wants the *delta* — compute against last_body so
            # repeated send_draft calls with growing content animate naturally.
            last_body = str(state.get("last_body") or "")
            delta = body[len(last_body):] if body.startswith(last_body) else body
            if delta:
                await asyncio.to_thread(self._call_tool, "chat.appendDraftChunk", {
                    "threadId": thread_id,
                    "draftId": state["forge_draft_id"],
                    "delta": delta,
                })
            state["last_body"] = body
            return SendResult(success=True)
        except Exception as exc:
            logger.exception("[Forge] send_draft failed; clearing draft state for thread %s", thread_id)
            # Drop the draft so the final send() falls through to appendMessage.
            self._drafts.pop(key, None)
            return SendResult(success=False, error=str(exc))

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        thread_id = str(chat_id or "").strip()
        body = str(content or "").strip()
        if not thread_id:
            return SendResult(success=False, error="Missing Forge thread id")
        if not body or body == "[SILENT]":
            # Treat explicit silence as delivered so echoed AGENT events do not
            # create empty/noisy chat messages.
            self._cleanup_drafts_for_thread(thread_id)
            return SendResult(success=True)

        # If a draft is outstanding for this thread, finalize via chat.finalizeDraft
        # so the streaming bubble swaps cleanly to the persisted message instead
        # of leaving the preview hanging client-side and posting a duplicate.
        active = self._pop_first_draft_for_thread(thread_id)
        if active is not None:
            try:
                result = await asyncio.to_thread(self._call_tool, "chat.finalizeDraft", {
                    "threadId": thread_id,
                    "draftId": active["forge_draft_id"],
                    "body": body,
                })
                message_id = self._extract_message_id(result)
                logger.info("[Forge] Finalized draft on thread %s", thread_id)
                return SendResult(success=True, message_id=message_id)
            except Exception:
                # Fall through to appendMessage so the user still gets the reply.
                logger.exception("[Forge] chat.finalizeDraft failed; falling back to appendMessage")

        try:
            result = await asyncio.to_thread(self._call_tool, "chat.appendMessage", {
                "threadId": thread_id,
                "body": body,
            })
        except Exception as exc:
            logger.exception("[Forge] Failed to append chat message to thread %s", thread_id)
            return SendResult(success=False, error=str(exc))

        message_id = self._extract_message_id(result)
        logger.info("[Forge] Appended chat message to thread %s", thread_id)
        return SendResult(success=True, message_id=message_id)

    def _extract_message_id(self, result: Any) -> Optional[str]:
        try:
            if isinstance(result, dict):
                return str(result.get("id") or result.get("messageId") or "") or None
        except Exception:
            return None
        return None

    def _pop_first_draft_for_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        for key in list(self._drafts.keys()):
            if key[0] == thread_id:
                return self._drafts.pop(key, None)
        return None

    def _cleanup_drafts_for_thread(self, thread_id: str) -> None:
        for key in list(self._drafts.keys()):
            if key[0] == thread_id:
                self._drafts.pop(key, None)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"id": chat_id, "name": f"Forge thread {chat_id}", "type": "forge_thread"}

    def _call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.api_key}",
        }
        req = urllib.request.Request(
            self.rpc_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Forge MCP HTTP {exc.code}: {detail}") from exc

        data = json.loads(raw)
        if data.get("error"):
            raise RuntimeError(f"Forge MCP error: {data['error']}")
        result = data.get("result") or {}
        content = result.get("content") or []
        if content and isinstance(content, list):
            text = content[0].get("text") if isinstance(content[0], dict) else None
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
        return result


def register(ctx):
    """Plugin entry point: called by the Hermes plugin system."""
    ctx.register_platform(
        name="forge",
        label="Forge",
        adapter_factory=lambda cfg: ForgeAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        env_enablement_fn=_env_enablement,
        required_env=["FORGE_API_KEY"],
        install_hint="Set FORGE_API_KEY and optional FORGE_BASE_URL in the Hermes profile .env",
        emoji="🛠️",
        pii_safe=False,
        allow_update_command=False,
    )
