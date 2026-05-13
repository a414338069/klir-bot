"""Transient error detection and exponential backoff for cron jobs."""

from __future__ import annotations

import re

DEFAULT_MAX_RETRIES = 3

_BACKOFF_SCHEDULE_SECONDS: list[float] = [30.0, 60.0, 300.0, 900.0, 3600.0]

_TRANSIENT_PATTERN = re.compile(
    r"rate.?limit|overloaded|network|timeout|timed.?out"
    r"|\b503\b|\b502\b|\b500\b|connection.?refused|temporarily.?unavailable",
    re.IGNORECASE,
)


def is_transient_error(error: str) -> bool:
    """Return True if the error message matches a known transient error pattern."""
    return bool(_TRANSIENT_PATTERN.search(error))


def compute_backoff_seconds(consecutive_errors: int) -> float:
    """Return backoff delay in seconds based on number of consecutive errors.

    Schedule: 0 errors→0s, 1→30s, 2→60s, 3→300s, 4→900s, 5+→3600s.
    """
    if consecutive_errors <= 0:
        return 0.0
    idx = min(consecutive_errors - 1, len(_BACKOFF_SCHEDULE_SECONDS) - 1)
    return _BACKOFF_SCHEDULE_SECONDS[idx]


def should_auto_disable(
    consecutive_errors: int,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> bool:
    """Return True if the job should be auto-disabled after too many consecutive errors."""
    return consecutive_errors >= max_retries
