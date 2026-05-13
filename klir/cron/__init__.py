"""Cron job management: JSON storage + in-process scheduling."""

from klir.cron.alerts import format_failure_alert, should_alert
from klir.cron.backoff import compute_backoff_seconds, is_transient_error, should_auto_disable
from klir.cron.manager import CronJob, CronManager
from klir.cron.observer import CronObserver
from klir.cron.run_log import (
    CronRunLogEntry,
    RunLogPage,
    append_run_log,
    cleanup_old_runs,
    prune_run_outputs,
    read_run_log_page,
    save_run_output,
)

__all__ = [
    "CronJob",
    "CronManager",
    "CronObserver",
    "CronRunLogEntry",
    "RunLogPage",
    "append_run_log",
    "cleanup_old_runs",
    "compute_backoff_seconds",
    "format_failure_alert",
    "is_transient_error",
    "prune_run_outputs",
    "read_run_log_page",
    "save_run_output",
    "should_alert",
    "should_auto_disable",
]
