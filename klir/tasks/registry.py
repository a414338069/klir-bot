"""Task registry: persistent CRUD for background tasks backed by SQLite."""

from __future__ import annotations

import logging
import secrets
import shutil
import time
from pathlib import Path

from klir.infra.db import KlirDB
from klir.tasks.models import TaskEntry, TaskSubmit

logger = logging.getLogger(__name__)

_PROMPT_PREVIEW_LEN = 80
_RESULT_PREVIEW_LEN = 200
_FINISHED_STATUSES = frozenset({"done", "failed", "cancelled"})

_INSERT_COLS = (
    "task_id",
    "chat_id",
    "parent_agent",
    "name",
    "prompt_preview",
    "original_prompt",
    "provider",
    "model",
    "status",
    "session_id",
    "created_at",
    "completed_at",
    "elapsed_seconds",
    "error",
    "result_preview",
    "question_count",
    "num_turns",
    "last_question",
    "thinking",
    "tasks_dir",
    "thread_id",
)

_INSERT_SQL = (
    f"INSERT INTO tasks ({', '.join(_INSERT_COLS)}) "  # noqa: S608
    f"VALUES ({', '.join('?' for _ in _INSERT_COLS)})"
)


def _entry_to_params(entry: TaskEntry) -> tuple[object, ...]:
    """Convert TaskEntry to a tuple of SQLite parameters matching _INSERT_COLS order."""
    return (
        entry.task_id,
        entry.chat_id,
        entry.parent_agent,
        entry.name,
        entry.prompt_preview,
        entry.original_prompt,
        entry.provider,
        entry.model,
        entry.status,
        entry.session_id,
        entry.created_at,
        entry.completed_at,
        entry.elapsed_seconds,
        entry.error,
        entry.result_preview,
        entry.question_count,
        entry.num_turns,
        entry.last_question,
        entry.thinking,
        entry.tasks_dir,
        entry.thread_id,
    )


def _entry_from_row(row: dict[str, object]) -> TaskEntry:
    """Construct TaskEntry from a SQLite row dict."""
    return TaskEntry(
        task_id=str(row["task_id"]),
        chat_id=int(row["chat_id"]),  # type: ignore[call-overload]
        parent_agent=str(row.get("parent_agent") or "main"),
        name=str(row.get("name") or ""),
        prompt_preview=str(row.get("prompt_preview") or ""),
        provider=str(row.get("provider") or ""),
        model=str(row.get("model") or ""),
        status=str(row.get("status") or "running"),
        session_id=str(row.get("session_id") or ""),
        created_at=float(row.get("created_at") or 0.0),  # type: ignore[arg-type]
        completed_at=float(row.get("completed_at") or 0.0),  # type: ignore[arg-type]
        elapsed_seconds=float(row.get("elapsed_seconds") or 0.0),  # type: ignore[arg-type]
        error=str(row.get("error") or ""),
        result_preview=str(row.get("result_preview") or ""),
        question_count=int(row.get("question_count") or 0),  # type: ignore[call-overload]
        num_turns=int(row.get("num_turns") or 0),  # type: ignore[call-overload]
        last_question=str(row.get("last_question") or ""),
        thinking=str(row.get("thinking") or ""),
        tasks_dir=str(row.get("tasks_dir") or ""),
        thread_id=int(row["thread_id"]) if row.get("thread_id") is not None else None,  # type: ignore[call-overload]
        original_prompt=str(row.get("original_prompt") or ""),
    )


