"""Per-run logging for cron jobs: SQLite-backed run index and disk-based output capture."""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klir.infra.db import KlirDB

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CronRunLogEntry:
    """A single log entry recording the outcome of one cron job execution."""

    ts: float
    job_id: str
    action: str = "finished"
    status: str | None = None
    error: str | None = None
    summary: str | None = None
    duration_ms: int | None = None
    delivery_status: str | None = None
    delivery_error: str | None = None
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    run_id: str | None = None
    output_path: str | None = None

    def to_json_line(self) -> str:
        """Serialize to compact JSON (no trailing newline), omitting None fields."""
        data = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(data, separators=(",", ":"))


# ── SQLite operations ────────────────────────────────────────────────

_INSERT_SQL = """\
INSERT INTO cron_runs (id, ts, job_id, status, error, summary, duration_ms,
                       delivery_status, delivery_error, provider, model,
                       input_tokens, output_tokens, output_path)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


async def append_run_log(db: KlirDB, entry: CronRunLogEntry) -> None:
    """Insert a run log entry into the cron_runs table."""
    row_id = entry.run_id or uuid.uuid4().hex
    await db.execute(
        _INSERT_SQL,
        (
            row_id,
            entry.ts,
            entry.job_id,
            entry.status,
            entry.error,
            entry.summary,
            entry.duration_ms,
            entry.delivery_status,
            entry.delivery_error,
            entry.provider,
            entry.model,
            entry.input_tokens,
            entry.output_tokens,
            entry.output_path,
        ),
    )


@dataclass(frozen=True, slots=True)
class RunLogPage:
    """A paginated view of run log entries."""

    entries: list[CronRunLogEntry]
    total: int
    offset: int
    limit: int
    has_more: bool


async def read_run_log_page(  # noqa: PLR0913
    db: KlirDB,
    *,
    job_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    status_filter: str = "all",
    sort_dir: str = "desc",
) -> RunLogPage:
    """Read paginated run log entries from SQLite with optional filtering."""
    conditions: list[str] = []
    params: list[object] = []

    if job_id is not None:
        conditions.append("job_id = ?")
        params.append(job_id)
    if status_filter != "all":
        conditions.append("status = ?")
        params.append(status_filter)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    direction = "DESC" if sort_dir == "desc" else "ASC"

    count_sql = "SELECT COUNT(*) AS cnt FROM cron_runs" + where  # noqa: S608
    count_row = await db.fetch_one(count_sql, tuple(params))
    total = int(count_row["cnt"]) if count_row else 0  # type: ignore[call-overload]

    query_sql = (
        "SELECT * FROM cron_runs"  # noqa: S608
        + where
        + " ORDER BY ts "
        + direction
        + " LIMIT ? OFFSET ?"
    )
    rows = await db.fetch_all(query_sql, (*params, limit, offset))

    entries = [_row_to_entry(r) for r in rows]
    return RunLogPage(
        entries=entries,
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
    )


def _row_to_entry(row: dict[str, object]) -> CronRunLogEntry:
    """Convert a SQLite row dict to a CronRunLogEntry."""
    valid_keys = {f.name for f in fields(CronRunLogEntry)}
    # Map 'id' column back to 'run_id' field
    data: dict[str, object] = {}
    for k, v in row.items():
        if k == "id":
            data["run_id"] = v
        elif k in valid_keys:
            data[k] = v
    return CronRunLogEntry(**data)  # type: ignore[arg-type]


# ── Retention cleanup ────────────────────────────────────────────────


async def cleanup_old_runs(db: KlirDB, *, max_age_days: int = 30) -> None:
    """Delete cron_runs rows older than max_age_days."""
    cutoff = _time.time() - max_age_days * 86400
    await db.execute("DELETE FROM cron_runs WHERE ts < ?", (cutoff,))


# ── Disk-based output files ──────────────────────────────


async def save_run_output(
    state_dir: Path,
    *,
    run_id: str,
    stdout: bytes,
    stderr: bytes,
) -> Path | None:
    """Save full stdout/stderr to a per-run output file. Returns path or None if empty."""
    if not stdout and not stderr:
        return None
    return await asyncio.to_thread(_write_output, state_dir, run_id, stdout, stderr)


def _write_output(state_dir: Path, run_id: str, stdout: bytes, stderr: bytes) -> Path:
    """Write output to {state_dir}/runs/{run_id}.log."""
    runs_dir = state_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_path = runs_dir / f"{run_id}.log"
    with output_path.open("wb") as f:
        if stdout:
            f.write(b"=== STDOUT ===\n")
            f.write(stdout)
        if stderr:
            f.write(b"=== STDERR ===\n")
            f.write(stderr)
    return output_path


def prune_run_outputs(state_dir: Path, *, max_age_seconds: float = 30 * 86400) -> int:
    """Delete run output files older than max_age_seconds.

    Call periodically (e.g. from the daily cleanup observer) to prevent
    orphaned .log files accumulating after their SQLite entries are pruned.
    Returns the number of files deleted.
    """
    runs_dir = state_dir / "runs"
    if not runs_dir.exists():
        return 0
    cutoff = _time.time() - max_age_seconds
    deleted = 0
    for log_file in runs_dir.glob("*.log"):
        try:
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted
