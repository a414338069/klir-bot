"""Failure alert logic with cooldown for cron jobs."""

from __future__ import annotations

from klir.i18n import t

DEFAULT_ALERT_AFTER = 3
DEFAULT_COOLDOWN_SECONDS = 3600


def should_alert(
    *,
    consecutive_errors: int,
    last_alert_at: str | None,
    alert_after: int = DEFAULT_ALERT_AFTER,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> bool:
    """Return True if a failure alert should be sent.

    An alert fires when consecutive_errors >= alert_after AND either no
    prior alert was sent or the cooldown period has elapsed since the last one.
    """
    if consecutive_errors < alert_after:
        return False
    if last_alert_at is None:
        return True
    try:
        from datetime import UTC, datetime

        last = datetime.fromisoformat(last_alert_at)
        now = datetime.now(UTC)
        elapsed = (now - last).total_seconds()
    except (ValueError, TypeError):
        return True
    else:
        return elapsed >= cooldown_seconds


def format_failure_alert(
    job_title: str,
    consecutive_errors: int,
    last_error: str,
) -> str:
    """Format a human-readable failure alert message."""
    return t(
        "cron.alert.failure",
        title=job_title,
        count=consecutive_errors,
        error=last_error,
    )
