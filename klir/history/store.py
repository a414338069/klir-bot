"""Message history store: records inbound/outbound messages in SQLite."""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klir.bus.envelope import Envelope
    from klir.infra.db import KlirDB

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ResponseRecord:
    """Parameters for recording a normal conversation response."""

    chat_id: int
    text: str
    topic_id: int | None = None
    provider: str = ""
    model: str = ""
    session_id: str = ""
    cost_usd: float = 0.0
    tokens: int = 0
    elapsed_seconds: float = 0.0
    is_error: bool = False


class MessageHistory:
    """Records and queries message history backed by the ``messages`` table in KlirDB."""

    def __init__(self, db: KlirDB) -> None:
        self._db = db

    async def record_inbound(
        self,
        *,
        chat_id: int,
        topic_id: int | None = None,
        text: str,
        provider: str = "",
        model: str = "",
    ) -> str:
        """Record an inbound (user) message. Returns the message ID."""
        msg_id = secrets.token_hex(8)
        await self._db.execute(
            "INSERT INTO messages "
            "(id, ts, origin, chat_id, topic_id, direction, text, provider, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, time.time(), "user", chat_id, topic_id, "inbound", text, provider, model),
        )
        logger.debug("Recorded inbound message %s chat=%d", msg_id, chat_id)
        return msg_id

    async def record_outbound(
        self,
        envelope: Envelope,
        *,
        cost_usd: float = 0.0,
        tokens: int = 0,
    ) -> str:
        """Record an outbound (bot response) message from a bus envelope. Returns the message ID."""
        msg_id = secrets.token_hex(8)
        await self._db.execute(
            "INSERT INTO messages "
            "(id, ts, origin, chat_id, topic_id, direction, text, provider, model, "
            "session_id, session_name, cost_usd, tokens, elapsed_seconds, is_error, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg_id,
                time.time(),
                envelope.origin.value,
                envelope.chat_id,
                envelope.topic_id,
                "outbound",
                envelope.result_text,
                envelope.provider,
                envelope.model,
                envelope.session_id,
                envelope.session_name,
                cost_usd,
                tokens,
                envelope.elapsed_seconds,
                int(envelope.is_error),
                json.dumps(envelope.metadata),
            ),
        )
        logger.debug("Recorded outbound message %s chat=%d", msg_id, envelope.chat_id)
        return msg_id

    async def record_response(self, record: ResponseRecord) -> str:
        """Record a normal conversation response (not routed via bus). Returns the message ID."""
        msg_id = secrets.token_hex(8)
        await self._db.execute(
            "INSERT INTO messages "
            "(id, ts, origin, chat_id, topic_id, direction, text, provider, model, "
            "session_id, cost_usd, tokens, elapsed_seconds, is_error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg_id,
                time.time(),
                "assistant",
                record.chat_id,
                record.topic_id,
                "outbound",
                record.text,
                record.provider,
                record.model,
                record.session_id,
                record.cost_usd,
                record.tokens,
                record.elapsed_seconds,
                int(record.is_error),
            ),
        )
        logger.debug("Recorded response message %s chat=%d", msg_id, record.chat_id)
        return msg_id

    async def query(
        self,
        chat_id: int,
        *,
        topic_id: int | None = None,
        limit: int = 50,
        before: float | None = None,
        origin: str | None = None,
    ) -> tuple[list[dict[str, object]], bool]:
        """Query message history with cursor-based pagination.

        Returns ``(messages, has_more)`` where *has_more* indicates additional
        older messages exist beyond the requested page.
        """
        conditions = ["chat_id = ?"]
        params: list[object] = [chat_id]

        if topic_id is not None:
            conditions.append("topic_id = ?")
            params.append(topic_id)

        if before is not None:
            conditions.append("ts < ?")
            params.append(before)

        if origin is not None:
            conditions.append("origin = ?")
            params.append(origin)

        where = " AND ".join(conditions)
        # Fetch limit+1 to detect has_more without a separate COUNT query.
        sql = f"SELECT * FROM messages WHERE {where} ORDER BY ts DESC LIMIT ?"  # noqa: S608
        params.append(limit + 1)

        rows = await self._db.fetch_all(sql, tuple(params))
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def cleanup(self, retention_days: int = 30) -> int:
        """Delete messages older than *retention_days*. Returns count of rows deleted."""
        cutoff = time.time() - retention_days * 86400
        count_row = await self._db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM messages WHERE ts < ?",
            (cutoff,),
        )
        cnt = count_row["cnt"] if count_row else 0
        count = cnt if isinstance(cnt, int) else 0
        if count > 0:
            await self._db.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
            logger.info(
                "Message history cleanup: deleted %d rows older than %d days",
                count,
                retention_days,
            )
        return count
