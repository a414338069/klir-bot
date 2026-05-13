"""Rich tool activity descriptor for Telegram display."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klir.cli.stream_events import ToolUseEvent

_MAX_DETAIL_LEN = 100
_TRUNCATION_SUFFIX = "\u2026"
_FILE_PATH_KEYS = ("file_path", "path", "glob", "pattern")
_COMMAND_KEYS = ("command", "cmd")


@dataclass(frozen=True, slots=True)
class ToolActivity:
    """Enriched tool call info passed through streaming callbacks."""

    name: str
    file_path: str | None = None
    command: str | None = None

    def display_label(self) -> str:
        """Format a human-readable label for Telegram display."""
        detail = self.file_path or self.command
        if detail is None:
            return self.name
        if len(detail) > _MAX_DETAIL_LEN:
            detail = detail[: _MAX_DETAIL_LEN - 1] + _TRUNCATION_SUFFIX
        return f"{self.name}: {detail}"

    @classmethod
    def from_event(cls, event: ToolUseEvent) -> ToolActivity:
        """Extract activity from a parsed ToolUseEvent."""
        params = event.parameters or {}

        file_path: str | None = None
        for key in _FILE_PATH_KEYS:
            val = params.get(key)
            if isinstance(val, str) and val:
                file_path = val
                break

        command: str | None = None
        for key in _COMMAND_KEYS:
            val = params.get(key)
            if isinstance(val, str) and val:
                command = val
                break

        return cls(name=event.tool_name, file_path=file_path, command=command)
