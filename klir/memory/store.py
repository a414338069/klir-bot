"""SQLite FTS5 memory index with BM25 ranking.

Stores memory abstracts and content in a full-text search index.
Retrieval uses BM25 scoring with a fallback to substring matching.
The interface is designed so the search implementation can be swapped
to embedding-based retrieval without changing callers.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """A single memory record."""

    uri: str
    abstract: str
    category: str
    content: str
    score: float = 0.0


class MemoryStore:
    """SQLite-backed memory index with FTS5 full-text search.

    The ``search`` method is the single retrieval interface.  Currently
    it uses BM25 over FTS5; a future embedding backend only needs to
    replace the body of ``search`` (and possibly ``upsert``).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                uri TEXT PRIMARY KEY,
                abstract TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                uri,
                abstract,
                category,
                content,
                content='memories',
                content_rowid='rowid',
                tokenize='porter unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, uri, abstract, category, content)
                VALUES (new.rowid, new.uri, new.abstract, new.category, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, uri, abstract, category, content)
                VALUES ('delete', old.rowid, old.uri, old.abstract, old.category, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, uri, abstract, category, content)
                VALUES ('delete', old.rowid, old.uri, old.abstract, old.category, old.content);
                INSERT INTO memories_fts(rowid, uri, abstract, category, content)
                VALUES (new.rowid, new.uri, new.abstract, new.category, new.content);
            END;
        """)
        self._conn.commit()

    def upsert(self, entry: MemoryEntry) -> None:
        """Insert or update a memory entry."""
        self._conn.execute(
            """INSERT INTO memories (uri, abstract, category, content)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(uri) DO UPDATE SET
                   abstract = excluded.abstract,
                   category = excluded.category,
                   content = excluded.content,
                   updated_at = datetime('now')""",
            (entry.uri, entry.abstract, entry.category, entry.content),
        )
        self._conn.commit()

    def delete(self, uri: str) -> None:
        """Remove a memory entry by URI."""
        self._conn.execute("DELETE FROM memories WHERE uri = ?", (uri,))
        self._conn.commit()

    def search(self, query: str, *, limit: int = 10) -> list[MemoryEntry]:
        """Search memories by BM25 relevance.

        This is the single retrieval interface.  To swap to embeddings,
        replace this method body.
        """
        query = query.strip()
        if not query:
            return []

        # Strip FTS5-unsafe characters and build OR query for flexibility
        cleaned = [t.replace('"', "") for t in query.split()]
        cleaned = [c for c in cleaned if c]
        if not cleaned:
            return []

        fts_query = " OR ".join(f'"{c}"' for c in cleaned)

        try:
            rows = self._conn.execute(
                """SELECT m.uri, m.abstract, m.category, m.content,
                          rank * -1 AS score
                   FROM memories_fts f
                   JOIN memories m ON m.rowid = f.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "fts5" not in msg and "syntax" not in msg and "match" not in msg:
                raise
            logger.warning("FTS5 query syntax error, falling back to LIKE: %s", fts_query)
            like_pattern = f"%{cleaned[0]}%"
            rows = self._conn.execute(
                """SELECT uri, abstract, category, content, 0.0 AS score
                   FROM memories
                   WHERE abstract LIKE ? OR content LIKE ?
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (like_pattern, like_pattern, limit),
            ).fetchall()

        return [
            MemoryEntry(uri=r[0], abstract=r[1], category=r[2], content=r[3], score=r[4])
            for r in rows
        ]

    def list_category(self, category: str) -> list[MemoryEntry]:
        """List all entries in a category."""
        rows = self._conn.execute(
            "SELECT uri, abstract, category, content FROM memories WHERE category = ?",
            (category,),
        ).fetchall()
        return [MemoryEntry(uri=r[0], abstract=r[1], category=r[2], content=r[3]) for r in rows]

    def all_abstracts(self) -> list[tuple[str, str]]:
        """Return all (uri, abstract) pairs for index summaries."""
        return self._conn.execute(
            "SELECT uri, abstract FROM memories ORDER BY updated_at DESC"
        ).fetchall()

    def count(self) -> int:
        """Return total number of stored memories."""
        row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
