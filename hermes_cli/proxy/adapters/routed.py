"""Model-routed Hermes proxy adapter.

Routes OpenAI-compatible requests to a sub-adapter based on the requested
model ID so clients can use a single local endpoint:

  - grok* / xai* -> xai-oauth
  - gpt* / o* / codex* / chatgpt* -> openai-codex
  - hermes* / nous* -> nous

This is a local Axiom patch until upstream implements multi-provider routing.
"""

from __future__ import annotations

import json
import logging
from typing import FrozenSet, Iterable, Optional

from hermes_cli.codex_models import DEFAULT_CODEX_MODELS, _add_forward_compat_models
from hermes_cli.models import provider_model_ids
from hermes_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential
from hermes_cli.proxy.adapters.nous_portal import NousPortalAdapter
from hermes_cli.proxy.adapters.openai_codex import OpenAICodexAdapter
from hermes_cli.proxy.adapters.xai_oauth import XaiOAuthAdapter

logger = logging.getLogger(__name__)

def _model_entries(model_ids: Iterable[str], owned_by: str) -> list[dict]:
    return [
        {"id": model_id, "object": "model", "owned_by": owned_by}
        for model_id in model_ids
        if isinstance(model_id, str) and model_id.strip()
    ]


def _text_model_ids(model_ids: Iterable[str]) -> list[str]:
    """Keep the routed proxy catalog focused on text/chat-capable models."""
    blocked_fragments = ("imagine", "image", "video", "vision")
    out: list[str] = []
    seen: set[str] = set()
    for model_id in model_ids:
        if not isinstance(model_id, str):
            continue
        clean = model_id.strip()
        if not clean:
            continue
        lowered = clean.lower()
        if any(fragment in lowered for fragment in blocked_fragments):
            continue
        if clean not in seen:
            out.append(clean)
            seen.add(clean)
    return out


def _provider_models(provider: str, fallback: Iterable[str]) -> list[str]:
    try:
        ids = provider_model_ids(provider)
    except Exception:
        ids = []
    return _text_model_ids(ids or fallback)


_XAI_FALLBACK_MODELS = [
    "grok-4.3",
    "grok-4.20-reasoning",
]
_CODEX_FALLBACK_MODELS = _add_forward_compat_models(list(DEFAULT_CODEX_MODELS))


_DEFAULT_MODELS = [
    *_model_entries(_XAI_FALLBACK_MODELS, "xai-oauth"),
    *_model_entries(_CODEX_FALLBACK_MODELS, "openai-codex"),
]


def _union_allowed_paths(adapters: Iterable[UpstreamAdapter]) -> FrozenSet[str]:
    paths: set[str] = set()
    for adapter in adapters:
        paths.update(adapter.allowed_paths)
    return frozenset(paths)


class RoutedOAuthAdapter(UpstreamAdapter):
    """Single-instance adapter that routes by requested model."""

    def __init__(self) -> None:
        self.xai = XaiOAuthAdapter()
        self.codex = OpenAICodexAdapter()
        self.nous = NousPortalAdapter()
        self.adapters = [self.xai, self.codex, self.nous]

    @property
    def name(self) -> str:
        return "auto"

    @property
    def display_name(self) -> str:
        return "Hermes OAuth Router"

    @property
    def allowed_paths(self) -> FrozenSet[str]:
        return _union_allowed_paths(self.adapters)

    @property
    def available_models(self) -> list[dict]:
        models: list[dict] = []
        if self.xai.is_authenticated():
            models.extend(_model_entries(_provider_models("xai-oauth", _XAI_FALLBACK_MODELS), "xai-oauth"))
        if self.codex.is_authenticated():
            models.extend(_model_entries(_provider_models("openai-codex", _CODEX_FALLBACK_MODELS), "openai-codex"))
        if self.nous.is_authenticated():
            models.extend(_model_entries(_provider_models("nous", []), "nous"))

        # Startup already requires at least one authenticated adapter. Keep a
        # static fallback for tests and for status probes during auth churn.
        return models or _DEFAULT_MODELS

    def is_authenticated(self) -> bool:
        return any(adapter.is_authenticated() for adapter in self.adapters)

    def _model_from_body(self, body: bytes) -> str:
        if not body:
            return ""
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return ""
        model = payload.get("model", "")
        return str(model or "").strip().lower()

    def _select_adapter(self, rel_path: str, body: bytes) -> UpstreamAdapter:
        model = self._model_from_body(body)

        if model.startswith("grok") or model.startswith("xai") or model.startswith("x-ai/"):
            return self.xai

        if (
            model.startswith("gpt")
            or model.startswith("o1")
            or model.startswith("o3")
            or model.startswith("o4")
            or model.startswith("o5")
            or "codex" in model
            or model.startswith("chatgpt")
        ):
            return self.codex

        if model.startswith("hermes") or model.startswith("nous"):
            return self.nous

        # Nous Portal exposes OpenRouter-style provider/model IDs. Preserve
        # slash-prefixed IDs for Nous rather than accidentally sending them to
        # a bare-model OAuth backend.
        if "/" in model:
            return self.nous

        # If no model is present (for /models or basic probes), prefer xAI if ready.
        for adapter in (self.xai, self.codex, self.nous):
            if adapter.is_authenticated():
                return adapter

        raise RuntimeError(
            "No authenticated Hermes OAuth upstreams are available. "
            "Run `hermes login --provider xai-oauth`, `hermes login --provider openai-codex`, "
            "or `hermes login --provider nous`."
        )

    def get_credential(self) -> UpstreamCredential:
        # Fallback for server versions that do not pass request bodies.
        return self._select_adapter("", b"").get_credential()

    def get_credential_for_request(self, rel_path: str, body: bytes) -> UpstreamCredential:
        adapter = self._select_adapter(rel_path, body)
        logger.debug("proxy router: %s -> %s", rel_path, adapter.display_name)
        return adapter.get_credential()
