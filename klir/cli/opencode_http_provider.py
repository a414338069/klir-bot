"""HTTP client provider that communicates with ``opencode serve`` via REST API.

Implements :class:`BaseCLI` using blocking HTTP calls to a local
``opencode serve`` instance managed by :class:`OpenCodeServerManager`.

Session lifecycle:
  - ``POST /session`` → create new session (or reuse existing session_id)
  - ``POST /session/{id}/message`` → send prompt, wait for full AI response

Streaming is a single-event fallback wrapping :meth:`send`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from klir.cli.base import BaseCLI, CLIConfig
from klir.cli.server_manager import get_server_manager
from klir.cli.stream_events import ResultEvent, StreamEvent
from klir.cli.types import CLIResponse

if TYPE_CHECKING:
    from klir.cli.server_manager import OpenCodeServerManager
    from klir.cli.timeout_controller import TimeoutController

logger = logging.getLogger("klir.cli.opencode_http")

# ---------------------------------------------------------------------------


def _parse_error_body(text: str) -> str:
    """Extract a human-readable error message from an API error JSON body.

    Expects ``{"name": "ErrorName", "data": {"message": "..."}}``.
    Falls back to the raw text if parsing fails.
    """
    try:
        data = json.loads(text)
        name = data.get("name", "UnknownError")
        msg = data.get("data", {}).get("message", "")
        detail = f"{name}" if not msg else f"{name}: {msg}"
        return detail if detail else text[:500]
    except (json.JSONDecodeError, AttributeError):
        return text[:500] if text else "(empty error body)"


def _parse_model_string(model: str | None) -> dict[str, str] | None:
    """Parse a model string like ``"zhipuai-coding-plan/glm-5.1"`` into
    ``{"providerID": "...", "modelID": "..."}``.

    Returns ``None`` when *model* is empty/None so the caller can omit the
    ``model`` key in the request body (server uses its default).
    """
    if not model:
        return None
    if "/" in model:
        provider_id, _, model_id = model.partition("/")
        return {"providerID": provider_id, "modelID": model_id}
    return {"modelID": model}


def _extract_text_from_parts(parts: list[dict[str, Any]]) -> str:
    """Join ``text`` fields from parts where ``type == "text"``."""
    chunks = [p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text"]
    return "\n".join(chunks)


def _extract_tokens(info: dict[str, Any]) -> dict[str, Any]:
    """Extract a flat token-usage dict from the nested ``info.tokens`` structure.

    Returns a dict with keys matching :attr:`CLIResponse.usage` convention:
    ``input_tokens``, ``output_tokens``, ``total_tokens``, ``cache_read_tokens``,
    ``cache_write_tokens``.
    """
    tokens = info.get("tokens", {}) or {}
    return {
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "total_tokens": tokens.get("total", 0),
        "cache_read_tokens": (tokens.get("cache") or {}).get("read", 0),
        "cache_write_tokens": (tokens.get("cache") or {}).get("write", 0),
    }


class OpenCodeHTTPCLI(BaseCLI):
    """HTTP client for an ``opencode serve`` backend.

    Communicates with a locally-running ``opencode serve`` instance via
    its REST API.  The server is lazily started through
    :class:`OpenCodeServerManager` on the first :meth:`send` call.

    .. code-block:: python

        config = CLIConfig(working_dir="/path/to/project", model="zhipuai-coding-plan/glm-5.1")
        cli = OpenCodeHTTPCLI(config)
        response = await cli.send("Hello, world!")
        print(response.result)
        await cli.close()
    """

    def __init__(self, config: CLIConfig) -> None:
        self._config = config
        self._working_dir = str(Path(config.working_dir).resolve())
        self._session: aiohttp.ClientSession | None = None
        self._server_manager: OpenCodeServerManager | None = None

    async def send(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,  # noqa: ARG002
    ) -> CLIResponse:
        """Send a prompt via HTTP and return the final CLIResponse.

        Session logic:
          - *resume_session* provides an existing opencode session UUID.
          - Otherwise a new session is created via ``POST /session``.

        Timeouts are enforced via ``aiohttp.ClientTimeout``.  All
        exceptions (connection errors, timeouts, API errors, JSON parse
        failures) are caught and returned as ``CLIResponse(is_error=True)``.
        """
        start = time.monotonic()
        try:
            base_url = await self._ensure_server()
            session = await self._ensure_session()

            composed_prompt = self._compose_prompt(prompt)

            session_id: str
            if resume_session:
                session_id = resume_session
                logger.info("Resuming opencode session %s", session_id[:8])
            else:
                session_id = await self._create_session(base_url, session)
                logger.info("Created opencode session %s", session_id[:8])

            body = self._build_message_body(composed_prompt)

            message_url = f"{base_url}/session/{session_id}/message"
            http_timeout = max(timeout_seconds or 7200.0, 7200.0)
            client_timeout = aiohttp.ClientTimeout(total=http_timeout)

            logger.debug("POST %s (timeout=%s)", message_url, timeout_seconds)
            async with session.post(message_url, json=body, timeout=client_timeout) as resp:
                raw_text = await resp.text()

            duration_ms = (time.monotonic() - start) * 1000

            if resp.status != 200:
                error_msg = _parse_error_body(raw_text)
                logger.error("API error %d from %s: %s", resp.status, message_url, error_msg)
                return CLIResponse(
                    session_id=session_id,
                    result=error_msg,
                    is_error=True,
                    returncode=resp.status,
                    duration_ms=duration_ms,
                )

            return self._parse_response(raw_text, session_id, duration_ms)

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("Request timed out after %.1fs", (duration_ms / 1000))
            return CLIResponse(
                result="Request timed out",
                is_error=True,
                timed_out=True,
                duration_ms=duration_ms,
            )
        except aiohttp.ClientError as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("HTTP client error: %s", exc)
            return CLIResponse(
                result=f"Connection error: {exc}",
                is_error=True,
                duration_ms=duration_ms,
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("Response parse error: %s", exc)
            return CLIResponse(
                result=f"Failed to parse response: {exc}",
                is_error=True,
                duration_ms=duration_ms,
            )

    async def send_streaming(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Streaming fallback — wraps :meth:`send` and yields a single
        :class:`ResultEvent`.

        Per Decision D2, this provider uses blocking HTTP only (no SSE).
        """
        response = await self.send(
            prompt=prompt,
            resume_session=resume_session,
            continue_session=continue_session,
            timeout_seconds=timeout_seconds,
            timeout_controller=timeout_controller,
        )
        yield ResultEvent(
            type="result",
            session_id=response.session_id,
            result=response.result,
            is_error=response.is_error,
            returncode=response.returncode,
            duration_ms=response.duration_ms,
            total_cost_usd=response.total_cost_usd,
            usage=response.usage,
        )

    async def close(self) -> None:
        """Release the aiohttp session.  The server is NOT shut down —
        it remains available for other callers managed by the shared
        :class:`OpenCodeServerManager`.
        """
        if self._session is not None:
            await self._session.close()
            self._session = None
            logger.debug("Closed aiohttp session")

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Return (or lazily create) the shared aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _ensure_server(self) -> str:
        """Return the base URL of the ``opencode serve`` instance for
        the configured working directory, lazily starting it if needed.
        """
        if self._server_manager is None:
            self._server_manager = get_server_manager()
        instance = await self._server_manager.get_or_create(self._working_dir)
        return instance.base_url

    def _compose_prompt(self, prompt: str) -> str:
        """Inject system context into the user prompt."""
        cfg = self._config
        parts: list[str] = []
        if cfg.system_prompt:
            parts.append(cfg.system_prompt)
        parts.append(prompt)
        if cfg.append_system_prompt:
            parts.append(cfg.append_system_prompt)
        return "\n\n".join(parts)

    async def _create_session(
        self, base_url: str, session: aiohttp.ClientSession,
    ) -> str:
        """Create a new opencode session via ``POST /session``.

        Returns the session UUID (``.id`` field).
        """
        url = f"{base_url}/session"
        body: dict[str, Any] = {}
        model = _parse_model_string(self._config.model)
        if model is not None:
            body.update(model)
        logger.debug("POST %s (create session) body=%s", url, body)
        async with session.post(url, json=body) as resp:
            raw = await resp.text()
        if resp.status != 200:
            error_msg = _parse_error_body(raw)
            raise aiohttp.ClientError(f"Failed to create session: {error_msg}")
        data = json.loads(raw)
        session_id = data.get("id")
        if not session_id:
            raise aiohttp.ClientError(f"POST /session returned no id: {raw[:200]}")
        return session_id

    def _build_message_body(self, prompt: str) -> dict[str, Any]:
        """Build the JSON body for ``POST /session/{id}/message``."""
        cfg = self._config
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": prompt}],
        }

        model = _parse_model_string(cfg.model)
        if model is not None:
            body["model"] = model

        if cfg.agent_name and cfg.agent_name != "main":
            body["agent"] = cfg.agent_name

        return body

    def _parse_response(
        self, raw_text: str, session_id: str, duration_ms: float,
    ) -> CLIResponse:
        """Parse the full JSON response from ``POST /session/{id}/message``
        into a :class:`CLIResponse`.
        """
        data = json.loads(raw_text)
        parts = data.get("parts", [])
        info = data.get("info", {})

        result_text = _extract_text_from_parts(parts)
        usage = _extract_tokens(info)

        time_info = info.get("time", {}) or {}
        created = time_info.get("created", 0)
        completed = time_info.get("completed", 0)
        duration_api_ms = (completed - created) if created and completed else None

        cost = info.get("cost", 0)
        total_cost_usd = float(cost) if cost else None

        logger.info(
            "OpenCode HTTP done session=%s tokens=%d cost=%.6f",
            session_id[:8],
            usage.get("total_tokens", 0),
            total_cost_usd or 0,
        )

        return CLIResponse(
            session_id=session_id,
            result=result_text,
            is_error=False,
            returncode=0,
            duration_ms=duration_ms,
            duration_api_ms=duration_api_ms,
            total_cost_usd=total_cost_usd,
            usage=usage,
        )
