"""Central authority for CLI parameter and model resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from klir.errors import KlirError

if TYPE_CHECKING:
    from klir.cli.codex_cache import CodexModelCache
    from klir.config import AgentConfig

from klir.config import _GEMINI_ALIASES, CLAUDE_MODELS, get_gemini_models


def _looks_like_gemini_model(model: str) -> bool:
    return model.startswith(("gemini-", "auto-gemini-"))


def _validate_gemini_model(model: str) -> None:
    gemini_models = get_gemini_models()
    if model in _GEMINI_ALIASES:
        return
    if gemini_models and model not in gemini_models:
        msg = f"Invalid Gemini model: {model}. Must be one of {sorted(gemini_models)}"
        raise KlirError(msg)
    if not gemini_models and not _looks_like_gemini_model(model):
        msg = (
            f"Invalid Gemini model: {model}. Must use a Gemini model ID "
            "(e.g. gemini-2.5-pro) or Gemini alias."
        )
        raise KlirError(msg)


@dataclass(frozen=True)
class TaskOverrides:
    """Per-task configuration overrides from CronJob or WebhookEntry."""

    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    cli_parameters: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TaskExecutionConfig:
    """Resolved configuration for a single CLI execution."""

    provider: str
    model: str
    reasoning_effort: str
    cli_parameters: list[str]
    permission_mode: str
    working_dir: str
    file_access: str
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)


def resolve_cli_config(
    base_config: AgentConfig,
    codex_cache: CodexModelCache | None,
    *,
    task_overrides: TaskOverrides | None = None,
) -> TaskExecutionConfig:
    """Merge global config with task overrides, validate, return execution config.

    Logic:
    1. Resolve provider (task override → global config)
    2. Resolve model (task override → global config)
    3. Validate model against cache (Claude hardcoded, Codex from cache)
    4. Resolve reasoning effort (Codex only, validate against model's supported efforts)
    5. Merge CLI parameters (global + task-specific)
    6. Return immutable TaskExecutionConfig

    Args:
        base_config: Global agent configuration
        codex_cache: Codex model cache (optional, required for Codex validation)
        task_overrides: Task-specific overrides (optional)

    Returns:
        TaskExecutionConfig with resolved and validated settings

    Raises:
        KlirError: If model validation fails
    """
    overrides = task_overrides or TaskOverrides()

    # 1. Resolve provider
    provider = overrides.provider or base_config.provider

    # 2. Resolve model
    model = overrides.model or base_config.model

    # 3. Validate model
    if provider == "claude":
        if model not in CLAUDE_MODELS:
            msg = f"Invalid Claude model: {model}. Must be one of {sorted(CLAUDE_MODELS)}"
            raise KlirError(msg)
    elif provider == "gemini":
        _validate_gemini_model(model)
    elif provider == "opencode":
        pass  # OpenCode uses provider/model format; validated by the CLI itself
    else:  # codex
        if codex_cache is None:
            msg = "Codex cache is required for Codex model validation"
            raise KlirError(msg)
        if not codex_cache.validate_model(model):
            msg = f"Invalid Codex model: {model}"
            raise KlirError(msg)

    # 4. Resolve reasoning effort (Codex only)
    reasoning_effort = ""
    if provider == "codex":
        requested_effort = overrides.reasoning_effort or base_config.reasoning_effort

        # Check if model supports reasoning and if effort is valid
        if codex_cache and requested_effort:
            model_info = codex_cache.get_model(model)
            if (
                model_info
                and model_info.supported_efforts
                and requested_effort in model_info.supported_efforts
            ):
                reasoning_effort = requested_effort
            # Otherwise, fall back to empty (invalid effort or model doesn't support reasoning)

    # 5. Merge CLI parameters (currently no provider-specific params in flat config)
    cli_parameters = [*overrides.cli_parameters]

    # 6. Return immutable config
    return TaskExecutionConfig(
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        cli_parameters=cli_parameters,
        permission_mode=base_config.permission_mode,
        working_dir=base_config.klir_home,
        file_access=base_config.file_access,
        allowed_tools=list(base_config.allowed_tools),
        disallowed_tools=list(base_config.disallowed_tools),
    )