class TaskRegistry:
    """Persistent registry for background task metadata.

    Uses SQLite via ``KlirDB`` for persistence with an in-memory cache for
    fast synchronous reads.  On load, stale ``"running"`` entries are
    downgraded to ``"failed"``.
    """

    def __init__(self, db: KlirDB, tasks_dir: Path) -> None:
        self._db = db
        self._tasks_dir = tasks_dir
        self._entries: dict[str, TaskEntry] = {}

    async def load(self) -> None:
        """Load tasks from SQLite into memory."""
        rows = await self._db.fetch_all("SELECT * FROM tasks")

        for row in rows:
            entry = _entry_from_row(row)
            if entry.status == "running":
                entry.status = "failed"
                entry.error = "Bot restarted while task was running"
                await self._db.execute(
                    "UPDATE tasks SET status = ?, error = ? WHERE task_id = ?",
                    ("failed", entry.error, entry.task_id),
                )
                logger.info("Downgraded stale running task %s to failed", entry.task_id)
            self._entries[entry.task_id] = entry

        await self.cleanup_orphans()

    # -- Write methods (async: update dict + SQLite) ---------------------------

    async def create(
        self,
        submit: TaskSubmit,
        provider: str,
        model: str,
        thinking: str = "",
        tasks_dir: Path | None = None,
    ) -> TaskEntry:
        """Create a new task entry and persist it.

        *tasks_dir* overrides the default tasks directory (for per-agent isolation).
        """
        task_id = secrets.token_hex(4)
        resolved_dir = tasks_dir or self._tasks_dir
        entry = TaskEntry(
            task_id=task_id,
            chat_id=submit.chat_id,
            parent_agent=submit.parent_agent,
            name=submit.name or task_id,
            prompt_preview=submit.prompt[:_PROMPT_PREVIEW_LEN],
            provider=provider,
            model=model,
            status="running",
            original_prompt=submit.prompt,
            thinking=thinking,
            tasks_dir=str(resolved_dir),
            thread_id=submit.thread_id,
        )
        self._entries[task_id] = entry
        await self._db.execute(_INSERT_SQL, _entry_to_params(entry))

        # Create task folder with TASKMEMORY.md and rule files
        folder = self.task_folder(task_id)
        folder.mkdir(parents=True, exist_ok=True)
        _seed_task_folder(folder, entry, submit.prompt, provider, model)

        logger.info("Task created id=%s name='%s' provider=%s", task_id, entry.name, provider)
        return entry

    async def update_status(self, task_id: str, status: str, **kwargs: object) -> None:
        """Update a task's status and optional fields."""
        entry = self._entries.get(task_id)
        if entry is None:
            return
        entry.status = status
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)

        # Build dynamic UPDATE
        set_clauses = ["status = ?"]
        params: list[object] = [status]
        for key, value in kwargs.items():
            if hasattr(entry, key):
                set_clauses.append(f"{key} = ?")
                params.append(value)
        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(set_clauses)} WHERE task_id = ?"  # noqa: S608
        await self._db.execute(sql, tuple(params))

    async def delete(self, task_id: str) -> bool:
        """Delete a single finished task (entry + folder).

        Only tasks with status done/failed/cancelled can be deleted.
        Returns ``True`` if deleted, ``False`` if not found or not deletable.
        """
        entry = self._entries.get(task_id)
        if entry is None or entry.status not in _FINISHED_STATUSES:
            return False
        await self._remove_entries([task_id], "delete")
        return True

    async def cleanup_old(self, max_age_hours: int) -> int:
        """Remove completed/failed tasks older than *max_age_hours*."""
        cutoff = time.time() - max_age_hours * 3600
        to_remove: list[str] = []
        for task_id, entry in self._entries.items():
            if entry.status in _FINISHED_STATUSES and entry.created_at < cutoff:
                to_remove.append(task_id)
        return await self._remove_entries(to_remove, "cleanup_old")

    async def cleanup_finished(self, chat_id: int | None = None) -> int:
        """Remove all finished tasks (done/failed/cancelled) regardless of age."""
        to_remove: list[str] = []
        for task_id, entry in self._entries.items():
            if entry.status not in _FINISHED_STATUSES:
                continue
            if chat_id is not None and entry.chat_id != chat_id:
                continue
            to_remove.append(task_id)
        return await self._remove_entries(to_remove, "cleanup_finished")

    async def cleanup_orphans(self) -> int:
        """Remove orphaned entries and folders so nothing is left dangling.

        Called at startup and periodically.  Returns total items removed.
        """
        removed = await self._cleanup_orphan_entries()
        removed += self._cleanup_orphan_folders()
        return removed

    async def _cleanup_orphan_entries(self) -> int:
        """Remove registry entries whose task folder no longer exists."""
        orphan_ids: list[str] = [tid for tid in self._entries if not self.task_folder(tid).is_dir()]
        for task_id in orphan_ids:
            logger.info("Removing orphan registry entry %s (no folder)", task_id)
            del self._entries[task_id]
            await self._db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        return len(orphan_ids)

    def _cleanup_orphan_folders(self) -> int:
        """Remove task folders that have no corresponding registry entry."""
        known = set(self._entries)
        scan_dirs: set[Path] = {self._tasks_dir}
        for entry in self._entries.values():
            if entry.tasks_dir:
                scan_dirs.add(Path(entry.tasks_dir))

        removed = 0
        for tasks_dir in scan_dirs:
            if not tasks_dir.is_dir():
                continue
            for child in tasks_dir.iterdir():
                if child.is_dir() and child.name not in known:
                    logger.info("Removing orphan task folder %s (no registry entry)", child.name)
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
        return removed

    async def _remove_entries(self, task_ids: list[str], label: str) -> int:
        """Delete entries and their folders from the registry."""
        # Resolve folder paths before deleting entries (entries carry per-agent
        # tasks_dir overrides that task_folder() needs).
        folders = {tid: self.task_folder(tid) for tid in task_ids}
        for task_id in task_ids:
            del self._entries[task_id]
            await self._db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            folder = folders[task_id]
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        if task_ids:
            logger.info("%s removed %d task(s)", label, len(task_ids))
        return len(task_ids)

    # -- Read methods (sync: from in-memory dict) ------------------------------

    def get(self, task_id: str) -> TaskEntry | None:
        return self._entries.get(task_id)

    def find_by_name(self, chat_id: int, name: str) -> TaskEntry | None:
        """Find a task by name within a chat."""
        lower = name.lower()
        for entry in self._entries.values():
            if entry.chat_id == chat_id and entry.name.lower() == lower:
                return entry
        return None

    def list_active(self, chat_id: int | None = None) -> list[TaskEntry]:
        """Return tasks with status 'running'."""
        entries = [e for e in self._entries.values() if e.status == "running"]
        if chat_id is not None:
            entries = [e for e in entries if e.chat_id == chat_id]
        return sorted(entries, key=lambda e: e.created_at)

    def list_all(
        self,
        chat_id: int | None = None,
        parent_agent: str | None = None,
    ) -> list[TaskEntry]:
        """Return all tasks (active + completed)."""
        entries = list(self._entries.values())
        if chat_id is not None:
            entries = [e for e in entries if e.chat_id == chat_id]
        if parent_agent is not None:
            entries = [e for e in entries if e.parent_agent == parent_agent]
        return sorted(entries, key=lambda e: e.created_at, reverse=True)

    def task_folder(self, task_id: str) -> Path:
        """Return the task's metadata folder.

        Uses the entry's stored ``tasks_dir`` when available (per-agent isolation),
        falling back to the registry-wide default.
        """
        entry = self._entries.get(task_id)
        if entry and entry.tasks_dir:
            return Path(entry.tasks_dir) / task_id
        return self._tasks_dir / task_id

    def taskmemory_path(self, task_id: str) -> Path:
        """Return the path to a task's TASKMEMORY.md."""
        return self.task_folder(task_id) / "TASKMEMORY.md"


