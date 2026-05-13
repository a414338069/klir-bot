from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATION_RE = re.compile(r"^(\d{3})_.+\.sql$")


class KlirDB:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    # ── helpers ─────────────────────────────────────────────────

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("KlirDB is not open; call open() first")
        return self._conn

    # ── lifecycle ───────────────────────────────────────────────

    async def open(self) -> None:
        if self._conn is not None:
            await self.close()
        # mkdir + connect both run in thread to avoid blocking event loop.
        self._conn = await asyncio.to_thread(self._open_connection)
        await self._run_migrations()

    def _open_connection(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None disables Python's implicit transaction
        # management, giving us full control over BEGIN/COMMIT.
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=wal")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    # ── write operations ────────────────────────────────────────

    async def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        async with self._lock:
            await asyncio.to_thread(self._execute_write, sql, params)

    def _execute_write(self, sql: str, params: tuple[object, ...]) -> None:
        conn = self._require_conn()
        conn.execute("BEGIN")
        try:
            conn.execute(sql, params)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    async def executemany(self, sql: str, params_seq: Sequence[tuple[object, ...]]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._executemany_write, sql, params_seq)

    def _executemany_write(self, sql: str, params_seq: Sequence[tuple[object, ...]]) -> None:
        conn = self._require_conn()
        conn.execute("BEGIN")
        try:
            conn.executemany(sql, params_seq)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── read operations ─────────────────────────────────────────

    async def fetch_one(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> dict[str, object] | None:
        row = await asyncio.to_thread(self._fetch_one, sql, params)
        return dict(row) if row is not None else None

    def _fetch_one(self, sql: str, params: tuple[object, ...]) -> sqlite3.Row | None:
        conn = self._require_conn()
        row: sqlite3.Row | None = conn.execute(sql, params).fetchone()
        return row

    async def fetch_all(self, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
        rows = await asyncio.to_thread(self._fetch_all, sql, params)
        return [dict(r) for r in rows]

    def _fetch_all(self, sql: str, params: tuple[object, ...]) -> list[sqlite3.Row]:
        conn = self._require_conn()
        return conn.execute(sql, params).fetchall()

    # ── migrations ──────────────────────────────────────────────

    def _migrations_dir(self) -> Path:
        return Path(__file__).parent / "migrations"

    async def _run_migrations(self) -> None:
        # Hold a single lock across bootstrap + version read + apply
        # to prevent TOCTOU races if open() is called concurrently.
        async with self._lock:
            await asyncio.to_thread(self._bootstrap_schema_version)
            current = await self._current_version()
            pending = self._pending_migrations(current)
            if not pending:
                return

            logger.info(
                "Applying %d migration(s) from version %d",
                len(pending),
                current,
            )
            for version, path in pending:
                await asyncio.to_thread(self._apply_migration, version, path)

    def _bootstrap_schema_version(self) -> None:
        conn = self._require_conn()
        conn.execute("BEGIN")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT version FROM _schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO _schema_version (version) VALUES (0)")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    async def _current_version(self) -> int:
        row = await self.fetch_one("SELECT version FROM _schema_version")
        if row is None:
            raise RuntimeError("_schema_version table is empty")
        version = row["version"]
        if not isinstance(version, int):
            raise TypeError(f"Unexpected schema version type: {type(version)}")
        return version

    def _pending_migrations(self, current: int) -> list[tuple[int, Path]]:
        migrations_dir = self._migrations_dir()
        if not migrations_dir.is_dir():
            return []

        found: list[tuple[int, Path]] = []
        for p in sorted(migrations_dir.iterdir()):
            m = _MIGRATION_RE.match(p.name)
            if m is None:
                continue
            ver = int(m.group(1))
            if ver > current:
                found.append((ver, p))
        return found

    def _apply_migration(self, version: int, path: Path) -> None:
        conn = self._require_conn()
        sql = path.read_text(encoding="utf-8")
        logger.info("Applying migration %03d: %s", version, path.name)
        # executescript handles semicolons inside strings/comments correctly
        # and runs all statements atomically. We wrap with BEGIN/version bump.
        conn.execute("BEGIN")
        try:
            # executescript issues an implicit COMMIT, so we use individual
            # execute() calls. The _split_sql helper is sufficient for DDL
            # migrations which should not contain semicolons in values.
            for statement in self._split_sql(sql):
                conn.execute(statement)
            conn.execute(
                "UPDATE _schema_version SET version = ?",
                (version,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _split_sql(sql: str) -> list[str]:
        """Split SQL text on semicolons.

        Sufficient for DDL migrations. Migrations containing semicolons
        inside string literals should use Python callables instead.
        """
        return [s.strip() for s in sql.split(";") if s.strip()]
