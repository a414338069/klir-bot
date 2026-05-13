"""Application configuration and model registry."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

ReplyToMode = Literal["off", "first", "all"]

logger = logging.getLogger(__name__)
NULLISH_TEXT_VALUES: frozenset[str] = frozenset({"null", "none"})
DEFAULT_EMPTY_GEMINI_API_KEY: str = "null"

# Intentional bind-all: the API is designed for private-network use (Tailscale).
# Public exposure is gated by ``allow_public`` + a prominent warning at startup.
_BIND_ALL_INTERFACES: str = ".".join(["0"] * 4)

# Pre-build a safe UTC fallback.  On Windows without the ``tzdata`` package
# (now a declared dependency), ``ZoneInfo("UTC")`` raises.  The fallback
# is a minimal ``datetime.tzinfo`` subclass with a ``.key`` attribute so
# callers that log ``tz.key`` keep working.
try:
    _SAFE_UTC: ZoneInfo = ZoneInfo("UTC")
except (ZoneInfoNotFoundError, KeyError):
    import datetime as _dt

    class _UTCFallback(_dt.tzinfo):  # pragma: no cover
        """Minimal UTC stand-in for systems without IANA timezone data."""

        key: str = "UTC"
        _ZERO = _dt.timedelta(0)

        def utcoffset(self, dt: _dt.datetime | None) -> _dt.timedelta:
            return self._ZERO

        def tzname(self, dt: _dt.datetime | None) -> str:
            return "UTC"

        def dst(self, dt: _dt.datetime | None) -> _dt.timedelta:
            return self._ZERO

    _SAFE_UTC = _UTCFallback()  # type: ignore[assignment]
    logger.warning("tzdata package missing — using built-in UTC fallback")


class StreamingConfig(BaseModel):
    """Settings for streaming response output."""

    enabled: bool = True
    min_chars: int = 200
    max_chars: int = 4000
    idle_ms: int = 800
    edit_interval_seconds: float = 2.0
    max_edit_failures: int = 3
    append_mode: bool = False
    sentence_break: bool = True


class ReactionConfig(BaseModel):
    """Settings for Telegram message reactions."""

    level: str = "ack"
    ack_emoji: str = "👀"
    done_emoji: str = "✅"
    error_emoji: str = "❌"

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        if v not in ("off", "ack"):
            msg = f"reaction level must be 'off' or 'ack', got '{v}'"
            raise ValueError(msg)
        return v


_DEFAULT_HEARTBEAT_PROMPT = (
    "You are running as a background heartbeat check. Review the current workspace context:\n"
    "- Read memory_system/MAINMEMORY.md for user interests and personality\n"
    "- Check cron_tasks/ for active projects\n"
    "- Think about what might be useful, interesting, or fun for the user\n"
    "\n"
    "If you have a creative idea, suggestion, interesting fact, or something the user might enjoy:\n"
    "Reply with your message directly.\n"
    "\n"
    "If nothing needs attention right now:\n"
    "Reply exactly: HEARTBEAT_OK"
)

_DEFAULT_HEARTBEAT_ACK = "HEARTBEAT_OK"


class HeartbeatGroupTarget(BaseModel):
    """A single group/topic heartbeat target with independent scheduling."""

    enabled: bool = True
    chat_id: int
    topic_id: int | None = None
    prompt: str = _DEFAULT_HEARTBEAT_PROMPT
    interval_minutes: int = 30
    quiet_start: int | None = None  # None = inherit global
    quiet_end: int | None = None  # None = inherit global


class HeartbeatConfig(BaseModel):
    """Settings for the periodic heartbeat system."""

    enabled: bool = False
    interval_minutes: int = 30
    cooldown_minutes: int = 5
    quiet_start: int = 21
    quiet_end: int = 8
    prompt: str = _DEFAULT_HEARTBEAT_PROMPT
    ack_token: str = _DEFAULT_HEARTBEAT_ACK
    group_targets: list[HeartbeatGroupTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_duplicate_targets(self) -> HeartbeatConfig:
        """Reject duplicate (chat_id, topic_id) pairs in group_targets."""
        seen: set[tuple[int, int | None]] = set()
        for t in self.group_targets:
            key = (t.chat_id, t.topic_id)
            if key in seen:
                msg = f"Duplicate heartbeat target: chat_id={t.chat_id}, topic_id={t.topic_id}"
                raise ValueError(msg)
            seen.add(key)
        return self


class CleanupConfig(BaseModel):
    """Settings for automatic file cleanup of workspace directories."""

    enabled: bool = True
    telegram_files_days: int = 30
    output_to_user_days: int = 30
    api_files_days: int = 30
    chat_activity_days: int = 90
    session_retention_days: int = 30
    history_retention_days: int = 30
    check_hour: int = 3


class CLIParametersConfig(BaseModel):
    """CLI parameters for main agent."""

    claude: list[str] = Field(default_factory=list)
    codex: list[str] = Field(default_factory=list)
    gemini: list[str] = Field(default_factory=list)
    opencode: list[str] = Field(default_factory=list)
    http: list[str] = Field(default_factory=list)


class TasksConfig(BaseModel):
    """Settings for background task delegation."""

    enabled: bool = True
    max_parallel: int = 5
    timeout_seconds: float = 3600.0


class TimeoutConfig(BaseModel):
    """Per-execution-path timeout settings."""

    normal: float = 1800.0
    background: float = 1800.0
    subagent: float = 3600.0
    warning_intervals: list[float] = Field(default_factory=lambda: [60.0, 10.0])
    extend_on_activity: bool = True
    activity_extension: float = 120.0
    max_extensions: int = 3


class WebhookConfig(BaseModel):
    """Settings for the webhook HTTP server."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8742
    token: str = ""
    max_body_bytes: int = 262144
    rate_limit_per_minute: int = 30


