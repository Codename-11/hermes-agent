"""OpenAI Codex upstream adapter for Hermes proxy.

Uses Hermes' shared credential pool/auth-store machinery, not a separate API
key. The selected pool entry usually points at the ChatGPT Codex backend
(`https://chatgpt.com/backend-api/codex`) and carries a refreshed OAuth bearer.

Chat-completions clients are translated onto the Codex Responses transport.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, FrozenSet

from hermes_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential

logger = logging.getLogger(__name__)

_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_ALLOWED_PATHS: FrozenSet[str] = frozenset({
    "/chat/completions",
    "/completions",
    "/embeddings",
    "/models",
    "/responses",
})


def _load_pool(provider: str):
    # Kept for cheap auth checks; runtime credential resolution below owns
    # refresh and singleton/pool reconciliation.
    from agent.credential_pool import load_pool

    return load_pool(provider)


def _resolve_codex_credential(*, force_refresh: bool = False) -> UpstreamCredential:
    from hermes_cli.auth import resolve_codex_runtime_credentials

    resolved = resolve_codex_runtime_credentials(force_refresh=force_refresh)
    bearer = str(resolved.get("api_key") or "").strip()
    if not bearer:
        raise RuntimeError("Codex access token missing.")
    return UpstreamCredential(
        bearer=bearer,
        base_url=str(resolved.get("base_url") or _CODEX_BASE_URL).rstrip("/"),
        token_type="Bearer",
    )


class OpenAICodexAdapter(UpstreamAdapter):
    """Proxy upstream for OpenAI Codex / ChatGPT Pro via Hermes OAuth."""

    @property
    def name(self) -> str:
        return "openai-codex"

    @property
    def display_name(self) -> str:
        return "OpenAI Codex / ChatGPT Pro (OAuth)"

    @property
    def allowed_paths(self) -> FrozenSet[str]:
        return _ALLOWED_PATHS

    def is_authenticated(self) -> bool:
        try:
            from hermes_cli.auth import get_provider_auth_state

            state = get_provider_auth_state(self.name) or {}
            if isinstance(state, dict):
                tokens = state.get("tokens") or {}
                if state.get("access_token") or (
                    isinstance(tokens, dict) and tokens.get("access_token")
                ):
                    return True
            pool = _load_pool(self.name)
            return pool.has_credentials() and pool.has_available()
        except Exception as exc:
            logger.debug("Codex pool auth check failed: %s", exc)
            return False

    def get_credential(self) -> UpstreamCredential:
        try:
            return _resolve_codex_credential()
        except Exception as exc:
            raise RuntimeError(
                "OpenAI Codex credentials are unavailable. Run "
                "`hermes auth add openai-codex --type oauth` or "
                "`hermes auth reset openai-codex`."
            ) from exc

    def get_retry_credential(
        self,
        *,
        failed_credential: UpstreamCredential,
        status_code: int,
    ) -> UpstreamCredential | None:
        if status_code != 401:
            return None
        refreshed = _resolve_codex_credential(force_refresh=True)
        if refreshed.bearer == failed_credential.bearer:
            return None
        return refreshed

    def prepare_proxy_request(
        self,
        rel_path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[str, bytes, dict[str, str], dict[str, Any]]:
        """Translate OpenAI chat-completions requests to Codex Responses.

        The ChatGPT Codex backend accepts the Responses route, not a normal
        OpenAI `/chat/completions` request. Keep this adapter-specific so the
        main proxy remains a boring credential-attaching forwarder for sane
        providers. Rare creatures, those.
        """
        if rel_path != "/chat/completions":
            return rel_path, body, headers, {}

        try:
            payload = json.loads(body.decode("utf-8") if body else "{}")
        except Exception:
            return rel_path, body, headers, {}
        if not isinstance(payload, dict):
            return rel_path, body, headers, {}

        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            messages = []

        instructions: list[str] = []
        input_items: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip().lower()
            text = _message_content_to_text(message.get("content"))
            if role in {"system", "developer"}:
                if text:
                    instructions.append(text)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            input_items.append({
                "role": role,
                "content": [{"type": "input_text", "text": text}],
            })

        translated: dict[str, Any] = {
            "model": payload.get("model") or "gpt-5.5",
            "instructions": "\n\n".join(instructions) or "You are a helpful assistant.",
            "input": input_items,
            "store": False,
            # Codex backend requires streaming upstream; non-streaming OpenAI
            # clients are reconstructed after consuming the Codex SSE stream.
            "stream": True,
        }
        for key in (
            "temperature",
            "top_p",
            "reasoning",
            "text",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
        ):
            if key in payload:
                translated[key] = payload[key]

        out_headers = {"Content-Type": "application/json"}
        context = {
            "codex_chat_completion": True,
            "client_stream": bool(payload.get("stream")),
            "model": str(translated["model"]),
            "created": int(time.time()),
            "id": f"chatcmpl-{uuid.uuid4().hex}",
        }
        return (
            "/responses",
            json.dumps(translated).encode("utf-8"),
            out_headers,
            context,
        )

    async def finalize_proxy_response(
        self, request, upstream_resp, session, context: dict[str, Any]
    ):
        """Convert Codex Responses SSE back to OpenAI chat-completions."""
        if not context.get("codex_chat_completion"):
            return None
        # If Codex returns an error, preserve its response body/status rather
        # than hiding useful diagnostics behind a translation failure.
        if upstream_resp.status != 200:
            return None
        try:
            if context.get("client_stream"):
                return await _stream_chat_completion_chunks(
                    request, upstream_resp, context
                )
            return await _collect_chat_completion(upstream_resp, context)
        finally:
            upstream_resp.release()
            await session.close()


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(part for part in parts if part)
    return str(content)


async def _iter_codex_sse_objects(upstream_resp) -> AsyncIterator[dict[str, Any]]:
    data_lines: list[str] = []
    async for raw in upstream_resp.content:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                data_lines = []
                if data and data != "[DONE]":
                    try:
                        parsed = json.loads(data)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        yield parsed
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        data = "\n".join(data_lines)
        if data and data != "[DONE]":
            try:
                parsed = json.loads(data)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                yield parsed


def _extract_delta(event: dict[str, Any]) -> str:
    if event.get("type") == "response.output_text.delta":
        return str(event.get("delta") or "")
    return ""


def _chat_chunk(
    context: dict[str, Any], delta: dict[str, Any], *, finish_reason: str | None = None
) -> dict[str, Any]:
    return {
        "id": context["id"],
        "object": "chat.completion.chunk",
        "created": context["created"],
        "model": context["model"],
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


async def _stream_chat_completion_chunks(
    request, upstream_resp, context: dict[str, Any]
):
    from aiohttp import web

    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
    )
    await response.prepare(request)
    await response.write(
        f"data: {json.dumps(_chat_chunk(context, {'role': 'assistant'}))}\n\n".encode(
            "utf-8"
        )
    )
    async for event in _iter_codex_sse_objects(upstream_resp):
        delta = _extract_delta(event)
        if not delta:
            continue
        chunk = _chat_chunk(context, {"content": delta})
        await response.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
    await response.write(
        f"data: {json.dumps(_chat_chunk(context, {}, finish_reason='stop'))}\n\n".encode(
            "utf-8"
        )
    )
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


async def _collect_chat_completion(upstream_resp, context: dict[str, Any]):
    from aiohttp import web

    text_parts: list[str] = []
    async for event in _iter_codex_sse_objects(upstream_resp):
        delta = _extract_delta(event)
        if delta:
            text_parts.append(delta)
    content = "".join(text_parts)
    body = {
        "id": context["id"],
        "object": "chat.completion",
        "created": context["created"],
        "model": context["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    return web.json_response(body)
