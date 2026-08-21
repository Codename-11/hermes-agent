"""xAI OAuth upstream adapter for Hermes proxy.

Uses Hermes' credential pool/auth-store machinery so the proxy shares the same
refreshed xai-oauth state as normal Hermes runs.

This is a local stub until upstream adds a proper xai-oauth adapter.
"""

from __future__ import annotations

import logging
from typing import FrozenSet

from hermes_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential

logger = logging.getLogger(__name__)

_XAI_BASE_URL = "https://api.x.ai/v1"
_ALLOWED_PATHS: FrozenSet[str] = frozenset(
    {
        "/chat/completions",
        "/completions",
        "/embeddings",
        "/models",
        "/responses",
    }
)


def _load_pool(provider: str):
    # Import lazily so tests can monkeypatch HERMES_HOME before first use and
    # proxy import stays lightweight.
    from agent.credential_pool import load_pool

    return load_pool(provider)


class XaiOAuthAdapter(UpstreamAdapter):
    """Proxy upstream for xAI (Grok) via Hermes OAuth."""

    @property
    def name(self) -> str:
        return "xai-oauth"

    @property
    def display_name(self) -> str:
        return "xAI / Grok (OAuth)"

    @property
    def allowed_paths(self) -> FrozenSet[str]:
        return _ALLOWED_PATHS

    def is_authenticated(self) -> bool:
        try:
            pool = _load_pool(self.name)
            return pool.has_credentials() and pool.has_available()
        except Exception as exc:
            logger.debug("xAI OAuth pool auth check failed: %s", exc)
            return False

    def get_credential(self) -> UpstreamCredential:
        pool = _load_pool(self.name)
        entry = pool.select()
        if entry is None:
            raise RuntimeError(
                "Not logged into xAI via Hermes or all xAI credentials are exhausted. "
                "Run the normal Hermes xAI OAuth login flow or `hermes auth reset xai-oauth`."
            )

        bearer = entry.runtime_api_key or entry.access_token
        if not bearer:
            raise RuntimeError("xai-oauth access token missing or expired.")

        return UpstreamCredential(
            bearer=bearer,
            base_url=(entry.runtime_base_url or _XAI_BASE_URL).rstrip("/"),
            token_type="Bearer",
            expires_at=entry.expires_at,
        )
