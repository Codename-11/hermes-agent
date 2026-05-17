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

from hermes_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential
from hermes_cli.proxy.adapters.nous_portal import NousPortalAdapter
from hermes_cli.proxy.adapters.openai_codex import OpenAICodexAdapter
from hermes_cli.proxy.adapters.xai_oauth import XaiOAuthAdapter

logger = logging.getLogger(__name__)

_DEFAULT_MODELS = [
    {"id": "grok-4.3", "object": "model", "owned_by": "xai-oauth"},
    {"id": "grok-4.20-reasoning", "object": "model", "owned_by": "xai-oauth"},
    {"id": "gpt-5.4", "object": "model", "owned_by": "openai-codex"},
    {"id": "gpt-5.4-mini", "object": "model", "owned_by": "openai-codex"},
    {"id": "gpt-5.3-codex", "object": "model", "owned_by": "openai-codex"},
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
        return _DEFAULT_MODELS

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

        if model.startswith("grok") or model.startswith("xai"):
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
