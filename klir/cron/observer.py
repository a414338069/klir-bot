"""In-process cron job scheduler: watches cron_jobs.json, schedules and executes jobs."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from cronsim import CronSim, CronSimError

from klir.cli.param_resolver import TaskOverrides
from klir.config import resolve_user_timezone
from klir.cron.alerts import format_failure_alert, should_alert
from klir.cron.backoff import compute_backoff_seconds, should_auto_disable
from klir.cron.manager import CronManager
from klir.cron.run_log import CronRunLogEntry, append_run_log, save_run_output
from klir.i18n import t
from klir.infra.base_task_observer import BaseTaskObserver
from klir.infra.file_watcher import FileWatcher
from klir.infra.task_runner import execute_in_task_folder
from klir.log_context import set_log_context
from klir.utils.quiet_hours import check_quiet_hour

if TYPE_CHECKING:
    from klir.cli.codex_cache import CodexModelCache
    from klir.config import AgentConfig
    from klir.cron.manager import CronJob
    from klir.infra.db import KlirDB
    from klir.workspace.paths import KlirPaths

logger = logging.getLogger(__name__)

# Callback: (job_title, result_text, status,
#            routing_chat_id, routing_topic_id, routing_transport)
CronResultCallback = Callable[
    [str, str, str, int | None, int | None, str | None],
    Awaitable[None],
]


@dataclass(slots=True)
class _ScheduledJob:
    """Scheduling payload for one cron entry."""

    id: str
    schedule: str
    instruction: str
    task_folder: str
    timezone: str


class CronObserver(BaseTaskObserver):
    """Watches cron_jobs.json and schedules jobs in-process.

    On start: reads all jobs, calculates next run times via cronsim,
    and schedules asyncio tasks. A background watcher polls the JSON
    file's mtime every 5 seconds; on change it reloads and reschedules.
    """

    def __init__(
        self,
        paths: KlirPaths,
        manager: CronManager,
        *,
        config: AgentConfig,
        codex_cache: CodexModelCache,
        db: KlirDB,
    ) -> None:
        super().__init__(paths, config, codex_cache)
        self._manager = manager
        self._db = db
        self._on_result: CronResultCallback | None = None
        self._scheduled: dict[str, asyncio.Task[None]] = {}
        self._backoff_until: dict[str, float] = {}
        self._reschedule_lock = asyncio.Lock()
        self._requested_reschedule_task: asyncio.Task[None] | None = None
        self._running = False
        self._watcher = FileWatcher(
            paths.cron_jobs_path,
            self._on_file_change,
        )

    def set_result_handler(self, handler: CronResultCallback) -> None:
        """Set callback for job results (called after each execution)."""
        self._on_result = handler

    async def start(self) -> None:
        """Start the observer: schedule all jobs and begin watching."""
        self._running = True
        await self._schedule_all()
        await self._watcher.start()
        logger.info("CronObserver started (%d jobs scheduled)", len(self._scheduled))

    async def stop(self) -> None:
        """Stop the observer: cancel all scheduled jobs and the watcher."""
        self._running = False
        await self._watcher.stop()
        request_task = self._requested_reschedule_task
        self._requested_reschedule_task = None
        if request_task is not None:
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
        tasks = list(self._scheduled.values())
        for task in tasks:
            task.cancel()
        self._scheduled.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("CronObserver stopped")

    def request_reschedule(self) -> None:
        """Queue a background reschedule request without blocking the caller."""
        if not self._running:
            return
        task = self._requested_reschedule_task
        if task is not None and not task.done():
            return
        self._requested_reschedule_task = asyncio.create_task(self._run_requested_reschedule())

    async def reschedule_now(self) -> None:
        """Reschedule all jobs immediately (used by interactive cron toggles)."""
        if not self._running:
            return
        await self._update_mtime()
        await self._reschedule_locked()

    async def _run_requested_reschedule(self) -> None:
        """Execute one queued reschedule request and log failures."""
        try:
            await self.reschedule_now()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background cron reschedule failed")
        finally:
            self._requested_reschedule_task = None

    # -- File watcher callback --

    async def _on_file_change(self) -> None:
        """Reload manager in a thread, then reschedule."""
        logger.info("File watcher detected cron_jobs.json change, rescheduling")
        await asyncio.to_thread(self._manager.reload)
        await self._reschedule_locked()

    # -- Scheduling --

    async def _schedule_all(self) -> None:
        """Schedule asyncio tasks for all enabled jobs."""
        await self._watcher.update_mtime()
        for job in self._manager.list_jobs():
            if job.enabled:
                self._schedule_job(
                    job.id,
                    job.schedule,
                    job.agent_instruction,
                    job.task_folder,
                    job.timezone,
                )

    async def _reschedule_all(self) -> None:
        """Cancel existing schedules, await their termination, then reschedule.

        Awaiting cancellation prevents a race where the old task (executing a
        subprocess via asyncio.to_thread) is not yet interrupted and runs
        concurrently with the newly created replacement task.
        """
        tasks = list(self._scheduled.values())
        for task in tasks:
            task.cancel()
        self._scheduled.clear()
        # Prune backoff entries for jobs that no longer exist.
        active_ids = {j.id for j in self._manager.list_jobs()}
        self._backoff_until = {k: v for k, v in self._backoff_until.items() if k in active_ids}
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._schedule_all()
        logger.info("Rescheduled %d jobs", len(self._scheduled))

    async def _reschedule_locked(self) -> None:
        """Serialize reschedules from watcher and interactive updates."""
        async with self._reschedule_lock:
            await self._reschedule_all()

    def _schedule_job(
        self,
        job_id: str,
        schedule: str,
        instruction: str,
        task_folder: str,
        job_timezone: str = "",
    ) -> None:
        """Calculate next run time and schedule an asyncio task.

        Uses the job's timezone (if set), then the global ``user_timezone``
        config, then the host OS timezone, and finally UTC as last resort.
        CronSim iterates in the resolved local timezone so that ``0 9 * * *``
        means 09:00 in the user's wall-clock time.
        """
        try:
            tz = resolve_user_timezone(job_timezone or self._config.user_timezone)
            now_local = datetime.now(tz)
            # CronSim works on time components; feed it the local time
            # so hour fields match the user's wall clock.
            now_naive = now_local.replace(tzinfo=None)
            it = CronSim(schedule, now_naive)
            next_naive: datetime = next(it)
            # Re-attach the timezone using fold=0 (prefer pre-DST interpretation
            # for ambiguous times).  For non-existent times (DST spring-forward
            # gap) the delay becomes negative; in that case advance to the next
            # cron slot so the job fires at the correct wall-clock time.
            next_aware = next_naive.replace(tzinfo=tz)  # fold=0 default
            delay = (next_aware - datetime.now(tz)).total_seconds()
            if delay < 0:
                next_naive = next(it)
                next_aware = next_naive.replace(tzinfo=tz)
                delay = (next_aware - datetime.now(tz)).total_seconds()
            delay = max(delay, 0)
            # Apply exponential backoff anchored to the time of the last failure.
            backoff_remaining = self._backoff_until.get(job_id, 0.0) - time.time()
            if backoff_remaining > 0:
                delay = max(delay, backoff_remaining)
            scheduled_job = _ScheduledJob(
                id=job_id,
                schedule=schedule,
                instruction=instruction,
                task_folder=task_folder,
                timezone=job_timezone,
            )
            task = asyncio.create_task(
                self._run_at(delay, scheduled_job),
            )
            self._scheduled[job_id] = task
            logger.debug(
                "Scheduled %s: next run %s (%s), delay %.0fs",
                job_id,
                next_naive.isoformat(),
                tz.key,
                delay,
            )
        except (CronSimError, StopIteration):
            logger.warning("Invalid cron expression for job %s: %s", job_id, schedule)
        except Exception:
            logger.exception("Failed to schedule job %s", job_id)

    async def _run_at(self, delay: float, scheduled_job: _ScheduledJob) -> None:
        """Wait for delay, execute the job, then reschedule for next occurrence."""
        try:
            await asyncio.sleep(delay)
            await self._execute_job(
                scheduled_job.id,
                scheduled_job.instruction,
                scheduled_job.task_folder,
            )
        except asyncio.CancelledError:
            logger.warning("Cron job %s cancelled during execution", scheduled_job.id)
            return
        except Exception:
            logger.exception("Cron job %s failed unexpectedly", scheduled_job.id)
        if self._running:
            self._schedule_job(
                scheduled_job.id,
                scheduled_job.schedule,
                scheduled_job.instruction,
                scheduled_job.task_folder,
                scheduled_job.timezone,
            )

    # -- Execution --

    def _build_log_entry(  # noqa: PLR0913
        self,
        *,
        job_id: str,
        run_id: str,
        status: str,
        elapsed_ms: int,
        delivery_status: str,
        delivery_error: str | None = None,
        output_path: object = None,
    ) -> CronRunLogEntry:
        """Build a run log entry, pulling provider/model from the job."""
        job = self._manager.get_job(job_id)
        return CronRunLogEntry(
            ts=time.time(),
            job_id=job_id,
            status=status,
            duration_ms=elapsed_ms,
            delivery_status=delivery_status,
            delivery_error=delivery_error,
            run_id=run_id,
            output_path=str(output_path) if output_path else None,
            provider=job.provider if job else None,
            model=job.model if job else None,
        )

    async def _deliver_result(  # noqa: PLR0913
        self,
        job_id: str,
        job_title: str,
        result_text: str,
        status: str,
        *,
        routing_chat_id: int | None,
        routing_topic_id: int | None,
        routing_transport: str | None,
    ) -> tuple[str, str | None]:
        """Send result to the external handler (e.g. Telegram).

        Uses *job_title* (computed at execution start) so delivery works even
        if the job was removed from the manager mid-execution.

        Returns (delivery_status, delivery_error).
        """
        if self._on_result:
            try:
                await self._on_result(
                    job_title,
                    result_text,
                    status,
                    routing_chat_id,
                    routing_topic_id,
                    routing_transport,
                )
            except Exception as exc:
                logger.exception("Error in cron result handler for job %s", job_id)
                return "error", str(exc)
            else:
                return "delivered", None
        return "skipped", None

    async def _execute_job(
        self,
        job_id: str,
        instruction: str,
        task_folder: str,
    ) -> None:
        """Spawn a fresh CLI session in the cron_task folder."""
        set_log_context(operation="cron")
        job = self._manager.get_job(job_id)
        job_title = job.title if job else job_id
        routing_chat_id, routing_topic_id, routing_transport = (
            (job.routing_chat_id, job.routing_topic_id, job.routing_transport)
            if job
            else (None, None, None)
        )

        if self._is_quiet_hours(job, job_title):
            return

        run_id = f"{int(time.time())}_{secrets.token_hex(4)}"
        state_dir = self._paths.cron_job_state_dir(job_id)

        logger.info("Cron job starting job=%s run_id=%s", job_title, run_id)
        t0 = time.monotonic()

        overrides = TaskOverrides(
            provider=job.provider if job else None,
            model=job.model if job else None,
            reasoning_effort=job.reasoning_effort if job else None,
            cli_parameters=job.cli_parameters if job else [],
        )

        result = await execute_in_task_folder(
            self,
            cron_tasks_dir=self._paths.cron_tasks_dir,
            task_folder=task_folder,
            instruction=instruction,
            overrides=overrides,
            dependency=job.dependency if job else None,
            task_id=job_id,
            task_label="Cron job",
            timeout_seconds=self._config.cli_timeout,
            extra_env={"KLIR_CRON_STATE_DIR": str(self._paths.cron_state_dir)},
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if result.status == "error:folder_missing":
            logger.error("Cron task folder missing: %s", task_folder)
            self._manager.record_error(
                job_id,
                error=result.status,
                duration_ms=elapsed_ms,
                delivery_status="skipped",
            )
            await append_run_log(
                self._db,
                self._build_log_entry(
                    job_id=job_id,
                    run_id=run_id,
                    status=result.status,
                    elapsed_ms=elapsed_ms,
                    delivery_status="skipped",
                ),
            )
            return

        if result.execution is None:
            logger.error("CLI not found for cron job %s", job_id)
            delivery_status, delivery_error = await self._deliver_result(
                job_id,
                job_title,
                result.result_text,
                result.status,
                routing_chat_id=routing_chat_id,
                routing_topic_id=routing_topic_id,
                routing_transport=routing_transport,
            )
            self._manager.record_error(
                job_id,
                error=result.status,
                duration_ms=elapsed_ms,
                delivery_status=delivery_status,
                delivery_error=delivery_error,
            )
            await append_run_log(
                self._db,
                self._build_log_entry(
                    job_id=job_id,
                    run_id=run_id,
                    status=result.status,
                    elapsed_ms=elapsed_ms,
                    delivery_status=delivery_status,
                    delivery_error=delivery_error,
                ),
            )
            return

        logger.info(
            "Cron job completed job=%s status=%s duration_ms=%d stdout=%d result=%d",
            job_title,
            result.status,
            elapsed_ms,
            len(result.execution.stdout),
            len(result.result_text),
        )

        # Save raw output before delivery so the path can go in the log entry.
        output_path = await save_run_output(
            state_dir,
            run_id=run_id,
            stdout=result.execution.stdout,
            stderr=result.execution.stderr,
        )

        # Deliver result BEFORE writing run-status to disk.  The file
        # write can trigger the file-watcher which reschedules (and
        # cancels) running tasks.  Delivering first guarantees the
        # Telegram message is sent even if the task is cancelled during
        # the subsequent file I/O.
        delivery_status, delivery_error = await self._deliver_result(
            job_id,
            job_title,
            result.result_text,
            result.status,
            routing_chat_id=routing_chat_id,
            routing_topic_id=routing_topic_id,
            routing_transport=routing_transport,
        )

        if result.status == "success":
            self._manager.record_success(
                job_id, duration_ms=elapsed_ms, delivery_status=delivery_status
            )
            self._backoff_until.pop(job_id, None)
        else:
            self._manager.record_error(
                job_id,
                error=result.status,
                duration_ms=elapsed_ms,
                delivery_status=delivery_status,
                delivery_error=delivery_error,
            )
            job_after = self._manager.get_job(job_id)
            if job_after:
                backoff = compute_backoff_seconds(job_after.consecutive_errors)
                if backoff > 0:
                    self._backoff_until[job_id] = time.time() + backoff
            # Auto-disable if the job has exceeded its max_retries threshold.
            if job_after and should_auto_disable(
                job_after.consecutive_errors, max_retries=job_after.max_retries
            ):
                disable_msg = t(
                    "cron.autodisabled",
                    count=job_after.consecutive_errors,
                    title=job_title,
                    error=job_after.last_error or result.status,
                )
                await self._deliver_result(
                    job_id,
                    job_title,
                    disable_msg,
                    "auto_disabled",
                    routing_chat_id=routing_chat_id,
                    routing_topic_id=routing_topic_id,
                    routing_transport=routing_transport,
                )
                self._manager.set_enabled(job_id, enabled=False)
                self._backoff_until.pop(job_id, None)
                logger.warning(
                    "Cron job auto-disabled after %d consecutive errors: %s",
                    job_after.consecutive_errors,
                    job_id,
                )
            # Send failure alert if threshold reached and cooldown elapsed.
            elif job_after and should_alert(
                consecutive_errors=job_after.consecutive_errors,
                last_alert_at=job_after.last_alert_at,
                alert_after=job_after.alert_after,
                cooldown_seconds=job_after.alert_cooldown_seconds,
            ):
                alert_text = format_failure_alert(
                    job_title,
                    job_after.consecutive_errors,
                    job_after.last_error or result.status,
                )
                await self._deliver_result(
                    job_id,
                    job_title,
                    alert_text,
                    "alert",
                    routing_chat_id=routing_chat_id,
                    routing_topic_id=routing_topic_id,
                    routing_transport=routing_transport,
                )
                self._manager.record_alert(job_id)
                logger.warning(
                    "Failure alert sent for job %s (%d consecutive errors)",
                    job_id,
                    job_after.consecutive_errors,
                )

        await append_run_log(
            self._db,
            self._build_log_entry(
                job_id=job_id,
                run_id=run_id,
                status=result.status,
                elapsed_ms=elapsed_ms,
                delivery_status=delivery_status,
                delivery_error=delivery_error,
                output_path=output_path,
            ),
        )

        # Refresh our mtime baseline so the file-watcher doesn't treat the
        # run-status write as a user-initiated change and trigger a full
        # reschedule of all other jobs.
        await self._watcher.update_mtime()

    def _is_quiet_hours(self, job: CronJob | None, job_title: str) -> bool:
        """Return True when the job must be skipped due to quiet-hour settings."""
        job_start = job.quiet_start if job else None
        job_end = job.quiet_end if job else None

        # Cron jobs only respect quiet hours explicitly set on the job itself.
        # Do NOT fall back to heartbeat quiet hours.
        if job_start is None and job_end is None:
            return False

        is_quiet, now_hour, tz = check_quiet_hour(
            quiet_start=job_start,
            quiet_end=job_end,
            user_timezone=self._config.user_timezone,
            global_quiet_start=0,
            global_quiet_end=0,
        )
        if not is_quiet:
            return False

        logger.debug(
            "Cron job skipped: quiet hours (%d:00 %s) job=%s",
            now_hour,
            tz.key,
            job_title,
        )
        return True

    async def _update_mtime(self) -> None:
        """Cache the current mtime of the jobs file."""
        await self._watcher.update_mtime()
