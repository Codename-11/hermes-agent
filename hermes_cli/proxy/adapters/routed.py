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
import os
import time
from pathlib import Path
from typing import Any, FrozenSet, Iterable

from hermes_cli.codex_models import DEFAULT_CODEX_MODELS, _add_forward_compat_models
from hermes_cli.models import provider_model_ids
from hermes_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential
from hermes_cli.proxy.adapters.nous_portal import NousPortalAdapter
from hermes_cli.proxy.adapters.openai_codex import OpenAICodexAdapter
from hermes_cli.proxy.adapters.xai_oauth import XaiOAuthAdapter

logger = logging.getLogger(__name__)

_ADVERTISE_MODE_ENV = "HERMES_PROXY_MODEL_ADVERTISE_MODE"
_HEALTH_CACHE_ENV = "HERMES_PROXY_MODEL_HEALTH_CACHE"
_HEALTH_TTL_ENV = "HERMES_PROXY_MODEL_HEALTH_TTL_SECONDS"


def _model_entries(model_ids: Iterable[str], owned_by: str, *, health: str = "unknown") -> list[dict]:
    return [
        {
            "id": model_id,
            "object": "model",
            "owned_by": owned_by,
            # OpenAI clients ignore unknown fields; ModelFoundry and other
            # routers can use them to separate discovery inventory from live
            # routing health without scraping error text.
            "hermes_provider": owned_by,
            "hermes_capabilities": ["chat"],
            "hermes_health": health,
        }
        for model_id in model_ids
        if isinstance(model_id, str) and model_id.strip()
    ]


_XAI_UNROUTABLE_MODEL_IDS = {
    # xAI retired these legacy public IDs before this local proxy patch, but
    # stale models.dev caches can keep surfacing them. If chat/completions
    # returns "Model not found", advertising the ID is worse than omitting it.
    "grok-2",
    "grok-2-1212",
    "grok-2-latest",
    "grok-beta",
}


def _chat_model_ids(model_ids: Iterable[str], *, provider: str | None = None) -> list[str]:
    """Keep the routed proxy catalog focused on chat-completions models."""
    blocked_fragments = (
        "audio",
        "embedding",
        "imagine",
        "image",
        "moderation",
        "multi-agent",
        "rerank",
        "speech",
        "tts",
        "video",
        "vision",
    )
    out: list[str] = []
    seen: set[str] = set()
    normalized_provider = (provider or "").strip().lower()
    for model_id in model_ids:
        if not isinstance(model_id, str):
            continue
        clean = model_id.strip()
        if not clean:
            continue
        lowered = clean.lower()
        if normalized_provider in {"xai", "xai-oauth"} and lowered in _XAI_UNROUTABLE_MODEL_IDS:
            continue
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
    return _chat_model_ids(ids or fallback, provider=provider)


_XAI_FALLBACK_MODELS = [
    "grok-4.3",
    "grok-4.20-0309-non-reasoning",
    "grok-4.20-0309-reasoning",
]
_CODEX_FALLBACK_MODELS = _add_forward_compat_models(list(DEFAULT_CODEX_MODELS))


_DEFAULT_MODELS = [
    *_model_entries(_XAI_FALLBACK_MODELS, "xai-oauth"),
    *_model_entries(_CODEX_FALLBACK_MODELS, "openai-codex"),
]


def _advertise_mode() -> str:
    """Return model-advertising policy for the routed proxy.

    - auth: advertise models for currently authenticated adapters only.
    - routable: advertise authenticated models only if the health cache says
      the model recently passed a chat-completions probe.
    - all: legacy optimistic catalog fallback for compatibility/debugging.
    """
    raw = os.getenv(_ADVERTISE_MODE_ENV, "auth").strip().lower()
    aliases = {
        "authenticated": "auth",
        "available": "auth",
        "healthy": "routable",
        "health": "routable",
        "strict": "routable",
        "legacy": "all",
    }
    mode = aliases.get(raw, raw)
    if mode not in {"auth", "routable", "all"}:
        logger.warning("Invalid %s=%r; using auth", _ADVERTISE_MODE_ENV, raw)
        return "auth"
    return mode


def _health_cache_path() -> Path:
    raw = os.getenv(_HEALTH_CACHE_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "proxy_model_health.json"


def _health_ttl_seconds() -> float:
    raw = os.getenv(_HEALTH_TTL_ENV, "86400").strip()
    try:
        return float(raw)
    except ValueError:
        return 86400.0


def _entry_status(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip().lower()
    if isinstance(entry, dict):
        return str(entry.get("status") or entry.get("health") or "").strip().lower()
    if entry is True:
        return "up"
    return ""


def _entry_checked_at(entry: Any) -> float | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("checked_at") or entry.get("ts") or entry.get("timestamp")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _load_routable_model_ids() -> set[str]:
    """Load cached chat-probe winners for strict/routable advertising.

    Expected shape:
      {"models": {"grok-4.3": {"status": "up", "checked_at": 1770000000}}}

    Also accepts compact shapes for hand-authored/operator scripts:
      {"up": ["grok-4.3"]}
      {"grok-4.3": "up"}
    """
    path = _health_cache_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return set()
    except Exception as exc:
        logger.warning("Could not read Hermes proxy model health cache %s: %s", path, exc)
        return set()

    now = time.time()
    ttl = _health_ttl_seconds()
    good_statuses = {"ok", "pass", "passed", "ready", "routable", "healthy", "up"}
    out: set[str] = set()

    if isinstance(data, dict) and isinstance(data.get("up"), list):
        out.update(str(item).strip() for item in data["up"] if str(item).strip())
        return out

    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict) and isinstance(data, dict):
        models = data
    if not isinstance(models, dict):
        return set()

    for model_id, entry in models.items():
        clean = str(model_id or "").strip()
        if not clean or _entry_status(entry) not in good_statuses:
            continue
        checked_at = _entry_checked_at(entry)
        if ttl > 0 and checked_at is not None and now - checked_at > ttl:
            continue
        out.add(clean)
    return out


def _filter_routable(entries: list[dict]) -> list[dict]:
    allowed = _load_routable_model_ids()
    if not allowed:
        return []
    return [
        {**entry, "hermes_health": "up"}
        for entry in entries
        if entry.get("id") in allowed
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

        mode = _advertise_mode()
        if mode == "all":
            # Legacy compatibility/debug mode: keep the old optimistic catalog
            # fallback when no authenticated adapter-specific list is present.
            return models or _DEFAULT_MODELS
        if mode == "routable":
            # Strict mode: only advertise models that have recently passed a
            # chat-completions probe recorded by an external/ops health task.
            # Do not probe synchronously from /v1/models.
            return _filter_routable(models)

        # Default: authenticated adapter inventory only. No no-auth static
        # fallback — advertising ghosts is worse than returning an empty list.
        return models

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