# -- Task folder seeding -------------------------------------------------------

_TASK_RULES = """\
# Task Agent Rules

You are a background task agent. You have NO direct user access.

## MANDATORY: Asking Questions

If you need ANY information to complete your task (missing details,
clarifications, user preferences), you MUST use this tool:

```bash
python3 tools/task_tools/ask_parent.py "your question here"
```

This forwards your question to the parent agent and returns immediately.
Do NOT write questions in your response — the user cannot see them.
After asking, finish your current work — you will be resumed with the answer.

## Other Tools (in `tools/task_tools/`)

- `python3 tools/task_tools/list_tasks.py` — List active tasks
- `python3 tools/task_tools/cancel_task.py TASK_ID` — Cancel a task
- `python3 tools/task_tools/delete_task.py TASK_ID` — Delete a finished task

## TASKMEMORY.md

Path: `{taskmemory_path}`

Update after completing your work:
- What you did and key decisions
- Results, file paths, or findings
"""


def _seed_task_folder(
    folder: Path,
    entry: TaskEntry,
    prompt: str,
    provider: str,
    model: str,
) -> None:
    """Seed a task folder with TASKMEMORY.md and rule files."""
    taskmemory = folder / "TASKMEMORY.md"
    if not taskmemory.exists():
        taskmemory.write_text(
            f"# Task: {entry.name}\n\n"
            f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Provider: {provider}/{model}\n\n"
            f"## Task Description\n\n"
            f"{prompt[:500]}\n\n"
            f"## Progress\n\n"
            f"_Update this section as you work._\n",
            encoding="utf-8",
        )

    # Deploy rule files for all providers
    rules_content = _TASK_RULES.format(taskmemory_path=taskmemory)
    for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
        rules_path = folder / name
        rules_path.write_text(rules_content, encoding="utf-8")
