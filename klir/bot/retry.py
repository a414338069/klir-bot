"""Telegram API error classification and async retry with exponential backoff."""

from __future__ import annotations

import asyncio
import enum
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from aiogram.exceptions import (
    TelegramConflictError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from klir.config import ResilienceConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ErrorClass(enum.Enum):
    """Classification of Telegram API errors."""

    PERMANENT = "permanent"
    RECOVERABLE = "recoverable"
    RATE_LIMITED = "rate_limited"
    CONFLICT = "conflict"


def classify_error(exc: BaseException) -> ErrorClass:
    """Classify a Telegram or network error for retry decisions."""
    if isinstance(exc, TelegramConflictError):
        return ErrorClass.CONFLICT
    if isinstance(exc, TelegramRetryAfter):
        return ErrorClass.RATE_LIMITED
    if isinstance(exc, (TelegramServerError, TelegramNetworkError)):
        return ErrorClass.RECOVERABLE
    return ErrorClass.PERMANENT


def compute_backoff(
    *,
    attempt: int,
    base: float,
    maximum: float,
    jitter: bool,
) -> float:
    """Compute backoff delay: ``min(base * 2^attempt, maximum)`` with optional jitter."""
    delay: float = min(base * (2**attempt), maximum)
    if jitter:
        delay = float(random.uniform(0.0, delay))  # noqa: S311
    return delay


async def retry_async(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    config: ResilienceConfig,
    context: str = "",
    **kwargs: Any,
) -> T:
    """Call *fn* with retries for recoverable Telegram errors.

    Permanent errors and conflicts are re-raised immediately.
    Rate-limited errors honor the ``retry_after`` value from Telegram.
    """
    last_exc: BaseException | None = None

    for attempt in range(config.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            error_class = classify_error(exc)

            log_ctx = f" [{context}]" if context else ""

            if error_class is ErrorClass.PERMANENT:
                logger.warning(
                    "Permanent Telegram error%s: %s",
                    log_ctx,
                    exc,
                )
                raise

            if error_class is ErrorClass.CONFLICT:
                logger.exception(
                    "Telegram 409 conflict%s: another bot instance may be running",
                    log_ctx,
                )
                raise

            if attempt >= config.max_retries:
                logger.exception(
                    "Retries exhausted (%d/%d)%s",
                    attempt,
                    config.max_retries,
                    log_ctx,
                )
                raise

            if error_class is ErrorClass.RATE_LIMITED and isinstance(exc, TelegramRetryAfter):
                delay: float = exc.retry_after
                logger.warning(
                    "Rate limited%s, waiting %.1fs (attempt %d/%d)",
                    log_ctx,
                    delay,
                    attempt + 1,
                    config.max_retries,
                )
            else:
                delay = compute_backoff(
                    attempt=attempt,
                    base=config.base_backoff_seconds,
                    maximum=config.max_backoff_seconds,
                    jitter=config.jitter,
                )
                logger.warning(
                    "Recoverable error%s, retrying in %.2fs (attempt %d/%d): %s",
                    log_ctx,
                    delay,
                    attempt + 1,
                    config.max_retries,
                    exc,
                )

            await asyncio.sleep(delay)

    # Should not reach here, but satisfy type checker.
    raise last_exc  # type: ignore[misc]
