"""Detect Telegram 409 conflict errors (another bot instance polling)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramConflictError

logger = logging.getLogger(__name__)


class ConflictDetector:
    """Track and react to Telegram 409 conflict errors.

    When ``getUpdates`` returns 409, another bot instance is polling with
    the same token.  This detector counts occurrences and optionally calls
    an async callback (e.g. to trigger a clean shutdown).
    """

    def __init__(
        self,
        on_conflict: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._on_conflict = on_conflict
        self._count: int = 0

    @property
    def conflict_detected(self) -> bool:
        return self._count > 0

    @property
    def conflict_count(self) -> int:
        return self._count

    def record(self, exc: BaseException) -> None:
        """Synchronously record a conflict error."""
        if not isinstance(exc, TelegramConflictError):
            return
        self._count += 1
        logger.error(
            "Telegram 409 conflict (#%d): another instance is likely polling with the same token",
            self._count,
        )

    async def record_async(self, exc: BaseException) -> None:
        """Record a conflict error and fire the async callback if set."""
        if not isinstance(exc, TelegramConflictError):
            return
        self.record(exc)
        if self._on_conflict is not None:
            await self._on_conflict()

    def reset(self) -> None:
        """Reset conflict state."""
        self._count = 0
