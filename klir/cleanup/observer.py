"""Cleanup observer: daily removal of old files from workspace directories."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from klir.config import resolve_user_timezone
from klir.cron.run_log import cleanup_old_runs, prune_run_outputs
from klir.infra.base_observer import BaseObserver

if TYPE_CHECKING:
    from klir.bot.chat_tracker import ChatTracker
    from klir.config import AgentConfig, CleanupConfig
    from klir.history.store import MessageHistory
    from klir.infra.db import KlirDB
    from klir.session.manager import SessionManager
    from klir.workspace.paths import KlirPaths

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 3600  # Re-check every hour whether it's time to run.


def _delete_old_files(directory: Path, max_age_days: int) -> int:
    """Delete files older than *max_age_days* from *directory*.

    Walks the directory tree recursively, removes old files, then prunes
    empty subdirectories so date-based folders (``YYYY-MM-DD/``) don't
    accumulate indefinitely.
    """
    if not directory.is_dir():
        return 0

    cutoff = time.time() - max_age_days * 86400
    deleted = 0
    for entry in directory.rglob("*"):
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                deleted += 1
        except OSError:
            logger.warning("Failed to delete %s", entry)

    # Prune empty subdirectories (bottom-up so nested empties are removed)
    for sub in sorted(directory.rglob("*"), reverse=True):
        if sub.is_dir():
            with contextlib.suppress(OSError):
                sub.rmdir()  # only succeeds if empty
    return deleted


class CleanupObserver(BaseObserver):
    """Runs daily file cleanup for telegram_files, output_to_user, and api_files.

    Follows the same lifecycle pattern as HeartbeatObserver:
    ``start()`` / ``stop()`` with an asyncio background task.
    """

    def __init__(
        self,
        config: AgentConfig,
        paths: KlirPaths,
        db: KlirDB,
        *,
        message_history: MessageHistory | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._paths = paths
        self._db = db
        self._message_history = message_history
        self._last_run_date: str = ""
        self._chat_tracker: ChatTracker | None = None
        self._session_manager: SessionManager | None = None

    def set_chat_tracker(self, tracker: ChatTracker) -> None:
        """Wire the chat tracker for retention-based cleanup."""
        self._chat_tracker = tracker

    def set_session_manager(self, manager: SessionManager) -> None:
        """Late-bind the session manager (avoids circular imports)."""
        self._session_manager = manager

    @property
    def _cfg(self) -> CleanupConfig:
        return self._config.cleanup

    async def start(self) -> None:
        """Start the cleanup background loop."""
        if not self._cfg.enabled:
            logger.info("File cleanup disabled in config")
            return
        await super().start()
        logger.info(
            "File cleanup started (telegram: %dd, output: %dd, api: %dd, "
            "chat_activity: %dd, hour: %d:00)",
            self._cfg.telegram_files_days,
            self._cfg.output_to_user_days,
            self._cfg.api_files_days,
            self._cfg.chat_activity_days,
            self._cfg.check_hour,
        )

    async def stop(self) -> None:
        """Stop the cleanup background loop."""
        await super().stop()
        logger.info("File cleanup stopped")

    async def _run(self) -> None:
        """Sleep -> check hour -> run if due -> repeat."""
        try:
            while self._running:
                await asyncio.sleep(_CHECK_INTERVAL)
                if not self._running or not self._cfg.enabled:
                    continue
                try:
                    await self._maybe_run()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Cleanup tick failed (continuing)")
        except asyncio.CancelledError:
            logger.debug("Cleanup loop cancelled")

    async def _maybe_run(self) -> None:
        """Run cleanup if the current hour matches and we haven't run today."""
        tz = resolve_user_timezone(self._config.user_timezone)
        now = datetime.now(tz)
        today = now.strftime("%Y-%m-%d")

        if now.hour != self._cfg.check_hour:
            return
        if self._last_run_date == today:
            return

        await self._execute()
        # Set AFTER successful execution so a transient error doesn't
        # permanently suppress cleanup for the rest of the day.
        self._last_run_date = today

    async def _execute(self) -> None:
        """Perform the actual cleanup in a thread to avoid blocking the loop."""
        targets = [
            (self._paths.telegram_files_dir, self._cfg.telegram_files_days),
            (self._paths.output_to_user_dir, self._cfg.output_to_user_days),
            (self._paths.api_files_dir, self._cfg.api_files_days),
        ]
        results, cron_deleted, _ = await asyncio.gather(
            asyncio.to_thread(_run_cleanup, targets),
            asyncio.to_thread(prune_run_outputs, self._paths.cron_state_dir),
            cleanup_old_runs(self._db),
        )

        # Prune inactive chat activity records from SQLite.
        activity_pruned = 0
        if self._chat_tracker:
            activity_pruned = await self._chat_tracker.prune_inactive(self._cfg.chat_activity_days)

        # Prune stale sessions from SQLite.
        session_pruned = 0
        if self._session_manager is not None:
            session_pruned = await self._session_manager.prune_stale(
                retention_days=self._cfg.session_retention_days
            )

        # Prune old message history records from SQLite.
        history_deleted = 0
        if self._message_history:
            history_deleted = await self._message_history.cleanup(self._cfg.history_retention_days)

        if any(results) or cron_deleted or activity_pruned or session_pruned or history_deleted:
            logger.info(
                "Cleanup complete: telegram=%d, output=%d, api=%d, "
                "cron_runs=%d, chat_activity=%d, sessions=%d, history=%d",
                results[0],
                results[1],
                results[2],
                cron_deleted,
                activity_pruned,
                session_pruned,
                history_deleted,
            )
        else:
            logger.debug("Cleanup: nothing to delete")


def _run_cleanup(targets: list[tuple[Path, int]]) -> list[int]:
    """Synchronous cleanup runner (called via ``asyncio.to_thread``)."""
    return [_delete_old_files(d, days) for d, days in targets]