class DashboardConfig(BaseModel):
    """Settings for the real-time web dashboard."""

    enabled: bool = True
    e2e_required: bool = False
    history_retention_days: int = 30
    max_clients: int = 5


TailscaleMode = Literal["off", "serve", "funnel"]


class TailscaleConfig(BaseModel):
    """Settings for Tailscale network exposure.

    ``serve`` exposes the dashboard privately to your tailnet (HTTPS).
    ``funnel`` exposes it publicly via the internet (HTTPS).
    Both proxy to localhost, so the API server binds to ``127.0.0.1``.
    """

    mode: TailscaleMode = "off"
    reset_on_exit: bool = True


class ApiConfig(BaseModel):
    """Settings for the direct WebSocket API server.

    Designed for use over Tailscale or other private networks.
    When ``allow_public`` is False and Tailscale is not detected,
    the server still starts but logs a prominent warning.

    ``chat_id`` controls which session the API client uses.
    ``0`` means "use the first ``allowed_user_ids`` entry".
    """

    enabled: bool = True
    host: str = _BIND_ALL_INTERFACES
    port: int = 8741
    token: str = ""
    chat_id: int = 0
    allow_public: bool = False
    tailscale: TailscaleConfig = Field(default_factory=TailscaleConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)


def deep_merge_config(
    user: dict[str, object],
    defaults: dict[str, object],
) -> tuple[dict[str, object], bool]:
    """Recursively merge *defaults* into *user*, preserving user values.

    Returns ``(merged_dict, changed)`` where *changed* is True when new keys were added.
    """
    result: dict[str, object] = dict(user)
    changed = False
    new_keys = 0
    for key, default_val in defaults.items():
        if key not in result:
            result[key] = default_val
            changed = True
            new_keys += 1
        elif isinstance(default_val, dict) and isinstance(result[key], dict):
            sub_merged, sub_changed = deep_merge_config(
                result[key],  # type: ignore[arg-type]
                default_val,
            )
            result[key] = sub_merged
            changed = changed or sub_changed
    if new_keys:
        logger.info("Config deep-merge: %d new keys added", new_keys)
    return result, changed


def update_config_file(config_path: Path, **updates: object) -> None:
    """Update specific keys in config.json without overwriting other user settings."""
    from klir.infra.json_store import atomic_json_save

    data: dict[str, object] = json.loads(config_path.read_text(encoding="utf-8"))
    data.update(updates)
    atomic_json_save(config_path, data)
    logger.info("Persisted config update: %s", ", ".join(f"{k}={v}" for k, v in updates.items()))


async def update_config_file_async(config_path: Path, **updates: object) -> None:
    """Async wrapper: update config.json without blocking the event loop."""
    import asyncio

    await asyncio.to_thread(update_config_file, config_path, **updates)


class PairingConfig(BaseModel):
    """Settings for DM pairing / self-service onboarding."""

    enabled: bool = False
    code_ttl_minutes: int = 60
    code_length: int = 6
    max_active_codes: int = 10


class PollConfig(BaseModel):
    """Settings for Telegram poll creation."""

    enabled: bool = False
    is_anonymous: bool = True


class ForwardingConfig(BaseModel):
    """Settings for message forwarding and copying."""

    enabled: bool = False


class ChatOverrides(BaseModel):
    """Per-chat configuration overrides. All fields optional (None = use global)."""

    provider: str | None = None
    model: str | None = None
    streaming: StreamingConfig | None = None
    group_mention_only: bool | None = None
    require_mention: bool | None = None
    enabled: bool | None = None
    reply_to_mode: ReplyToMode | None = None


