"""Technical footer rendered as Telegram HTML."""

from __future__ import annotations

import html as _html
from dataclasses import dataclass

# Approximate pricing per 1M tokens (input + output blended).
# Keyed by model ID substring — first match wins.
_PRICE_PER_MILLION: dict[str, float] = {
    "claude-opus-4": 30.00,
    "claude-sonnet-4": 6.00,
    "claude-haiku-4": 1.25,
    "claude-opus-3": 30.00,
    "claude-sonnet-3-5": 6.00,
    "claude-sonnet-3": 6.00,
    "claude-haiku-3": 1.25,
    "gemini-2.5-pro": 7.00,
    "gemini-2.0-flash": 0.40,
    "gemini-1.5-pro": 7.00,
    "gemini-1.5-flash": 0.40,
}

_FALLBACK_PRICE = 5.00


def _price_per_million(model_id: str) -> float:
    lower = model_id.lower()
    for key, price in _PRICE_PER_MILLION.items():
        if key in lower:
            return price
    return _FALLBACK_PRICE


@dataclass(slots=True)
class FooterData:
    """Raw metadata captured from a completed CLI turn."""

    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    duration_ms: float | None


def _build_parts(data: FooterData) -> list[str]:
    """Build the display parts list shared by HTML and Markdown renderers."""
    total_tokens = data.input_tokens + data.output_tokens

    if data.cost_usd is not None:
        cost = data.cost_usd
    elif total_tokens > 0:
        cost = total_tokens * _price_per_million(data.model_id) / 1_000_000
    else:
        cost = None

    parts: list[str] = [data.model_id] if data.model_id else []

    if total_tokens > 0:
        parts.append(f"{total_tokens:,} tok")

    if cost is not None:
        parts.append(f"${cost:.4f}")

    if data.duration_ms is not None:
        secs = data.duration_ms / 1000
        parts.append(f"{secs:.1f}s")

    return parts


def build_footer_html(data: FooterData) -> str:
    """Render the footer as a single Telegram HTML line.

    Returns an empty string when there is no meaningful data to show.
    """
    parts = _build_parts(data)
    if not parts:
        return ""
    body = _html.escape(" · ".join(parts))
    return f"\n<i>─\n{body}</i>"


def build_footer_markdown(data: FooterData) -> str:
    """Render the footer as Markdown for non-streaming send_rich compatibility."""
    parts = _build_parts(data)
    if not parts:
        return ""
    body = " · ".join(parts)
    return f"\n\n─\n_{body}_"
