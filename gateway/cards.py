"""Structured info cards for gateway commands.

A lightweight, platform-agnostic schema that plugin hook handlers and built-in
command handlers can return instead of a plain markdown string. Adapters that
support rich rendering (Discord embeds, Telegram formatted messages with
buttons, etc.) render the card natively; adapters without rich support fall
back to a plain-text rendering via :func:`render_card_as_text`.

Schema
------

An InfoCard is a ``dict`` with the following keys (all optional except
``title`` or ``description`` — at least one must be present)::

    {
        "title": "🔀 Model Routing Status",          # str, bold header
        "description": "Router is enabled.\n...",    # str, markdown body
        "color": "info",                             # str: info|success|warning|error|accent
                                                     #  or hex "#3bb8f0"
        "fields": [                                  # list of name/value pairs
            {"name": "Router", "value": "Enabled ✓", "inline": True},
            {"name": "Mode",   "value": "Auto",      "inline": True},
        ],
        "footer": "/route auto|opus|sonnet|...",     # str, small text at bottom
        "url": "https://example.com",                # optional title link
    }

Plugin handlers may return either a plain string (legacy behavior) or a dict
matching this schema. The gateway dispatcher detects the dict form and routes
it to ``adapter.send_info_card``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Color palette — semantic names mapped to hex. Platforms that use RGB ints
# (Discord) convert via ``parse_color``.
# ---------------------------------------------------------------------------

_SEMANTIC_COLORS: Dict[str, str] = {
    "info":    "#3bb8f0",   # Axiom-Labs accent cyan
    "success": "#2ecc71",
    "warning": "#f1c40f",
    "error":   "#e74c3c",
    "accent":  "#3bb8f0",
    "neutral": "#95a5a6",
}

DEFAULT_COLOR = "info"


def parse_color(color: Optional[str]) -> int:
    """Parse a color string (semantic name or ``#rrggbb``) into a 24-bit int.

    Falls back to the default accent color if unrecognized. Returns an int
    suitable for ``discord.Color`` or any RGB-int API.
    """
    if not color:
        color = DEFAULT_COLOR
    color = color.strip().lower()
    hex_ = _SEMANTIC_COLORS.get(color, color)
    if hex_.startswith("#"):
        hex_ = hex_[1:]
    try:
        return int(hex_, 16) & 0xFFFFFF
    except (TypeError, ValueError):
        return int(_SEMANTIC_COLORS[DEFAULT_COLOR][1:], 16)


# ---------------------------------------------------------------------------
# Validation / normalization
# ---------------------------------------------------------------------------

def is_card(value: Any) -> bool:
    """Return True if *value* looks like an InfoCard dict.

    A card is a dict that has ``title`` or ``description``. ``fields``,
    ``color``, ``footer``, ``url`` are all optional.
    """
    if not isinstance(value, dict):
        return False
    return bool(value.get("title") or value.get("description"))


def normalize_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *card* with missing optional keys filled in.

    Strips invalid fields; truncates title/description to safe lengths.
    Safe to call on user-supplied dicts — never raises.
    """
    out: Dict[str, Any] = {}
    title = card.get("title")
    if title:
        out["title"] = str(title)[:256]
    desc = card.get("description")
    if desc:
        out["description"] = str(desc)[:4000]
    color = card.get("color") or DEFAULT_COLOR
    out["color"] = color
    footer = card.get("footer")
    if footer:
        out["footer"] = str(footer)[:2048]
    url = card.get("url")
    if url and isinstance(url, str):
        out["url"] = url

    fields_raw = card.get("fields") or []
    fields: List[Dict[str, Any]] = []
    if isinstance(fields_raw, list):
        for f in fields_raw[:25]:  # Discord embed field cap
            if not isinstance(f, dict):
                continue
            name = f.get("name")
            value = f.get("value")
            if not name or value is None:
                continue
            fields.append({
                "name": str(name)[:256],
                "value": str(value)[:1024],
                "inline": bool(f.get("inline", False)),
            })
    out["fields"] = fields
    return out


# ---------------------------------------------------------------------------
# Fallback text renderer
# ---------------------------------------------------------------------------

def render_card_as_text(card: Dict[str, Any]) -> str:
    """Render an InfoCard as plain markdown for adapters without embed support.

    Output mirrors the card's visual structure so it remains readable on any
    platform. Used as a fallback by :meth:`BasePlatformAdapter.send_info_card`.
    """
    card = normalize_card(card)
    lines: List[str] = []
    if card.get("title"):
        lines.append(f"**{card['title']}**")
    if card.get("description"):
        if lines:
            lines.append("")
        lines.append(card["description"])
    if card.get("fields"):
        if lines:
            lines.append("")
        for f in card["fields"]:
            value = f["value"]
            # keep field values on one line when short, multi-line when long
            if "\n" in value or len(value) > 60:
                lines.append(f"**{f['name']}**")
                lines.append(value)
            else:
                lines.append(f"**{f['name']}:** {value}")
    if card.get("footer"):
        if lines:
            lines.append("")
        lines.append(f"_{card['footer']}_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience constructors — plugins can use these instead of hand-building
# dicts.
# ---------------------------------------------------------------------------

def build_card(
    title: Optional[str] = None,
    description: Optional[str] = None,
    fields: Optional[List[Dict[str, Any]]] = None,
    color: str = DEFAULT_COLOR,
    footer: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an InfoCard dict. Convenience wrapper around the raw schema."""
    card: Dict[str, Any] = {"color": color}
    if title:
        card["title"] = title
    if description:
        card["description"] = description
    if fields:
        card["fields"] = fields
    if footer:
        card["footer"] = footer
    if url:
        card["url"] = url
    return card


__all__ = [
    "DEFAULT_COLOR",
    "build_card",
    "is_card",
    "normalize_card",
    "parse_color",
    "render_card_as_text",
]
