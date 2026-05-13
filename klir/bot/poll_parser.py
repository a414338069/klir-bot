"""Parse poll directives from provider CLI output text."""

from __future__ import annotations

import re
from dataclasses import dataclass

_POLL_RE = re.compile(r"\[poll:([^\]]+)\]")
_MAX_OPTIONS = 10  # Telegram limit


@dataclass(frozen=True, slots=True)
class PollDirective:
    """A parsed poll directive."""

    question: str
    options: list[str]
    allows_multiple: bool = False


def parse_polls(text: str) -> list[PollDirective]:
    """Extract poll directives from text. Returns list of PollDirective."""
    results = []
    for match in _POLL_RE.finditer(text):
        raw = match.group(1)
        allows_multiple = False

        # Check for flags prefix (e.g., "multi:")
        if raw.startswith("multi:"):
            allows_multiple = True
            raw = raw[6:]

        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:  # question + at least 2 options
            continue

        question = parts[0]
        options = parts[1 : _MAX_OPTIONS + 1]

        results.append(
            PollDirective(
                question=question,
                options=options,
                allows_multiple=allows_multiple,
            )
        )

    return results


def strip_polls(text: str) -> str:
    """Remove poll directives from text, leaving surrounding content."""
    return _POLL_RE.sub("", text)
