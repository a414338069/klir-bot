"""JSON stream event parser for the OpenCode CLI."""

from __future__ import annotations

import json
import logging
from typing import Any

from klir.cli.stream_events import (
    AssistantTextDelta,
    ResultEvent,
    StreamEvent,
    SystemInitEvent,
    ThinkingEvent,
    ToolUseEvent,
)

logger = logging.getLogger(__name__)


def parse_opencode_json(raw: str) -> tuple[str, str | None, dict[str, Any] | None]:
    """Parse OpenCode JSON output into (result_text, session_id, usage)."""
    lines = raw.strip().splitlines()
    result_parts: list[str] = []
    session_id: str | None = None
    usage: dict[str, Any] | None = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        data = _try_parse_json(stripped)
        if data is None:
            # Plain text fallback: treat as result text.
            result_parts.append(stripped)
            continue

        session_id = _extract_session_id(data, session_id)
        usage = _extract_usage(data, usage)
        _extract_text(data, result_parts)

    return "\n".join(result_parts).strip(), session_id, usage


def parse_opencode_stream_event(line: str) -> list[StreamEvent]:
    """Parse a single OpenCode JSON line into normalised stream events."""
    stripped = line.strip()
    if not stripped:
        return []

    data = _try_parse_json(stripped)
    if data is None:
        # Plain text output: treat as assistant text delta.
        return [AssistantTextDelta(type="assistant", text=stripped)]

    return _dispatch_event(data)


def _try_parse_json(line: str) -> dict[str, Any] | None:
    """Try to parse a line as JSON dict, return None on failure."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("OpenCode: skipping unparseable JSON line: %.200s", line)
        return None
    return data if isinstance(data, dict) else None


def _extract_session_id(data: dict[str, Any], current: str | None) -> str | None:
    """Extract session_id from event data."""
    if current is not None:
        return current
    sid = data.get("session_id") or data.get("sessionId") or data.get("id")
    return sid.strip() if isinstance(sid, str) and sid.strip() else current


def _extract_usage(data: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract usage from event data."""
    event_type = data.get("type", "")

    # Prefer usage from completion/result events.
    if event_type in ("result", "message.completed", "session.completed"):
        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict):
            return raw_usage

    # Fall back to stats block.
    stats = data.get("stats")
    if isinstance(stats, dict) and current is None:
        return {
            "input_tokens": stats.get("input_tokens", 0),
            "output_tokens": stats.get("output_tokens", 0),
        }

    if current is None:
        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict):
            return raw_usage
    return current


def _extract_text(data: dict[str, Any], parts: list[str]) -> None:
    """Extract assistant text from OpenCode events."""
    event_type = data.get("type", "")

    # message.part.updated / message.completed with content.
    if event_type.startswith("message.") or event_type == "text":
        # Try nested part.text first (opencode format: {type:"text", part:{text:"..."}})
        part = data.get("part", {})
        content = (
            (part.get("text", "") if isinstance(part, dict) else "")
            or data.get("content")
            or data.get("text")
        )
        if isinstance(content, str) and content:
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        parts.append(text)
        return

    # Direct text/result fields.
    for key in ("result", "text", "content"):
        value = data.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
            return


def _dispatch_event(data: dict[str, Any]) -> list[StreamEvent]:
    """Route a parsed OpenCode event to the appropriate handler."""
    event_type = data.get("type", "")

    # Session init events.
    if event_type in ("session.started", "session.created", "step_start"):
        sid = data.get("session_id") or data.get("sessionId") or data.get("sessionID")
        if not sid:
            part = data.get("part", {})
            sid = part.get("sessionID") if isinstance(part, dict) else None
        return [
            SystemInitEvent(
                type="system",
                subtype="init",
                session_id=sid if isinstance(sid, str) else None,
            ),
        ]

    # Completion / result events.
    if event_type in ("session.completed", "message.completed", "result", "step_finish"):
        part = data.get("part", {})
        tokens = part.get("tokens", {}) if isinstance(part, dict) else {}
        raw_usage = data.get("usage") or (
            {"input_tokens": tokens.get("input", 0), "output_tokens": tokens.get("output", 0)}
            if tokens else None
        )
        return [
            ResultEvent(
                type="result",
                result=data.get("text", ""),
                usage=raw_usage if isinstance(raw_usage, dict) else {},
                session_id=_str_or_none(data, "session_id") or data.get("sessionID"),
            ),
        ]

    # Error events.
    if event_type in ("error", "session.failed"):
        error = data.get("error", {})
        msg = error.get("message", "") if isinstance(error, dict) else str(error)
        return [ResultEvent(type="result", result=msg, is_error=True)]

    # Text / content updates.
    if event_type in ("message.part.updated", "message.delta", "content.delta", "text"):
        return _parse_content_update(data)

    # Tool events.
    if event_type in ("tool.started", "tool.running", "tool_use"):
        name = data.get("name") or data.get("tool_name") or data.get("tool", "")
        params = data.get("parameters") or data.get("input")
        if name:
            return [
                ToolUseEvent(
                    type="assistant",
                    tool_name=str(name),
                    parameters=params if isinstance(params, dict) else None,
                )
            ]

    return []


def _parse_content_update(data: dict[str, Any]) -> list[StreamEvent]:
    """Parse a content update event into thinking or text delta."""
    part = data.get("part", data)
    part_type = part.get("type", "") if isinstance(part, dict) else ""

    text = ""
    if isinstance(part, dict):
        text = part.get("text", "") or part.get("content", "")
    if not text:
        text = data.get("text", "") or data.get("content", "")

    if not text:
        return []

    if part_type in ("thinking", "reasoning"):
        return [ThinkingEvent(type="assistant", text=text)]

    return [AssistantTextDelta(type="assistant", text=text)]


def _str_or_none(data: dict[str, Any], key: str) -> str | None:
    """Extract a string value or return None."""
    value = data.get(key)
    return value if isinstance(value, str) else None
