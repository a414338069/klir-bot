"""Lightweight chat activity tracker for /where visibility.

Tracks group joins/leaves (via ``my_chat_member`` events), rejected
group access attempts (via AuthMiddleware callback), and private chat
activity.  Persists to the ``chat_activity`` table in ``klir.db``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from klir.infra.db import KlirDB

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ts_to_iso(ts: float) -> str:
    """Convert a UNIX timestamp to ISO-8601 string."""
    return datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="seconds")


def _iso_to_ts(iso: str) -> float:
    """Convert an ISO-8601 string to UNIX timestamp."""
    return datetime.fromisoformat(iso).timestamp()


@dataclass
class ChatRecord:
    """A single tracked chat/group."""

    chat_id: int
    chat_type: str = "private"  # "private" | "group" | "supergroup"
    title: str = ""
    first_seen: str = field(default_factory=_now_iso)
    last_seen: str = field(default_factory=_now_iso)
    status: str = "active"  # "active" | "left" | "kicked" | "auto_left"
    allowed: bool = True
    rejected_count: int = 0


class ChatTracker:
    """In-memory tracker backed by the ``chat_activity`` SQLite table."""

    def __init__(self, db: KlirDB) -> None:
        self._db = db
        self._records: dict[int, ChatRecord] = {}

    # -- Factory ---------------------------------------------------------------

    @classmethod
    async def create(cls, db: KlirDB) -> ChatTracker:
        """Create a tracker, loading from SQLite."""
        tracker = cls(db)
        await tracker._load()
        return tracker

    # -- Public API -----------------------------------------------------------

    async def record_join(
        self,
        chat_id: int,
        chat_type: str,
        title: str,
        *,
        allowed: bool,
    ) -> None:
        """Record a group join from ``my_chat_member``."""
        existing = self._records.get(chat_id)
        now = _now_iso()
        if existing:
            existing.chat_type = chat_type
            existing.title = title or existing.title
            existing.last_seen = now
            existing.status = "active"
            existing.allowed = allowed
        else:
            self._records[chat_id] = ChatRecord(
                chat_id=chat_id,
                chat_type=chat_type,
                title=title,
                first_seen=now,
                last_seen=now,
                status="active",
                allowed=allowed,
            )
        await self._upsert(self._records[chat_id])

    async def record_leave(self, chat_id: int, status: str = "left") -> None:
        """Record a group leave/kick from ``my_chat_member`` or ``/leave``."""
        existing = self._records.get(chat_id)
        now = _now_iso()
        if existing:
            existing.status = status
            existing.last_seen = now
        else:
            self._records[chat_id] = ChatRecord(
                chat_id=chat_id,
                first_seen=now,
                last_seen=now,
                status=status,
            )
        await self._upsert(self._records[chat_id])

    async def record_rejected(self, chat_id: int, chat_type: str, title: str) -> None:
        """Record a rejected group message from AuthMiddleware."""
        existing = self._records.get(chat_id)
        now = _now_iso()
        if existing:
            existing.rejected_count += 1
            existing.last_seen = now
            existing.title = title or existing.title
        else:
            self._records[chat_id] = ChatRecord(
                chat_id=chat_id,
                chat_type=chat_type,
                title=title,
                first_seen=now,
                last_seen=now,
                status="rejected",
                allowed=False,
                rejected_count=1,
            )
        await self._upsert(self._records[chat_id])

    def get_all(self) -> list[ChatRecord]:
        """Return all records sorted by last_seen (newest first)."""
        return sorted(self._records.values(), key=lambda r: r.last_seen, reverse=True)

    async def prune_inactive(self, max_age_days: int) -> int:
        """Delete records with ``last_seen`` older than *max_age_days*.

        Returns the number of records pruned.
        """
        cutoff = datetime.now(UTC).timestamp() - max_age_days * 86400
        pruned = [cid for cid, rec in self._records.items() if _iso_to_ts(rec.last_seen) < cutoff]
        if not pruned:
            return 0

        for cid in pruned:
            del self._records[cid]
        await self._db.execute("DELETE FROM chat_activity WHERE last_seen < ?", (cutoff,))
        logger.info("Pruned %d inactive chat activity record(s)", len(pruned))
        return len(pruned)

    # -- Persistence ----------------------------------------------------------

    async def _load(self) -> None:
        """Load all records from the ``chat_activity`` table."""
        rows = await self._db.fetch_all(
            "SELECT chat_id, title, type, first_seen, last_seen, status, metadata "
            "FROM chat_activity ORDER BY last_seen DESC"
        )
        for row in rows:
            meta = _parse_metadata(row.get("metadata"))
            chat_id = int(str(row["chat_id"]))
            self._records[chat_id] = ChatRecord(
                chat_id=chat_id,
                chat_type=str(row.get("type") or "private"),
                title=str(row.get("title") or ""),
                first_seen=_ts_to_iso(float(str(row["first_seen"]))),
                last_seen=_ts_to_iso(float(str(row["last_seen"]))),
                status=str(row.get("status") or "active"),
                allowed=bool(meta.get("allowed", True)),
                rejected_count=int(meta.get("rejected_count", 0)),
            )

    async def _upsert(self, rec: ChatRecord) -> None:
        """Insert or replace a single record in the ``chat_activity`` table."""
        meta = json.dumps({"allowed": rec.allowed, "rejected_count": rec.rejected_count})
        await self._db.execute(
            "INSERT INTO chat_activity (chat_id, title, type, first_seen, last_seen, status, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "title=excluded.title, type=excluded.type, "
            "last_seen=excluded.last_seen, "
            "status=excluded.status, metadata=excluded.metadata",
            (
                rec.chat_id,
                rec.title,
                rec.chat_type,
                _iso_to_ts(rec.first_seen),
                _iso_to_ts(rec.last_seen),
                rec.status,
                meta,
            ),
        )


def _parse_metadata(raw: object) -> dict[str, Any]:
    """Parse a metadata JSON string, returning {} on failure."""
    if not raw or not isinstance(raw, str):
        return {}
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
