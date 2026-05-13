"""Parse forward/copy directives from provider CLI output text."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FORWARD_RE = re.compile(r"\[(forward|copy):(-?\d+):(\d+)\]")


@dataclass(frozen=True, slots=True)
class ForwardDirective:
    """A parsed forward or copy directive."""

    mode: str  # "forward" or "copy"
    chat_id: int
    message_id: int


def parse_forwards(text: str) -> list[ForwardDirective]:
    """Extract forward/copy directives from text."""
    results = []
    for match in _FORWARD_RE.finditer(text):
        mode = match.group(1)
        try:
            chat_id = int(match.group(2))
            message_id = int(match.group(3))
        except ValueError:
            continue
        results.append(ForwardDirective(mode=mode, chat_id=chat_id, message_id=message_id))
    return results


def strip_forwards(text: str) -> str:
    """Remove forward/copy directives from text, leaving surrounding content."""
    return _FORWARD_RE.sub("", text)
