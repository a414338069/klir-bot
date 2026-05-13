"""CLI backend factory -- returns the right provider based on config."""

from __future__ import annotations

import logging

from klir.cli.base import BaseCLI, CLIConfig

logger = logging.getLogger(__name__)


def create_cli(config: CLIConfig) -> BaseCLI:
    """Create a CLI backend instance based on ``config.provider``."""
    logger.debug("CLI factory creating provider=%s", config.provider)
    if config.provider == "gemini":
        from klir.cli.gemini_provider import GeminiCLI

        return GeminiCLI(config)

    if config.provider == "codex":
        from klir.cli.codex_provider import CodexCLI

        return CodexCLI(config)

    if config.provider == "opencode":
        from klir.cli.opencode_provider import OpenCodeCLI

        return OpenCodeCLI(config)

    if config.provider == "opencode_http":
        from klir.cli.opencode_http_provider import OpenCodeHTTPCLI

        return OpenCodeHTTPCLI(config)

    from klir.cli.claude_provider import ClaudeCodeCLI

    return ClaudeCodeCLI(config)
