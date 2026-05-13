"""Shared formatting primitives for command response text."""

from __future__ import annotations

from klir.i18n import t

SEP = "\u2500\u2500\u2500"


def fmt(*blocks: str) -> str:
    """Join non-empty blocks with double newlines."""
    return "\n\n".join(b for b in blocks if b)


# -- Shared response texts (eliminate duplication between handlers.py / commands.py) --

# Known CLI error patterns -> user-friendly short explanation.
_AUTH_PATTERNS = (
    "401",
    "unauthorized",
    "authentication",
    "signing in again",
    "sign in again",
    "token has been",
)
_RATE_PATTERNS = ("429", "rate limit", "too many requests", "quota exceeded")
_CONTEXT_PATTERNS = ("context length", "token limit", "maximum context", "too long")


def classify_cli_error(raw: str) -> str | None:
    """Return a user-facing hint for known CLI error patterns, or None."""
    lower = raw.lower()
    if any(p in lower for p in _AUTH_PATTERNS):
        return t("session.error.auth")
    if any(p in lower for p in _RATE_PATTERNS):
        return t("session.error.rate_limit")
    if any(p in lower for p in _CONTEXT_PATTERNS):
        return t("session.error.context")
    return None


def session_error_text(model: str, cli_detail: str = "") -> str:
    """Build the error message shown to the user on CLI failure."""
    base = fmt(
        t("session.error.title"),
        SEP,
        t("session.error.body", model=model),
    )
    hint = classify_cli_error(cli_detail) if cli_detail else None
    if hint:
        return fmt(base, t("session.error.cause", hint=hint))
    if cli_detail:
        # Show first meaningful line, truncated.
        detail = cli_detail.strip().split("\n")[0][:200]
        return fmt(base, t("session.error.detail", detail=detail))
    return base


def timeout_error_text(model: str, timeout_seconds: float) -> str:
    """Build the error message shown when the CLI times out."""
    minutes = int(timeout_seconds / 60)
    return fmt(
        t("timeout.title"),
        SEP,
        t("timeout.body", model=model, minutes=minutes),
    )


def new_session_text(provider: str) -> str:
    """Build /new response for provider-local reset."""
    provider_label = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}.get(
        provider.lower(), provider
    )
    return fmt(
        t("session.reset.title"),
        SEP,
        t("session.reset.body", provider=provider_label),
    )


def stop_text(killed: bool, provider: str) -> str:
    """Build the /stop response."""
    body = t("stop.killed", provider=provider) if killed else t("stop.nothing")
    return fmt(t("stop.title"), SEP, body)


# -- Timeout messages --


def timeout_warning_text(remaining: float) -> str:
    """Warning text shown when a timeout is approaching."""
    if remaining >= 60:
        mins = int(remaining // 60)
        return t("timeout.warning_mins", mins=mins)
    secs = int(remaining)
    return t("timeout.warning_secs", secs=secs)


def timeout_extended_text(extension: float, remaining_ext: int) -> str:
    """Notification that the timeout was extended due to activity."""
    secs = int(extension)
    return t("timeout.extended", secs=secs, remaining=remaining_ext)


def timeout_result_text(elapsed: float, configured: float) -> str:
    """Error text when a CLI process hit its timeout."""
    return fmt(
        t("timeout.title"),
        SEP,
        t("timeout.result", elapsed=int(elapsed), limit=int(configured)),
    )


# -- Startup lifecycle messages --


def startup_notification_text(kind: str) -> str:
    """Notification text for startup events.

    Only ``first_start`` and ``system_reboot`` produce output.
    ``service_restart`` is silent (handled by the existing sentinel system).
    """
    if kind == "first_start":
        return fmt(t("startup.first_start.title"), SEP, t("startup.first_start.body"))
    if kind == "system_reboot":
        return fmt(t("startup.reboot.title"), SEP, t("startup.reboot.body"))
    return ""


# -- Auto-recovery messages --


def recovery_notification_text(
    kind: str,
    prompt_preview: str,
    session_name: str = "",
) -> str:
    """Notification that interrupted work is being recovered."""
    preview = prompt_preview[:80] + ("…" if len(prompt_preview) > 80 else "")
    if kind == "named_session":
        return fmt(
            t("recovery.named.title"),
            SEP,
            t("recovery.named.body", name=session_name, preview=preview),
        )
    return fmt(
        t("recovery.interrupted.title"),
        SEP,
        t("recovery.interrupted.body", preview=preview),
    )
