"""Async wrapper around the OpenCode CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING

from klir.cli.base import (
    BaseCLI,
    CLIConfig,
)
from klir.cli.executor import (
    SubprocessResult,
    SubprocessSpec,
    run_oneshot_subprocess,
    run_streaming_subprocess,
)
from klir.cli.opencode_events import (
    parse_opencode_json,
    parse_opencode_stream_event,
)
from klir.cli.stream_events import (
    AssistantTextDelta,
    ResultEvent,
    StreamEvent,
    SystemInitEvent,
)
from klir.cli.types import CLIResponse

if TYPE_CHECKING:
    from klir.cli.timeout_controller import TimeoutController

logger = logging.getLogger(__name__)


class _StreamState:
    """Mutable accumulator for streaming session data."""

    __slots__ = ("accumulated_text", "session_id")

    def __init__(self) -> None:
        self.accumulated_text: list[str] = []
        self.session_id: str | None = None

    def track(self, event: StreamEvent) -> None:
        """Update state from a single stream event."""
        if isinstance(event, SystemInitEvent) and event.session_id:
            self.session_id = event.session_id
        elif isinstance(event, AssistantTextDelta) and event.text:
            self.accumulated_text.append(event.text)


class OpenCodeCLI(BaseCLI):
    """Async wrapper around the OpenCode CLI."""

    def __init__(self, config: CLIConfig) -> None:
        self._config = config
        self._working_dir = Path(config.working_dir).resolve()
        self._cli = self._find_cli()
        logger.info("OpenCode CLI wrapper: cwd=%s, model=%s", self._working_dir, config.model)

    @staticmethod
    def _find_cli() -> str:
        path = which("opencode")
        if not path:
            msg = (
                "opencode CLI not found on PATH. "
                "Install via: curl -fsSL https://opencode.ai/install | bash"
            )
            raise FileNotFoundError(msg)
        return path

    def _compose_prompt(self, prompt: str) -> str:
        """Inject system context into user prompt."""
        cfg = self._config
        parts: list[str] = []
        if cfg.system_prompt:
            parts.append(cfg.system_prompt)
        parts.append(prompt)
        if cfg.append_system_prompt:
            parts.append(cfg.append_system_prompt)
        return "\n\n".join(parts)

    def _build_command(
        self,
        prompt: str,
        resume_session: str | None = None,
        *,
        json_output: bool = True,
    ) -> list[str]:
        cfg = self._config
        final_prompt = self._compose_prompt(prompt)

        cmd = [self._cli, "run", final_prompt]

        if json_output:
            cmd += ["--format", "json"]

        if cfg.model:
            cmd += ["--model", cfg.model]
        if resume_session:
            cmd.append("--continue")

        if cfg.allowed_tools:
            cmd += ["--allowedTools", ",".join(cfg.allowed_tools)]
        if cfg.disallowed_tools:
            cmd += ["--excludedTools", ",".join(cfg.disallowed_tools)]

        if cfg.cli_parameters:
            cmd.extend(cfg.cli_parameters)

        return cmd

    async def send(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> CLIResponse:
        """Send a prompt and return the final result."""
        effective_resume = resume_session or ("continue" if continue_session else None)
        cmd = self._build_command(prompt, effective_resume, json_output=True)
        use_cwd = str(self._working_dir)
        _log_cmd(cmd)
        return await run_oneshot_subprocess(
            config=self._config,
            spec=SubprocessSpec(cmd, use_cwd, prompt, timeout_seconds, timeout_controller),
            parse_output=self._parse_output,
            provider_label="OpenCode",
        )

    async def send_streaming(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Send a prompt and yield stream events as they arrive."""
        effective_resume = resume_session or ("continue" if continue_session else None)
        cmd = self._build_command(prompt, effective_resume, json_output=True)
        use_cwd = str(self._working_dir)
        _log_cmd(cmd, streaming=True)

        state = _StreamState()

        async def line_handler(line: str) -> AsyncGenerator[StreamEvent, None]:
            if not line:
                return
            for event in parse_opencode_stream_event(line):
                state.track(event)
                yield event

        async def post_handler(result: SubprocessResult) -> AsyncGenerator[StreamEvent, None]:
            yield _opencode_final_result(result, state.accumulated_text, state.session_id)

        async for event in run_streaming_subprocess(
            config=self._config,
            spec=SubprocessSpec(cmd, use_cwd, prompt, timeout_seconds, timeout_controller),
            line_handler=line_handler,
            provider_label="OpenCode",
            post_handler=post_handler,
        ):
            yield event

    @staticmethod
    def _parse_output(
        stdout: bytes,
        stderr: bytes,
        returncode: int | None,
    ) -> CLIResponse:
        """Parse OpenCode subprocess output into a CLIResponse."""
        stderr_text = stderr.decode(errors="replace")[:2000] if stderr else ""
        if stderr_text:
            logger.warning("OpenCode stderr (exit=%s): %s", returncode, stderr_text[:500])

        raw = stdout.decode(errors="replace").strip()
        if not raw:
            logger.error("OpenCode returned empty output (exit=%s)", returncode)
            return CLIResponse(result="", is_error=True, returncode=returncode, stderr=stderr_text)

        is_error = returncode != 0
        result_text, session_id, usage = parse_opencode_json(raw)
        response = CLIResponse(
            session_id=session_id,
            result=result_text or raw,
            is_error=is_error or not result_text,
            returncode=returncode,
            stderr=stderr_text,
            usage=usage or {},
        )

        if response.is_error:
            logger.error("OpenCode error exit=%s: %s", returncode, response.result[:300])
        else:
            logger.info(
                "OpenCode done session=%s tokens=%d",
                (response.session_id or "?")[:8],
                response.total_tokens,
            )

        return response


def _opencode_final_result(
    result: SubprocessResult,
    accumulated_text: list[str],
    session_id: str | None,
) -> ResultEvent:
    """Build the final ResultEvent after the stream loop completes."""
    stderr_text = result.stderr_bytes.decode(errors="replace")[:2000] if result.stderr_bytes else ""

    if result.process.returncode != 0:
        error_detail = stderr_text or "\n".join(accumulated_text) or "(no output)"
        logger.error(
            "OpenCode stream exited with code %d: %s",
            result.process.returncode,
            error_detail[:300],
        )
        return ResultEvent(
            type="result",
            result=error_detail[:500],
            is_error=True,
            returncode=result.process.returncode,
        )

    return ResultEvent(
        type="result",
        session_id=session_id,
        result="\n".join(accumulated_text),
        is_error=False,
        returncode=result.process.returncode,
    )


def _log_cmd(cmd: list[str], *, streaming: bool = False) -> None:
    """Log the CLI command with truncated long values."""
    safe_cmd = [(c[:80] + "...") if len(c) > 80 else c for c in cmd]
    prefix = "OpenCode stream cmd" if streaming else "OpenCode cmd"
    logger.info("%s: %s", prefix, " ".join(safe_cmd))
