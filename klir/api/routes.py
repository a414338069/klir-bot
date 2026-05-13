"""Dashboard REST API routes: thin aiohttp layer over DashboardController."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_auth(
    request: web.Request,
    verify_bearer: Callable[[web.Request], bool],
) -> web.Response | None:
    """Return a 401 response if the request is not authenticated, else None."""
    if not verify_bearer(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


def _parse_int_query(
    request: web.Request,
    name: str,
) -> tuple[int | None, web.Response | None]:
    """Parse an optional integer query param. Returns (value, error_response)."""
    raw = request.query.get(name)
    if raw is None:
        return None, None
    try:
        return int(raw), None
    except ValueError:
        return None, web.json_response({"error": f"{name} must be an integer"}, status=400)


def _parse_float_query(
    request: web.Request,
    name: str,
) -> tuple[float | None, web.Response | None]:
    """Parse an optional float query param. Returns (value, error_response)."""
    raw = request.query.get(name)
    if raw is None:
        return None, None
    try:
        return float(raw), None
    except ValueError:
        return None, web.json_response({"error": f"{name} must be a number"}, status=400)


def _parse_int_path(
    request: web.Request,
    name: str,
) -> tuple[int, web.Response | None]:
    """Parse a required integer path param. Returns (value, error_response)."""
    try:
        return int(request.match_info[name]), None
    except ValueError:
        return 0, web.json_response({"error": f"{name} must be an integer"}, status=400)


async def _read_json_body(
    request: web.Request,
) -> tuple[dict[str, Any] | None, web.Response | None]:
    """Read and validate JSON body. Returns (body, error_response)."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return None, web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return None, web.json_response({"error": "invalid JSON body"}, status=400)
    return body, None


def _set_if(kwargs: dict[str, object], key: str, value: object) -> None:
    """Set key in kwargs if value is not None."""
    if value is not None:
        kwargs[key] = value


# ---------------------------------------------------------------------------
# Handler factories - each returns an async handler closure
# ---------------------------------------------------------------------------


