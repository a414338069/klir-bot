"""Periodic cleanup of expired thread/topic bindings."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from klir.infra.base_observer import BaseObserver

if TYPE_CHECKING:
    from klir.bot.topic import TopicNameCache
    from klir.config import AgentConfig

logger = logging.getLogger(__name__)


class BindingCleanupObserver(BaseObserver):
    """Periodically removes expired thread bindings from TopicNameCache.

    Follows the same lifecycle pattern as CleanupObserver:
    ``start()`` / ``stop()`` with an asyncio background task.
    """

    def __init__(self, config: AgentConfig, topic_cache: TopicNameCache) -> None:
        super().__init__()
        self._config = config
        self._topic_cache = topic_cache

    async def start(self) -> None:
        """Start the binding cleanup loop (no-op when disabled)."""
        if not self._config.thread_binding.enabled:
            logger.info("Thread binding lifecycle disabled in config")
            return
        await super().start()
        logger.info(
            "Binding cleanup started (idle=%dm, max_age=%dm, interval=%dm)",
            self._config.thread_binding.idle_timeout_minutes,
            self._config.thread_binding.max_age_minutes,
            self._config.thread_binding.cleanup_interval_minutes,
        )

    async def stop(self) -> None:
        """Stop the binding cleanup loop."""
        await super().stop()
        logger.debug("Binding cleanup stopped")

    async def _run(self) -> None:
        """Sleep -> cleanup -> repeat."""
        interval = self._config.thread_binding.cleanup_interval_minutes * 60
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    removed = self.run_cleanup()
                    if removed:
                        logger.info("Binding cleanup: removed %d expired bindings", removed)
                    else:
                        logger.debug("Binding cleanup: nothing to remove")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Binding cleanup tick failed (continuing)")
        except asyncio.CancelledError:
            logger.debug("Binding cleanup loop cancelled")

    def run_cleanup(self) -> int:
        """Execute one cleanup pass. Returns number of removed bindings."""
        cfg = self._config.thread_binding
        if not cfg.enabled:
            return 0
        expired = self._topic_cache.expired_bindings(cfg)
        if not expired:
            return 0
        return self._topic_cache.remove_bindings(expired)
