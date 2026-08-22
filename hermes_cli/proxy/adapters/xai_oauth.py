"""Compatibility-named xAI OAuth proxy adapter.

The current upstream :class:`XAIGrokAdapter` owns refresh, cooldown, and pool
rotation behavior.  Keep the routed adapter name used by existing proxy
clients without duplicating or bypassing that credential lifecycle.
"""

from __future__ import annotations

from hermes_cli.proxy.adapters.xai import XAIGrokAdapter


class XaiOAuthAdapter(XAIGrokAdapter):
    """xAI proxy adapter exposed under the credential provider's canonical name."""

    @property
    def name(self) -> str:
        return "xai-oauth"

    @property
    def display_name(self) -> str:
        return "xAI / Grok (OAuth)"


__all__ = ["XaiOAuthAdapter"]
