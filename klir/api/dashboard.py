"""Dashboard hub: WebSocket fan-out for live monitoring clients."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web

    from klir.api.crypto import E2ESession
    from klir.bus.envelope import Envelope
    from klir.cli.process_registry import ProcessRegistry, TrackedProcess
    from klir.cron.manager import CronJob, CronManager
    from klir.multiagent.health import AgentHealth
    from klir.session.manager import SessionData, SessionManager
    from klir.session.named import NamedSession, NamedSessionRegistry
    from klir.tasks.models import TaskEntry
    from klir.tasks.registry import TaskRegistry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DashboardFilter:
    """Narrows event stream for a client."""

    origins: list[str] | None = None
    chat_ids: list[int] | None = None
    event_types: list[str] | None = None


@dataclass(slots=True)
class DashboardClient:
    """A connected dashboard WebSocket client with optional filter."""

    ws: web.WebSocketResponse
    e2e: E2ESession | None
    filter: DashboardFilter | None
    connected_at: float = field(default_factory=time.time)

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


class DashboardHub:
    """Manages connected dashboard WebSocket clients and broadcasts events."""

    def __init__(self, max_clients: int = 5) -> None:
        self._clients: set[DashboardClient] = set()
        self._max_clients = max_clients

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def add_client(
        self,
        ws: web.WebSocketResponse,
        e2e: E2ESession | None = None,
    ) -> DashboardClient | None:
        """Add client. Returns None if max_clients reached."""
        if len(self._clients) >= self._max_clients:
            logger.warning("Dashboard client rejected: max_clients=%d reached", self._max_clients)
            return None
        client = DashboardClient(ws=ws, e2e=e2e, filter=None)
        self._clients.add(client)
        logger.info("Dashboard client connected (total=%d)", len(self._clients))
        return client

    def remove_client(self, client: DashboardClient) -> None:
        self._clients.discard(client)
        logger.info("Dashboard client disconnected (total=%d)", len(self._clients))

    def set_filter(self, client: DashboardClient, filt: DashboardFilter | None) -> None:
        client.filter = filt

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Fan out event to all connected clients (respecting filters)."""
        if not self._clients:
            return
        payload = {
            "type": "event",
            "event": event_type,
            "ts": time.time(),
            "data": data,
        }
        dead: list[DashboardClient] = []
        for client in list(self._clients):
            if not self._matches_filter(client, event_type, data):
                continue
            try:
                await self._send_to_client(client, payload)
            except Exception:
                logger.warning("Dashboard send failed, removing client")
                dead.append(client)
        for client in dead:
            self._clients.discard(client)

    def _matches_filter(
        self,
        client: DashboardClient,
        event_type: str,
        data: dict[str, Any],
    ) -> bool:
        """Check if event passes client's filter."""
        filt = client.filter
        if filt is None:
            return True
        if filt.event_types is not None and event_type not in filt.event_types:
            return False
        origin = data.get("origin")
        if filt.origins is not None and origin is not None and origin not in filt.origins:
            return False
        chat_id = data.get("chat_id")
        return not (
            filt.chat_ids is not None and chat_id is not None and chat_id not in filt.chat_ids
        )

    async def _send_to_client(self, client: DashboardClient, payload: dict[str, Any]) -> None:
        """Send JSON to client, with optional E2E encryption."""
        if client.ws.closed:
            msg = "WebSocket closed"
            raise ConnectionError(msg)
        if client.e2e is not None:
            frame = client.e2e.encrypt(payload)
            await client.ws.send_str(frame)
        else:
            await client.ws.send_json(payload)

    async def send_snapshot(self, client: DashboardClient, snapshot: dict[str, Any]) -> None:
        """Send full state snapshot to a newly connected client."""
        payload = {"type": "snapshot", "ts": time.time(), "data": snapshot}
        await self._send_to_client(client, payload)

    async def send_pong(self, client: DashboardClient) -> None:
        """Respond to ping with pong."""
        await self._send_to_client(client, {"type": "pong", "ts": time.time()})

    async def assemble_snapshot(  # noqa: PLR0913
        self,
        session_mgr: SessionManager,
        named_registry: NamedSessionRegistry,
        agent_health: dict[str, AgentHealth],
        cron_mgr: CronManager,
        task_registry: TaskRegistry | None,
        process_registry: ProcessRegistry,
        observer_status: dict[str, Any],
        config_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Build state snapshot from existing managers."""
        sessions = [session_to_dto(s) for s in await session_mgr.list_all()]

        named_sessions = [named_session_to_dto(ns) for ns in named_registry.list_all_active()]

        agents: list[dict[str, Any]] = [
            agent_health_to_dto(name, health) for name, health in agent_health.items()
        ]

        cron_jobs = [cron_job_to_dto(job) for job in cron_mgr.list_jobs()]

        tasks = [task_to_dto(entry) for entry in task_registry.list_all()] if task_registry else []

        processes = [process_to_dto(tp) for tp in process_registry.list_all_active()]

        return {
            "sessions": sessions,
            "named_sessions": named_sessions,
            "agents": agents,
            "cron_jobs": cron_jobs,
            "tasks": tasks,
            "processes": processes,
            "observers": observer_status,
            "config": config_summary,
        }


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def session_to_dto(session: SessionData) -> dict[str, Any]:
    """Convert SessionData to a plain dict for the dashboard."""
    return {
        "chat_id": session.chat_id,
        "topic_id": session.topic_id,
        "user_id": session.user_id,
        "topic_name": session.topic_name,
        "provider": session.provider,
        "model": session.model,
        "session_id": session.session_id,
        "message_count": session.message_count,
        "total_cost_usd": session.total_cost_usd,
        "total_tokens": session.total_tokens,
        "created_at": session.created_at,
        "last_active": session.last_active,
        "thinking_level": session.thinking_level,
    }


def named_session_to_dto(ns: NamedSession) -> dict[str, Any]:
    """Convert NamedSession to a plain dict for the dashboard."""
    return {
        "name": ns.name,
        "chat_id": ns.chat_id,
        "provider": ns.provider,
        "model": ns.model,
        "session_id": ns.session_id,
        "prompt_preview": ns.prompt_preview,
        "status": ns.status,
        "created_at": ns.created_at,
        "message_count": ns.message_count,
    }


def agent_health_to_dto(name: str, health: AgentHealth) -> dict[str, Any]:
    """Convert AgentHealth to a plain dict for the dashboard."""
    return {
        "name": name,
        "status": health.status,
        "uptime_seconds": health.uptime_seconds,
        "restart_count": health.restart_count,
        "last_crash_time": health.last_crash_time,
        "last_crash_error": health.last_crash_error,
    }


def cron_job_to_dto(job: CronJob) -> dict[str, Any]:
    """Convert CronJob to a plain dict for the dashboard."""
    return {
        "id": job.id,
        "title": job.title,
        "schedule": job.schedule,
        "enabled": job.enabled,
        "consecutive_errors": job.consecutive_errors,
        "last_error": job.last_error,
        "last_duration_ms": job.last_duration_ms,
        "provider": job.provider,
        "model": job.model,
    }


def task_to_dto(entry: TaskEntry) -> dict[str, Any]:
    """Convert TaskEntry to a plain dict for the dashboard."""
    return {
        "task_id": entry.task_id,
        "chat_id": entry.chat_id,
        "parent_agent": entry.parent_agent,
        "name": entry.name,
        "prompt_preview": entry.prompt_preview,
        "provider": entry.provider,
        "model": entry.model,
        "status": entry.status,
        "created_at": entry.created_at,
        "completed_at": entry.completed_at,
        "elapsed_seconds": entry.elapsed_seconds,
        "error": entry.error,
        "num_turns": entry.num_turns,
        "question_count": entry.question_count,
        "last_question": entry.last_question,
    }


def process_to_dto(tp: TrackedProcess) -> dict[str, Any]:
    """Convert TrackedProcess to a plain dict for the dashboard."""
    return {
        "chat_id": tp.chat_id,
        "label": tp.label,
        "pid": tp.process.pid,
        "registered_at": tp.registered_at,
    }


def envelope_to_dto(envelope: Envelope) -> dict[str, Any]:
    """Convert Envelope to a plain dict for the dashboard."""
    return {
        "envelope_id": envelope.envelope_id,
        "origin": envelope.origin.value,
        "chat_id": envelope.chat_id,
        "topic_id": envelope.topic_id,
        "status": envelope.status,
        "is_error": envelope.is_error,
        "provider": envelope.provider,
        "model": envelope.model,
        "session_name": envelope.session_name,
        "created_at": envelope.created_at,
        "elapsed_seconds": envelope.elapsed_seconds,
    }
