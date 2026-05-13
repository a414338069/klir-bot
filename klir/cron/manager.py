"""Cron job management: JSON-based persistence.

Jobs are stored in a JSON file. The CronObserver watches the file
for changes and schedules jobs in-process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from klir.infra.json_store import atomic_json_save, load_json

logger = logging.getLogger(__name__)


@dataclass
class CronJob:
    """A scheduled job definition."""

    id: str
    title: str
    description: str
    schedule: str
    task_folder: str
    agent_instruction: str
    enabled: bool = True
    timezone: str = ""
    created_at: str = ""

    # Per-task execution overrides
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    cli_parameters: list[str] = field(default_factory=list)

    # Error tracking
    consecutive_errors: int = 0
    last_error: str | None = None
    last_duration_ms: int | None = None
    delivery_status: str | None = None
    delivery_error: str | None = None

    # Retry/alert config
    max_retries: int = 3
    alert_after: int = 3
    alert_cooldown_seconds: int = 3600
    last_alert_at: str | None = None

    # Quiet hours (None = use global config defaults)
    quiet_start: int | None = None
    quiet_end: int | None = None

    # Optional dependency for sequential execution
    dependency: str | None = None

    # Routing: where results should be delivered (None = broadcast to all users)
    routing_chat_id: int | None = None
    routing_topic_id: int | None = None
    routing_transport: str | None = None  # reserved for future transports; "tg" = Telegram

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "schedule": self.schedule,
            "task_folder": self.task_folder,
            "agent_instruction": self.agent_instruction,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "cli_parameters": self.cli_parameters,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
            "last_duration_ms": self.last_duration_ms,
            "delivery_status": self.delivery_status,
            "delivery_error": self.delivery_error,
            "max_retries": self.max_retries,
            "alert_after": self.alert_after,
            "alert_cooldown_seconds": self.alert_cooldown_seconds,
            "last_alert_at": self.last_alert_at,
            "quiet_start": self.quiet_start,
            "quiet_end": self.quiet_end,
            "dependency": self.dependency,
            "routing_chat_id": self.routing_chat_id,
            "routing_topic_id": self.routing_topic_id,
            "routing_transport": self.routing_transport,
        }
        if self.timezone:
            result["timezone"] = self.timezone
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronJob:
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            schedule=data["schedule"],
            task_folder=data["task_folder"],
            agent_instruction=data["agent_instruction"],
            enabled=data.get("enabled", True),
            timezone=data.get("timezone", ""),
            created_at=data.get("created_at", ""),
            provider=data.get("provider"),
            model=data.get("model"),
            reasoning_effort=data.get("reasoning_effort"),
            cli_parameters=data.get("cli_parameters", []),
            consecutive_errors=data.get("consecutive_errors", 0),
            last_error=data.get("last_error"),
            last_duration_ms=data.get("last_duration_ms"),
            delivery_status=data.get("delivery_status"),
            delivery_error=data.get("delivery_error"),
            max_retries=data.get("max_retries", 3),
            alert_after=data.get("alert_after", 3),
            alert_cooldown_seconds=data.get("alert_cooldown_seconds", 3600),
            last_alert_at=data.get("last_alert_at"),
            quiet_start=data.get("quiet_start"),
            quiet_end=data.get("quiet_end"),
            dependency=data.get("dependency"),
            routing_chat_id=data.get("routing_chat_id"),
            routing_topic_id=data.get("routing_topic_id"),
            routing_transport=data.get("routing_transport"),
        )


class CronManager:
    """Manages cron jobs: JSON persistence.

    The CronObserver watches the JSON file for changes and handles
    scheduling. This class is responsible for data only.
    """

    def __init__(self, *, jobs_path: Path) -> None:
        self._jobs_path = jobs_path
        self._jobs: list[CronJob] = self._load()

    # -- CRUD --

    def add_job(self, job: CronJob) -> None:
        """Add a new job. Raises ValueError if ID already exists."""
        if any(j.id == job.id for j in self._jobs):
            msg = f"Job '{job.id}' already exists"
            raise ValueError(msg)
        self._jobs.append(job)
        self._save()
        logger.info("Cron job added: %s (%s)", job.id, job.schedule)

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID. Returns False if not found."""
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j.id != job_id]
        if len(self._jobs) == before:
            return False
        self._save()
        logger.info("Cron job removed: %s", job_id)
        return True

    def list_jobs(self) -> list[CronJob]:
        """Return all jobs."""
        return list(self._jobs)

    def get_job(self, job_id: str) -> CronJob | None:
        """Return a job by ID, or None."""
        return next((j for j in self._jobs if j.id == job_id), None)

    def set_enabled(self, job_id: str, *, enabled: bool) -> bool:
        """Set ``enabled`` for one job. Returns True if state changed."""
        job = self.get_job(job_id)
        if job is None:
            return False
        if job.enabled == enabled:
            return False
        job.enabled = enabled
        self._save()
        logger.info("Cron job %s: enabled=%s", job_id, enabled)
        return True

    def set_all_enabled(self, *, enabled: bool) -> int:
        """Set ``enabled`` for all jobs. Returns number of changed jobs."""
        changed = 0
        for job in self._jobs:
            if job.enabled != enabled:
                job.enabled = enabled
                changed += 1
        if changed:
            self._save()
            logger.info("Cron jobs bulk update: enabled=%s changed=%d", enabled, changed)
        return changed

    def record_success(self, job_id: str, *, duration_ms: int, delivery_status: str) -> None:
        """Reset consecutive errors and record successful run."""
        job = self.get_job(job_id)
        if job is None:
            return
        job.consecutive_errors = 0
        job.last_error = None
        job.last_duration_ms = duration_ms
        job.delivery_status = delivery_status
        job.delivery_error = None
        self._save()

    def record_error(
        self,
        job_id: str,
        *,
        error: str,
        duration_ms: int,
        delivery_status: str,
        delivery_error: str | None = None,
    ) -> None:
        """Increment consecutive errors and record failure."""
        job = self.get_job(job_id)
        if job is None:
            return
        job.consecutive_errors += 1
        job.last_error = error
        job.last_duration_ms = duration_ms
        job.delivery_status = delivery_status
        job.delivery_error = delivery_error
        self._save()

    def record_alert(self, job_id: str) -> None:
        """Record that a failure alert was sent for this job."""
        job = self.get_job(job_id)
        if job is None:
            return
        job.last_alert_at = datetime.now(UTC).isoformat()
        self._save()

    def reload(self) -> None:
        """Re-read jobs from disk (called by CronObserver on file change)."""
        self._jobs = self._load()

    # -- Persistence --

    def _load(self) -> list[CronJob]:
        """Load jobs from JSON file."""
        data = load_json(self._jobs_path)
        if data is None:
            return []
        try:
            jobs = [CronJob.from_dict(j) for j in data.get("jobs", [])]
        except (KeyError, TypeError):
            logger.warning("Corrupt cron jobs file: %s", self._jobs_path)
            return []
        for j in jobs:
            logger.debug("Job loaded id=%s title=%s enabled=%s", j.id, j.title, j.enabled)
        return jobs

    def _save(self) -> None:
        """Save jobs to JSON file atomically (temp write + rename)."""
        atomic_json_save(self._jobs_path, {"jobs": [j.to_dict() for j in self._jobs]})