class ProxyConfig(BaseModel):
    """HTTP/SOCKS5 proxy for Telegram API calls."""

    url: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.url.strip())


class ApprovalConfig(BaseModel):
    """Settings for tool execution approval routing."""

    enabled: bool = False
    approver_ids: list[int] = Field(default_factory=list)
    timeout_seconds: int = 120
    target: Literal["dm", "channel", "both"] = "dm"
    auto_approve_tools: list[str] = Field(default_factory=list)
    auto_deny_on_timeout: bool = True


class ThreadBindingConfig(BaseModel):
    """Settings for thread/topic binding lifecycle management."""

    enabled: bool = True
    idle_timeout_minutes: int = 60
    max_age_minutes: int = 1440
    cleanup_interval_minutes: int = 15


class ResilienceConfig(BaseModel):
    """Settings for Telegram API retry and backoff behavior."""

    max_retries: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    jitter: bool = True


_ALLOWED_IMAGE_FORMATS: frozenset[str] = frozenset({"webp", "jpeg", "jpg", "png"})


class ImageConfig(BaseModel):
    """Settings for automatic image resizing and format conversion."""

    max_dimension: int = 2000
    output_format: str = "webp"
    quality: int = 85

    @field_validator("max_dimension")
    @classmethod
    def _validate_max_dimension(cls, v: int) -> int:
        if v < 1:
            msg = "max_dimension must be >= 1"
            raise ValueError(msg)
        return v

    @field_validator("quality")
    @classmethod
    def _validate_quality(cls, v: int) -> int:
        if not 1 <= v <= 100:
            msg = "quality must be between 1 and 100"
            raise ValueError(msg)
        return v

    @field_validator("output_format")
    @classmethod
    def _validate_output_format(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in _ALLOWED_IMAGE_FORMATS:
            msg = f"output_format must be one of {sorted(_ALLOWED_IMAGE_FORMATS)}"
            raise ValueError(msg)
        return normalized


class SceneConfig(BaseModel):
    """Visual presentation settings."""

    technical_footer: bool = False


class UserMessageHookConfig(BaseModel):
    """User-defined message hook from config.json."""

    name: str
    phase: Literal["pre", "post"]
    action: Literal["prepend", "append", "replace"]
    text: str = ""
    condition: Literal["always", "regex", "provider"] = "always"
    pattern: str = ""
    provider: str = ""
    enabled: bool = True


class AgentConfig(BaseModel):
    """Top-level configuration loaded from config.json."""

    log_level: str = "INFO"
    provider: str = "claude"
    model: str = "opus"
    working_dir: str = ""
    klir_home: str = "~/.klir"
    idle_timeout_minutes: int = 1440
    session_age_warning_hours: int = 12
    daily_reset_hour: int = 4
    daily_reset_enabled: bool = False
    max_budget_usd: float | None = None
    max_turns: int | None = None
    max_session_messages: int | None = None
    permission_mode: str = "bypassPermissions"
    cli_timeout: float = 1800.0
    reasoning_effort: str = "medium"
    file_access: str = "all"
    gemini_api_key: str | None = None
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    reactions: ReactionConfig = Field(default_factory=ReactionConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    webhooks: WebhookConfig = Field(default_factory=WebhookConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    cli_parameters: CLIParametersConfig = Field(default_factory=CLIParametersConfig)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    user_timezone: str = ""
    language: str = "en"
    update_check: bool = True
    interagent_port: int = 8799
    peer_isolation: bool = False
    group_mention_only: bool = False
    telegram_token: str = ""
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_group_ids: list[int] = Field(default_factory=list)
    allowed_channel_ids: list[int] = Field(default_factory=list)
    pairing: PairingConfig = Field(default_factory=PairingConfig)
    polls: PollConfig = Field(default_factory=PollConfig)
    forwarding: ForwardingConfig = Field(default_factory=ForwardingConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    thread_binding: ThreadBindingConfig = Field(default_factory=ThreadBindingConfig)
    reply_to_mode: ReplyToMode = "first"
    resilience: ResilienceConfig = Field(default_factory=ResilienceConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    scene: SceneConfig = Field(default_factory=SceneConfig)
    message_hooks: list[UserMessageHookConfig] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    tool_loop_threshold: int = 0
    chat_overrides: dict[str, dict[str, object]] = Field(default_factory=dict)

    @property
    def allowed_forward_targets(self) -> set[int]:
        """Union of all authorized chat IDs for forward/copy security."""
        return (
            set(self.allowed_user_ids) | set(self.allowed_group_ids) | set(self.allowed_channel_ids)
        )

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _normalize_gemini_api_key(cls, value: object) -> object:
        """Normalize null-like string values to ``None`` for optional key config."""
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized or normalized.lower() in NULLISH_TEXT_VALUES:
            return None
        return normalized

    @model_validator(mode="after")
    def _sync_cli_timeout_to_timeouts(self) -> AgentConfig:
        """Sync legacy ``cli_timeout`` to ``timeouts.normal`` for backward compat.

        When ``cli_timeout`` differs from the default 1800.0 and ``timeouts.normal``
        is still at its default, propagate ``cli_timeout`` into ``timeouts.normal``.
        """
        if self.cli_timeout != 1800.0 and self.timeouts.normal == 1800.0:
            self.timeouts.normal = self.cli_timeout
        return self


def resolve_timeout(config: AgentConfig, path: str) -> float:
    """Resolve timeout for execution path: 'normal', 'background', 'subagent'."""
    mapping = {
        "normal": config.timeouts.normal,
        "background": config.timeouts.background,
        "subagent": config.timeouts.subagent,
    }
    return mapping.get(path, config.cli_timeout)


def resolve_user_timezone(configured: str = "") -> ZoneInfo:
    """Resolve timezone: config value -> host system -> UTC.

    Returns a ``ZoneInfo`` instance. Invalid or empty *configured* values
    fall through to the host OS timezone, then to UTC as last resort.
    """
    trimmed = configured.strip()
    if trimmed:
        try:
            return ZoneInfo(trimmed)
        except (ZoneInfoNotFoundError, KeyError):
            logger.warning("Invalid user_timezone '%s', falling back to host/UTC", trimmed)

    # Try host system timezone via environment or OS-specific detection.
    import os
    import sys

    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        try:
            return ZoneInfo(tz_env)
        except (ZoneInfoNotFoundError, KeyError):
            pass

    detected = _detect_host_timezone() if sys.platform == "win32" else _detect_posix_timezone()
    return detected or _SAFE_UTC


def _detect_host_timezone() -> ZoneInfo | None:
    """Detect timezone on Windows via datetime."""
    import datetime

    local_tz = datetime.datetime.now(datetime.UTC).astimezone().tzinfo
    if local_tz is None:
        return None
    tz_name = getattr(local_tz, "key", None) or str(local_tz)
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return None


def _detect_posix_timezone() -> ZoneInfo | None:
    """Detect timezone on POSIX via /etc/localtime symlink."""
    localtime = Path("/etc/localtime")
    if not localtime.is_symlink():
        return None
    target = str(localtime.resolve())
    marker = "/zoneinfo/"
    idx = target.find(marker)
    if idx == -1:
        return None
    candidate = target[idx + len(marker) :]
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, KeyError):
        return None


CLAUDE_MODELS_ORDERED: tuple[str, ...] = ("haiku", "sonnet", "opus")
CLAUDE_MODELS: frozenset[str] = frozenset(CLAUDE_MODELS_ORDERED)

# "auto" is a Gemini-specific alias (Gemini CLI auto-selects the best model).
_GEMINI_ALIASES: frozenset[str] = frozenset({"auto", "pro", "flash", "flash-lite"})

_runtime_gemini: list[frozenset[str]] = [frozenset()]


class ModelRegistry:
    """Provider resolution for models.

    Claude models (haiku, sonnet, opus) are hardcoded.
    Gemini models are hardcoded (parsed from CLI at startup if available).
    Codex models are discovered dynamically at runtime.
    """

    @staticmethod
    def provider_for(model_id: str) -> str:
        """Return the provider for a model ID."""
        if model_id in CLAUDE_MODELS:
            return "claude"
        if (
            model_id in _GEMINI_ALIASES
            or model_id in _runtime_gemini[0]
            or model_id.startswith(("gemini-", "auto-gemini-"))
        ):
            return "gemini"
        # OpenCode uses provider/model format (e.g. "anthropic/claude-sonnet-4").
        if "/" in model_id:
            return "opencode"
        if model_id.startswith("http-"):
            return "opencode_http"
        return "codex"


def get_gemini_models() -> frozenset[str]:
    """Return dynamically discovered Gemini models (may be empty)."""
    return _runtime_gemini[0]


def set_gemini_models(models: frozenset[str]) -> None:
    """Set runtime Gemini models discovered from local Gemini CLI files.

    Refuses to overwrite with an empty set to prevent cache wipe.
    """
    if not models:
        return
    _runtime_gemini[0] = models


def reset_gemini_models() -> None:
    """Clear runtime Gemini models. For test teardown only."""
    _runtime_gemini[0] = frozenset()
