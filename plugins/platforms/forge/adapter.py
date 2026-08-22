"""Forge platform adapter for Hermes webhook chat delivery.

Forge chat messages arrive through the generic webhook adapter. This platform
adapter is the outbound half: completed and streaming responses are persisted
to Forge through its MCP chat tools.
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
    """Return whether environment credentials can deliver Forge replies."""
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
    """Outbound Forge adapter with native streaming-draft support."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("forge"))
        self.api_key = _api_key_from_config(config)
        self.base_url = _base_url_from_config(config)
        self.rpc_url = f"{self.base_url}/api/mcp/rpc"
        self._drafts: Dict[tuple[str, int], Dict[str, Any]] = {}

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self.api_key:
            self._set_fatal_error(
                "missing_api_key",
                "FORGE_API_KEY is required",
                retryable=False,
            )
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
        chat_id: Optional[str] = None,
    ) -> bool:
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
        reply_to_message_id = str(
            (metadata or {}).get("reply_to_message_id") or ""
        ).strip()

        try:
            if state is None:
                start_args = {"threadId": thread_id}
                if reply_to_message_id:
                    start_args["replyToMessageId"] = reply_to_message_id
                start = await asyncio.to_thread(
                    self._call_tool,
                    "chat.startDraft",
                    start_args,
                )
                if not isinstance(start, dict) or not start.get("draftId"):
                    raise RuntimeError(f"chat.startDraft returned no draftId: {start!r}")
                state = {
                    "forge_draft_id": str(start["draftId"]),
                    "last_body": "",
                    "reply_to_message_id": reply_to_message_id,
                }
                self._drafts[key] = state

            last_body = str(state.get("last_body") or "")
            delta = body[len(last_body) :] if body.startswith(last_body) else body
            if delta:
                await asyncio.to_thread(
                    self._call_tool,
                    "chat.appendDraftChunk",
                    {
                        "threadId": thread_id,
                        "draftId": state["forge_draft_id"],
                        "delta": delta,
                    },
                )
            state["last_body"] = body
            return SendResult(success=True)
        except Exception as exc:
            logger.exception(
                "[Forge] send_draft failed; clearing draft state for thread %s",
                thread_id,
            )
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
            self._cleanup_drafts_for_thread(thread_id)
            return SendResult(success=True)

        active = self._pop_first_draft_for_thread(thread_id)
        if active is not None:
            try:
                result = await asyncio.to_thread(
                    self._call_tool,
                    "chat.finalizeDraft",
                    {
                        "threadId": thread_id,
                        "draftId": active["forge_draft_id"],
                        "body": body,
                    },
                )
                logger.info("[Forge] Finalized draft on thread %s", thread_id)
                return SendResult(
                    success=True,
                    message_id=self._extract_message_id(result),
                )
            except Exception:
                logger.exception(
                    "[Forge] chat.finalizeDraft failed; falling back to appendMessage"
                )

        try:
            append_args = {"threadId": thread_id, "body": body}
            reply_to_message_id = str(
                reply_to or (metadata or {}).get("reply_to_message_id") or ""
            ).strip()
            if reply_to_message_id:
                append_args["replyToMessageId"] = reply_to_message_id
            result = await asyncio.to_thread(
                self._call_tool,
                "chat.appendMessage",
                append_args,
            )
        except Exception as exc:
            logger.exception(
                "[Forge] Failed to append chat message to thread %s",
                thread_id,
            )
            return SendResult(success=False, error=str(exc))

        logger.info("[Forge] Appended chat message to thread %s", thread_id)
        return SendResult(success=True, message_id=self._extract_message_id(result))

    @staticmethod
    def _extract_message_id(result: Any) -> Optional[str]:
        try:
            if isinstance(result, dict):
                return str(result.get("id") or result.get("messageId") or "") or None
        except Exception:
            return None
        return None

    def _pop_first_draft_for_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        for key in list(self._drafts):
            if key[0] == thread_id:
                return self._drafts.pop(key, None)
        return None

    def _cleanup_drafts_for_thread(self, thread_id: str) -> None:
        for key in list(self._drafts):
            if key[0] == thread_id:
                self._drafts.pop(key, None)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {
            "id": chat_id,
            "name": f"Forge thread {chat_id}",
            "type": "forge_thread",
        }

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
    """Register the Forge platform through the standard plugin capability."""
    ctx.register_platform(
        name="forge",
        label="Forge",
        adapter_factory=lambda cfg: ForgeAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        env_enablement_fn=_env_enablement,
        required_env=["FORGE_API_KEY"],
        install_hint=(
            "Set FORGE_API_KEY and optional FORGE_BASE_URL in the Hermes profile .env"
        ),
        emoji="🛠️",
        pii_safe=False,
        allow_update_command=False,
    )
