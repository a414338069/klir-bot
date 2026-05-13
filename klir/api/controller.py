"""Dashboard controller: pure business logic layer for REST API endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from klir.api.dashboard import (
    agent_health_to_dto,
    cron_job_to_dto,
    named_session_to_dto,
    process_to_dto,
    session_to_dto,
    task_to_dto,
)
from klir.bot.middleware import QUICK_COMMANDS
from klir.commands import BOT_COMMANDS, MULTIAGENT_SUB_COMMANDS, discover_skill_commands
from klir.cron.run_log import read_run_log_page
from klir.session.key import SessionKey

if TYPE_CHECKING:
    from klir.cli.process_registry import ProcessRegistry
    from klir.cli.tool_activity import ToolActivity
    from klir.cron.manager import CronManager
    from klir.history.store import MessageHistory
    from klir.infra.db import KlirDB
    from klir.multiagent.health import AgentHealth
    from klir.session.manager import SessionManager
    from klir.session.named import NamedSessionRegistry
    from klir.tasks.registry import TaskRegistry

logger = logging.getLogger(__name__)


class DashboardController:
    """Pure business logic for dashboard REST endpoints.

    Returns plain dicts — no framework types (aiohttp, etc.).
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        session_mgr: SessionManager,
        named_registry: NamedSessionRegistry,
        cron_mgr: CronManager,
        task_registry_getter: Callable[[], TaskRegistry | None],
        process_registry: ProcessRegistry,
        task_cancel: Callable[[str], Any],
        message_handler: Callable[..., Any],
        abort_handler: Callable[..., Any],
        history_store: MessageHistory,
        dashboard_hub: Any,
        observer_status_getter: Callable[[], dict[str, Any]],
        config_summary_getter: Callable[[], dict[str, Any]],
        agent_health_getter: Callable[[], dict[str, AgentHealth]],
        db: KlirDB,
        skills_dir: Path,
        has_supervisor: Callable[[], bool],
    ) -> None:
        self._session_mgr = session_mgr
        self._named_registry = named_registry
        self._cron_mgr = cron_mgr
        self._task_registry_getter = task_registry_getter
        self._process_registry = process_registry
        self._task_cancel = task_cancel
        self._message_handler = message_handler
        self._abort_handler = abort_handler
        self._history_store = history_store
        self._dashboard_hub = dashboard_hub
        self._observer_status_getter = observer_status_getter
        self._config_summary_getter = config_summary_getter
        self._agent_health_getter = agent_health_getter
        self._db = db
        self._skills_dir = skills_dir
        self._has_supervisor = has_supervisor

    # ── Read-only list endpoints ──────────────────────────────────────

    async def list_sessions(
        self,
        *,
        chat_id: int | None = None,
    ) -> dict[str, Any]:
        """Return all sessions, optionally filtered by chat_id."""
        all_sessions = await self._session_mgr.list_all()
        if chat_id is not None:
            all_sessions = [s for s in all_sessions if s.chat_id == chat_id]
        return {"sessions": [session_to_dto(s) for s in all_sessions]}

    async def list_named_sessions(
        self,
        *,
        chat_id: int | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Return named sessions, optionally filtered by chat_id and/or status."""
        sessions = self._named_registry.list_all_active()
        if chat_id is not None:
            sessions = [ns for ns in sessions if ns.chat_id == chat_id]
        if status is not None:
            sessions = [ns for ns in sessions if ns.status == status]
        return {"sessions": [named_session_to_dto(ns) for ns in sessions]}

    async def list_agents(self) -> dict[str, Any]:
        """Return agent health as a list (matches WebSocket snapshot shape)."""
        health_map = self._agent_health_getter()
        return {
            "agents": [agent_health_to_dto(name, health) for name, health in health_map.items()],
        }

    async def list_cron_jobs(self) -> dict[str, Any]:
        """Return all cron jobs."""
        return {"jobs": [cron_job_to_dto(job) for job in self._cron_mgr.list_jobs()]}

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Return tasks, optionally filtered by status and/or agent."""
        registry = self._task_registry_getter()
        if registry is None:
            return {"tasks": []}
        tasks = registry.list_all()
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if agent is not None:
            tasks = [t for t in tasks if t.parent_agent == agent]
        return {"tasks": [task_to_dto(t) for t in tasks]}

    async def list_processes(self) -> dict[str, Any]:
        """Return all active tracked processes."""
        return {
            "processes": [process_to_dto(tp) for tp in self._process_registry.list_all_active()],
        }

    async def list_commands(self) -> dict[str, Any]:
        """Return all available commands with metadata."""
        commands: list[dict[str, Any]] = []

        for name, desc in BOT_COMMANDS:
            commands.append(
                {
                    "name": name,
                    "description": desc,
                    "category": "core",
                    "quick": f"/{name}" in QUICK_COMMANDS,
                }
            )

        if self._has_supervisor():
            for name, desc in MULTIAGENT_SUB_COMMANDS:
                commands.append(
                    {
                        "name": name,
                        "description": desc,
                        "category": "agent",
                        "quick": False,
                    }
                )

        for name, desc in await asyncio.to_thread(discover_skill_commands, self._skills_dir):
            commands.append(
                {
                    "name": name,
                    "description": desc,
                    "category": "skill",
                    "quick": False,
                }
            )

        return {"commands": commands}

    # ── Query endpoints ───────────────────────────────────────────────

    async def get_history(
        self,
        chat_id: int,
        *,
        topic_id: int | None = None,
        limit: int = 50,
        before: float | None = None,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """Return paginated message history for a chat."""
        limit = max(1, min(limit, 200))
        messages, has_more = await self._history_store.query(
            chat_id,
            topic_id=topic_id,
            limit=limit,
            before=before,
            origin=origin,
        )
        result: dict[str, Any] = {
            "messages": messages,
            "has_more": has_more,
        }
        if has_more and messages:
            result["next_cursor"] = messages[-1]["ts"]
        return result

    async def get_cron_history(
        self,
        job_id: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return run history for a cron job."""
        job = self._cron_mgr.get_job(job_id)
        if job is None:
            return {"runs": []}
        limit = max(1, min(limit, 100))
        page = await read_run_log_page(self._db, job_id=job_id, limit=limit)
        return {
            "runs": [
                {
                    "ts": e.ts,
                    "job_id": e.job_id,
                    "status": e.status,
                    "error": e.error,
                    "summary": e.summary,
                    "duration_ms": e.duration_ms,
                    "delivery_status": e.delivery_status,
                    "provider": e.provider,
                    "model": e.model,
                    "input_tokens": e.input_tokens,
                    "output_tokens": e.output_tokens,
                }
                for e in page.entries
            ],
        }

    async def get_health(self) -> dict[str, Any]:
        """Return system health summary."""
        return {
            "status": "ok",
            "connections": {
                "dashboard_clients": self._dashboard_hub.client_count,
            },
            "observers": self._observer_status_getter(),
        }

    # ── Action endpoints ──────────────────────────────────────────────

    async def toggle_cron_job(
        self,
        job_id: str,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        """Enable or disable a cron job."""
        ok = self._cron_mgr.set_enabled(job_id, enabled=enabled)
        return {"ok": ok, "job_id": job_id, "enabled": enabled}

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a running task."""
        ok = await self._task_cancel(task_id)
        return {"ok": ok, "task_id": task_id}

    async def abort_chat(self, chat_id: int) -> dict[str, Any]:
        """Abort active processes for a chat."""
        killed = await self._abort_handler(chat_id)
        return {"ok": True, "killed": killed}

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        topic_id: int | None = None,
    ) -> dict[str, Any]:
        """Send a message to a chat (non-streaming)."""
        key = SessionKey(chat_id=chat_id, topic_id=topic_id)
        try:
            result = await self._message_handler(key, text)
        except Exception:
            logger.exception("send_message failed chat_id=%d", chat_id)
            return {"ok": False, "error": "internal_error"}
        else:
            return {"ok": True, "result": {"text": result.text}}

    async def send_message_stream(
        self,
        chat_id: int,
        text: str,
        *,
        topic_id: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """Send a message and yield SSE events as they arrive."""
        key = SessionKey(chat_id=chat_id, topic_id=topic_id)
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def _on_text_delta(delta: str) -> None:
            queue.put_nowait(f"event: text_delta\ndata: {json.dumps({'delta': delta})}\n\n")

        async def _on_tool_activity(activity: ToolActivity) -> None:
            label = activity.display_label()
            queue.put_nowait(f"event: tool_activity\ndata: {json.dumps({'activity': label})}\n\n")

        async def _on_system_status(status: str | None) -> None:
            queue.put_nowait(f"event: system_status\ndata: {json.dumps({'status': status})}\n\n")

        async def _run() -> None:
            try:
                result = await self._message_handler(
                    key,
                    text,
                    on_text_delta=_on_text_delta,
                    on_tool_activity=_on_tool_activity,
                    on_system_status=_on_system_status,
                )
                queue.put_nowait(f"event: result\ndata: {json.dumps({'text': result.text})}\n\n")
            except Exception:
                logger.exception("send_message_stream failed chat_id=%d", chat_id)
                queue.put_nowait(
                    f"event: result\ndata: {json.dumps({'error': 'internal_error'})}\n\n"
                )
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(_run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
