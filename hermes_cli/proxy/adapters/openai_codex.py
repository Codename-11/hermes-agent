"""OpenAI Codex upstream adapter for Hermes proxy.

Uses Hermes' shared credential pool/auth-store machinery, not a separate API
key. The selected pool entry usually points at the ChatGPT Codex backend
(`https://chatgpt.com/backend-api/codex`) and carries a refreshed OAuth bearer.

This is a local stub until upstream adds a proper adapter.
"""

from __future__ import annotations

import logging
from typing import FrozenSet

from hermes_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential

logger = logging.getLogger(__name__)

_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
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
            pool = _load_pool(self.name)
            return pool.has_credentials() and pool.has_available()
        except Exception as exc:
            logger.debug("Codex pool auth check failed: %s", exc)
            return False

    def get_credential(self) -> UpstreamCredential:
        pool = _load_pool(self.name)
        entry = pool.select()
        if entry is None:
            raise RuntimeError(
                "Not logged into OpenAI Codex via Hermes or all Codex credentials are exhausted. "
                "Run `hermes login --provider openai-codex` or `hermes auth reset openai-codex`."
            )

        bearer = entry.runtime_api_key or entry.access_token
        if not bearer:
            raise RuntimeError("Codex access token missing.")

        return UpstreamCredential(
            bearer=bearer,
            base_url=(entry.runtime_base_url or _CODEX_BASE_URL).rstrip("/"),
            token_type="Bearer",
            expires_at=entry.expires_at,
        )
