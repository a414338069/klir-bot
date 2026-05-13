"""Heartbeat observer: periodic background agent turns in the main session."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from klir.infra.base_observer import BaseObserver
from klir.log_context import set_log_context
from klir.utils.quiet_hours import check_quiet_hour

if TYPE_CHECKING:
    from klir.config import AgentConfig, HeartbeatConfig, HeartbeatGroupTarget

logger = logging.getLogger(__name__)

_CHAT_VALID_TTL = 3600.0

# Callback signature: (chat_id, alert_text, topic_id)
HeartbeatResultCallback = Callable[[int, str, int | None], Awaitable[None]]


class HeartbeatObserver(BaseObserver):
    """Sends periodic heartbeat prompts through the main session.

    Follows the CronObserver lifecycle pattern: start/stop with an asyncio
    background task. Results are delivered via a callback set by
    ``set_result_handler``.
    """

    def __init__(self, config: AgentConfig) -> None:
        super().__init__()
        self._config = config
        self._on_result: HeartbeatResultCallback | None = None
        self._handle_heartbeat: (
            Callable[[int, int | None, str | None], Awaitable[str | None]] | None
        ) = None
        self._is_chat_busy: Callable[[int], bool] | None = None
        self._stale_cleanup: Callable[[], Awaitable[int]] | None = None
        self._chat_valid_cache: dict[int, float] = {}
        self._validate_chat: Callable[[int], Awaitable[bool]] | None = None
        self._target_tasks: list[asyncio.Task[None]] = []

    @property
    def _hb(self) -> HeartbeatConfig:
        return self._config.heartbeat

    def set_result_handler(self, handler: HeartbeatResultCallback) -> None:
        """Set callback for delivering alert messages to the user."""
        self._on_result = handler

    def set_heartbeat_handler(
        self,
        handler: Callable[[int, int | None, str | None], Awaitable[str | None]],
    ) -> None:
        """Set the function that executes a heartbeat turn.

        Handler signature: (chat_id, topic_id, prompt_override) -> alert_text | None
        """
        self._handle_heartbeat = handler

    def set_busy_check(self, check: Callable[[int], bool]) -> None:
        """Set the function that checks if a chat has active CLI processes."""
        self._is_chat_busy = check

    def set_stale_cleanup(self, cleanup: Callable[[], Awaitable[int]]) -> None:
        """Set the function that kills stale CLI processes."""
        self._stale_cleanup = cleanup

    def set_chat_validator(self, validator: Callable[[int], Awaitable[bool]]) -> None:
        """Set the function that validates chat reachability."""
        self._validate_chat = validator

    async def _is_chat_reachable(self, chat_id: int) -> bool:
        """Check if a chat is reachable, with TTL cache."""
        if self._validate_chat is None:
            return True
        now = time.time()
        cached_at = self._chat_valid_cache.get(chat_id)
        if cached_at is not None and (now - cached_at) < _CHAT_VALID_TTL:
            return True
        try:
            ok = await self._validate_chat(chat_id)
        except Exception:
            logger.debug("Chat validation failed for %d", chat_id)
            ok = False
        if ok:
            self._chat_valid_cache[chat_id] = now
        else:
            self._chat_valid_cache.pop(chat_id, None)
        return ok

    async def start(self) -> None:
        """Start the heartbeat background loop."""
        if not self._hb.enabled:
            logger.info("Heartbeat disabled in config")
            return
        if self._handle_heartbeat is None:
            logger.error("Heartbeat handler not set, cannot start")
            return
        await super().start()
        self._start_target_tasks()
        n_targets = len(self._hb.group_targets)
        logger.info(
            "Heartbeat started (every %dm, quiet %d:00-%d:00, %d target(s))",
            self._hb.interval_minutes,
            self._hb.quiet_start,
            self._hb.quiet_end,
            n_targets,
        )

    def _start_target_tasks(self) -> None:
        """Create independent tasks for each enabled group target."""
        for target in self._hb.group_targets:
            if not target.enabled:
                continue
            task = asyncio.create_task(self._run_target(target))
            task.add_done_callback(_log_task_crash)
            self._target_tasks.append(task)

    async def stop(self) -> None:
        """Stop the heartbeat background loop and all target tasks."""
        for task in self._target_tasks:
            task.cancel()
        if self._target_tasks:
            await asyncio.gather(*self._target_tasks, return_exceptions=True)
            self._target_tasks.clear()
        await super().stop()
        logger.info("Heartbeat stopped")

    async def _run(self) -> None:
        """Sleep -> check -> execute -> repeat."""
        last_wall = time.time()
        try:
            while self._running:
                interval = self._hb.interval_minutes * 60
                await asyncio.sleep(interval)
                if not self._running or not self._hb.enabled:
                    continue

                now_wall = time.time()
                wall_elapsed = now_wall - last_wall
                if wall_elapsed > interval * 2:
                    logger.warning(
                        "Wall-clock gap: %.0fs (expected ~%ds) -- system likely suspended",
                        wall_elapsed,
                        interval,
                    )
                last_wall = now_wall

                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Heartbeat tick failed (continuing)")
        except asyncio.CancelledError:
            logger.debug("Heartbeat loop cancelled")

    async def _tick(self) -> None:
        """Run one heartbeat cycle for all allowed users."""
        if self._stale_cleanup:
            try:
                killed = await self._stale_cleanup()
                if killed:
                    logger.info("Cleaned up %d stale process(es)", killed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stale process cleanup failed")

        is_quiet, now_hour, tz = check_quiet_hour(
            quiet_start=self._hb.quiet_start,
            quiet_end=self._hb.quiet_end,
            user_timezone=self._config.user_timezone,
            global_quiet_start=self._hb.quiet_start,
            global_quiet_end=self._hb.quiet_end,
        )
        if is_quiet:
            logger.debug(
                "Heartbeat skipped: quiet hours (%d:00 %s)",
                now_hour,
                tz.key,
            )
            return

        logger.debug(
            "Heartbeat tick: checking %d chat(s)",
            len(self._config.allowed_user_ids),
        )
        for chat_id in self._config.allowed_user_ids:
            await self._run_for_chat(chat_id)

    async def _run_for_chat(self, chat_id: int) -> None:
        """Execute a single heartbeat for one chat."""
        set_log_context(operation="hb", chat_id=chat_id)

        if self._is_chat_busy and self._is_chat_busy(chat_id):
            logger.debug("Heartbeat skipped: chat is busy")
            return

        if self._handle_heartbeat is None:
            return

        try:
            alert_text = await self._handle_heartbeat(chat_id, None, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Heartbeat execution error")
            return

        if alert_text is None:
            return

        if self._on_result:
            try:
                await self._on_result(chat_id, alert_text, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Heartbeat result delivery error")

    # -- Group target methods -----------------------------------------------

    async def _run_target(self, target: HeartbeatGroupTarget) -> None:
        """Independent loop for a single group target."""
        chat_id = target.chat_id
        topic_id = target.topic_id
        try:
            while True:
                if not self._running:
                    return
                t = self._find_target(chat_id, topic_id)
                if t is None or not t.enabled:
                    logger.debug(
                        "Target %d/%s removed or disabled, stopping",
                        chat_id,
                        topic_id,
                    )
                    return
                interval = t.interval_minutes * 60
                await asyncio.sleep(interval)
                if not self._running:
                    return  # type: ignore[unreachable]
                try:
                    await self._tick_target(t)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Target tick failed chat=%d topic=%s",
                        t.chat_id,
                        t.topic_id,
                    )
        except asyncio.CancelledError:
            logger.debug(
                "Target loop cancelled chat=%d topic=%s",
                chat_id,
                topic_id,
            )

    def _find_target(self, chat_id: int, topic_id: int | None) -> HeartbeatGroupTarget | None:
        """Re-read config to find a target by (chat_id, topic_id)."""
        for t in self._hb.group_targets:
            if t.chat_id == chat_id and t.topic_id == topic_id:
                return t
        return None

    async def _tick_target(self, target: HeartbeatGroupTarget) -> None:
        """Run one heartbeat cycle for a group target."""
        is_quiet, now_hour, tz = check_quiet_hour(
            quiet_start=target.quiet_start,
            quiet_end=target.quiet_end,
            user_timezone=self._config.user_timezone,
            global_quiet_start=self._hb.quiet_start,
            global_quiet_end=self._hb.quiet_end,
        )
        if is_quiet:
            logger.debug(
                "Target heartbeat skipped: quiet hours (%d:00 %s) chat=%d topic=%s",
                now_hour,
                tz.key,
                target.chat_id,
                target.topic_id,
            )
            return

        chat_id = target.chat_id
        topic_id = target.topic_id
        reachable = await self._is_chat_reachable(chat_id)
        if not reachable:
            logger.warning(
                "Target chat %d unreachable, trying fallback",
                chat_id,
            )
            fallback = self._config.allowed_user_ids
            if not fallback:
                logger.warning("No fallback chat available, skipping")
                return
            chat_id = fallback[0]
            topic_id = None  # fallback to private chat, no topic
            if not await self._is_chat_reachable(chat_id):
                logger.warning(
                    "Fallback chat %d also unreachable, skipping",
                    chat_id,
                )
                return

        await self._run_for_target(chat_id, topic_id, target.prompt)

    async def _run_for_target(
        self,
        chat_id: int,
        topic_id: int | None,
        prompt: str,
    ) -> None:
        """Execute a heartbeat for a group target."""
        set_log_context(operation="hb-group", chat_id=chat_id)

        if self._is_chat_busy and self._is_chat_busy(chat_id):
            logger.debug("Target heartbeat skipped: chat is busy")
            return

        if self._handle_heartbeat is None:
            return

        try:
            alert_text = await self._handle_heartbeat(chat_id, topic_id, prompt)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Target heartbeat execution error")
            return

        if alert_text is None:
            return

        if self._on_result:
            try:
                await self._on_result(chat_id, alert_text, topic_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Target heartbeat result delivery error")

    async def reconcile_target_tasks(self) -> None:
        """Cancel all target tasks and restart enabled ones."""
        for task in self._target_tasks:
            task.cancel()
        if self._target_tasks:
            await asyncio.gather(*self._target_tasks, return_exceptions=True)
        self._target_tasks.clear()
        if self._running:
            self._start_target_tasks()
            logger.info(
                "Reconciled target tasks: %d active",
                len(self._target_tasks),
            )


def _log_task_crash(task: asyncio.Task[None]) -> None:
    """Log if a target task crashes unexpectedly."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Heartbeat target task crashed: %s", exc)
