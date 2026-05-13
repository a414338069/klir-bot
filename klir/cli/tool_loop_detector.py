from __future__ import annotations


class ToolLoopDetector:
    """Detects consecutive identical tool calls that indicate a runaway loop."""

    __slots__ = ("_count", "_current_tool", "_threshold")

    def __init__(self, threshold: int = 10) -> None:
        self._current_tool: str | None = None
        self._count: int = 0
        self._threshold: int = threshold

    def record(self, tool_name: str) -> None:
        """Record a tool call. Increments counter if same tool, resets if different."""
        if tool_name == self._current_tool:
            self._count += 1
        else:
            self._current_tool = tool_name
            self._count = 1

    @property
    def is_looping(self) -> bool:
        """True when consecutive identical calls reach the threshold."""
        return self._count >= self._threshold

    @property
    def consecutive_count(self) -> int:
        return self._count

    @property
    def current_tool(self) -> str | None:
        return self._current_tool

    def reset(self) -> None:
        self._current_tool = None
        self._count = 0
