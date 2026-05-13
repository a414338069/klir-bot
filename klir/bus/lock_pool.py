"""Unified per-session lock pool shared by all transports and the message bus."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_MAX_LOCKS = 1000


class LockPool:
    """Single lock pool for all message sources.

    Replaces ``SequentialMiddleware._locks``, ``ApiServer._locks``, and
    the ad-hoc ``bot.sequential.get_lock()`` calls in result delivery.
    """

    # Lock key type: (chat_id,), (chat_id, topic_id), or
    # (chat_id, topic_id, user_id).  Kept as a broad tuple to avoid
    # mypy headaches with union narrowing.
    _LockKey = tuple[int | None, ...]

    def __init__(self, *, max_locks: int = _MAX_LOCKS) -> None:
        self._locks: dict[LockPool._LockKey, asyncio.Lock] = {}
        self._max = max_locks

    def get(self, key: _LockKey | int) -> asyncio.Lock:
        """Return the lock for *key*, creating one if needed.

        Accepts a ``(chat_id, topic_id)`` tuple, a
        ``(chat_id, topic_id, user_id)`` tuple, or a plain ``chat_id``
        integer for backward compatibility.
        """
        k = self._normalize(key)
        if k not in self._locks:
            self._evict_if_needed()
            self._locks[k] = asyncio.Lock()
        return self._locks[k]

    def is_locked(self, key: _LockKey | int) -> bool:
        """Return True if the lock for *key* is currently held."""
        lock = self._locks.get(self._normalize(key))
        return lock.locked() if lock else False

    def any_locked_for_chat(self, chat_id: int) -> bool:
        """Return True if any topic-scoped lock for *chat_id* is held."""
        return any(lock.locked() for k, lock in self._locks.items() if k[0] == chat_id)

    def __len__(self) -> int:
        return len(self._locks)

    # -- Internal helpers ------------------------------------------------------

    @staticmethod
    def _normalize(key: _LockKey | int) -> _LockKey:
        if isinstance(key, int):
            return (key, None, None)
        # Pad 2-tuples to 3-tuples for consistent lookup
        if len(key) == 2:
            return (*key, None)
        return key

    def _evict_if_needed(self) -> None:
        if len(self._locks) < self._max:
            return
        idle = [k for k, v in self._locks.items() if not v.locked()]
        for k in idle[: max(1, len(idle) // 2)]:
            del self._locks[k]