def _h_list_sessions(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        chat_id, err = _parse_int_query(request, "chat_id")
        if err:
            return err
        return web.json_response(await ctrl.list_sessions(chat_id=chat_id))

    return handler


def _h_get_history(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        chat_id, err = _parse_int_path(request, "chat_id")
        if err:
            return err
        kwargs: dict[str, object] = {"chat_id": chat_id}
        for name, parser in [("topic_id", _parse_int_query), ("limit", _parse_int_query)]:
            val, err = parser(request, name)
            if err:
                return err
            _set_if(kwargs, name, val)
        before, err = _parse_float_query(request, "before")
        if err:
            return err
        _set_if(kwargs, "before", before)
        _set_if(kwargs, "origin", request.query.get("origin"))
        return web.json_response(await ctrl.get_history(**kwargs))

    return handler


def _h_list_named_sessions(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        kwargs: dict[str, object] = {}
        chat_id, err = _parse_int_query(request, "chat_id")
        if err:
            return err
        _set_if(kwargs, "chat_id", chat_id)
        _set_if(kwargs, "status", request.query.get("status"))
        return web.json_response(await ctrl.list_named_sessions(**kwargs))

    return handler


def _h_list_agents(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        return web.json_response(await ctrl.list_agents())

    return handler


def _h_list_cron_jobs(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        return web.json_response(await ctrl.list_cron_jobs())

    return handler


def _h_get_cron_history(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        job_id = request.match_info["job_id"]
        kwargs: dict[str, object] = {"job_id": job_id}
        limit, err = _parse_int_query(request, "limit")
        if err:
            return err
        _set_if(kwargs, "limit", limit)
        return web.json_response(await ctrl.get_cron_history(**kwargs))

    return handler


def _h_toggle_cron_job(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        job_id = request.match_info["job_id"]
        body, err = await _read_json_body(request)
        if err:
            return err
        assert body is not None
        if "enabled" not in body:
            return web.json_response({"error": "missing required field: enabled"}, status=400)
        enabled = body["enabled"]
        if not isinstance(enabled, bool):
            return web.json_response({"error": "enabled must be a boolean"}, status=400)
        return web.json_response(await ctrl.toggle_cron_job(job_id, enabled=enabled))

    return handler


def _h_list_tasks(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        kwargs: dict[str, object] = {}
        _set_if(kwargs, "status", request.query.get("status"))
        _set_if(kwargs, "agent", request.query.get("agent"))
        return web.json_response(await ctrl.list_tasks(**kwargs))

    return handler


def _h_cancel_task(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        task_id = request.match_info["task_id"]
        return web.json_response(await ctrl.cancel_task(task_id))

    return handler


def _h_list_processes(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        return web.json_response(await ctrl.list_processes())

    return handler


def _h_list_commands(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        return web.json_response(await ctrl.list_commands())

    return handler


async def _validate_message_body(
    request: web.Request,
) -> tuple[int, str, int | None, bool, web.Response | None]:
    """Validate send-message request. Returns (chat_id, text, topic_id, stream, err)."""
    chat_id, err = _parse_int_path(request, "chat_id")
    if err:
        return 0, "", None, False, err
    body, err = await _read_json_body(request)
    if err:
        return 0, "", None, False, err
    assert body is not None
    if "text" not in body:
        return (
            0,
            "",
            None,
            False,
            web.json_response({"error": "missing required field: text"}, status=400),
        )
    text = body["text"]
    if not isinstance(text, str) or not text.strip():
        return (
            0,
            "",
            None,
            False,
            web.json_response({"error": "text must be a non-empty string"}, status=400),
        )
    raw_topic_id = body.get("topic_id")
    if raw_topic_id is not None and not isinstance(raw_topic_id, int):
        return (
            0,
            "",
            None,
            False,
            web.json_response({"error": "topic_id must be an integer"}, status=400),
        )
    topic_id: int | None = raw_topic_id
    stream = bool(body.get("stream", False))
    return chat_id, text.strip(), topic_id, stream, None


def _h_send_message(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response | web.StreamResponse:
        if err := _require_auth(request, auth):
            return err
        chat_id, text, topic_id, stream, err = await _validate_message_body(request)
        if err:
            return err
        kwargs: dict[str, object] = {"chat_id": chat_id, "text": text}
        _set_if(kwargs, "topic_id", topic_id)
        if stream:
            return await _stream_response(request, ctrl, kwargs)
        return web.json_response(await ctrl.send_message(**kwargs))

    return handler


async def _stream_response(
    request: web.Request,
    ctrl: Any,
    kwargs: dict[str, object],
) -> web.StreamResponse:
    """Create an SSE StreamResponse from the controller's async generator."""
    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)
    gen = ctrl.send_message_stream(**kwargs)
    try:
        async for chunk in gen:
            await resp.write(chunk.encode() if isinstance(chunk, str) else chunk)
        await resp.write_eof()
    except (ConnectionResetError, ConnectionError):
        logger.debug("SSE client disconnected mid-stream")
        await gen.aclose()
    return resp


def _h_health(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        return web.json_response(await ctrl.get_health())

    return handler


def _h_abort_chat(ctrl: Any, auth: Callable[[web.Request], bool]) -> Any:
    async def handler(request: web.Request) -> web.Response:
        if err := _require_auth(request, auth):
            return err
        body, err = await _read_json_body(request)
        if err:
            return err
        assert body is not None
        if "chat_id" not in body:
            return web.json_response({"error": "missing required field: chat_id"}, status=400)
        chat_id = body["chat_id"]
        if not isinstance(chat_id, int):
            return web.json_response({"error": "chat_id must be an integer"}, status=400)
        return web.json_response(await ctrl.abort_chat(chat_id))

    return handler


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_dashboard_routes(
    app: web.Application,
    controller: object,
    verify_bearer: Callable[[web.Request], bool],
) -> None:
    """Register all dashboard REST API endpoints on *app*."""
    r = app.router
    r.add_get("/api/sessions", _h_list_sessions(controller, verify_bearer))
    r.add_get("/api/sessions/{chat_id}/history", _h_get_history(controller, verify_bearer))
    r.add_get("/api/named-sessions", _h_list_named_sessions(controller, verify_bearer))
    r.add_get("/api/agents", _h_list_agents(controller, verify_bearer))
    r.add_get("/api/cron", _h_list_cron_jobs(controller, verify_bearer))
    r.add_get("/api/cron/{job_id}/history", _h_get_cron_history(controller, verify_bearer))
    r.add_patch("/api/cron/{job_id}", _h_toggle_cron_job(controller, verify_bearer))
    r.add_get("/api/tasks", _h_list_tasks(controller, verify_bearer))
    r.add_post("/api/tasks/{task_id}/cancel", _h_cancel_task(controller, verify_bearer))
    r.add_get("/api/processes", _h_list_processes(controller, verify_bearer))
    r.add_get("/api/commands", _h_list_commands(controller, verify_bearer))
    r.add_post("/api/sessions/{chat_id}/message", _h_send_message(controller, verify_bearer))
    r.add_get("/api/health", _h_health(controller, verify_bearer))
    r.add_post("/api/abort", _h_abort_chat(controller, verify_bearer))
