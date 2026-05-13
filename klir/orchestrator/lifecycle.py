"""Orchestrator lifecycle: async factory, startup, shutdown, infra management."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from klir.files.allowed_roots import resolve_allowed_roots
from klir.i18n import load_translations
from klir.workspace.init import inject_runtime_environment
from klir.workspace.paths import KlirPaths, resolve_paths
from klir.workspace.skill_sync import cleanup_klir_links

if TYPE_CHECKING:
    from klir.config import AgentConfig
    from klir.orchestrator.core import Orchestrator

logger = logging.getLogger(__name__)


async def create_orchestrator(
    config: AgentConfig,
    *,
    agent_name: str = "main",
) -> Orchestrator:
    """Async factory: build an Orchestrator.

    Workspace must already be initialized by the caller (``__main__.load_config``).
    """
    from klir.orchestrator.core import Orchestrator

    paths = resolve_paths(klir_home=config.klir_home)

    # Only set the process-wide env var for the main agent to avoid
    # race conditions in multi-agent mode (sub-agents use per-subprocess env).
    if agent_name == "main":
        os.environ["KLIR_HOME"] = str(paths.klir_home)

    await asyncio.to_thread(
        inject_runtime_environment,
        paths,
        agent_name=agent_name,
    )

    orch = Orchestrator(
        config,
        paths,
        agent_name=agent_name,
        interagent_port=config.interagent_port,
    )

    await orch.db.open()

    load_translations(config.language)

    from klir.cli.auth import AuthStatus, check_all_auth

    auth_results = await asyncio.to_thread(check_all_auth)
    orch._providers.apply_auth_results(
        auth_results,
        auth_status_enum=AuthStatus,
        cli_service=orch._cli_service,
    )

    if not orch._providers.available_providers:
        logger.error("No authenticated providers found! CLI calls will fail.")
    else:
        logger.info(
            "Available providers: %s",
            ", ".join(sorted(orch._providers.available_providers)),
        )

    await asyncio.to_thread(orch._providers.init_gemini_state, paths.workspace)

    codex_cache = await orch._observers.init_model_caches(
        on_gemini_refresh=orch._providers.on_gemini_models_refresh
    )
    orch._observers.init_task_observers(
        cron_manager=orch._cron_manager,
        webhook_manager=orch._webhook_manager,
        cli_service=orch._cli_service,
        codex_cache=codex_cache,
        db=orch._db,
    )
    orch._providers._codex_cache_fn = lambda: orch._observers.codex_cache
    orch._observers.cleanup.set_session_manager(orch._sessions)
    # Restore previously-saved opencode serve instances.
    # Must happen before start_all() so the shared manager is populated
    # before any background observers fire HTTP CLI calls.
    await _restore_server_state(paths)

    await orch._observers.start_all()

    # Direct API server (WebSocket, designed for Tailscale)
    if config.api.enabled:
        await start_api_server(orch, config, paths)

    await orch._observers.start_config_reloader(
        on_hot_reload=orch._on_config_hot_reload,
        on_restart_needed=lambda fields: logger.warning(
            "Config changed but requires restart: %s", ", ".join(fields)
        ),
    )

    return orch


def _make_task_cancel(orch: Orchestrator) -> Callable[[str], Awaitable[bool]]:
    """Build an awaitable cancel callback that tolerates a missing TaskHub."""

    async def _cancel(task_id: str) -> bool:
        if orch._task_hub is None:
            return False
        return await orch._task_hub.cancel(task_id)

    return _cancel


async def start_api_server(
    orch: Orchestrator,
    config: AgentConfig,
    paths: KlirPaths,
) -> None:
    """Initialize and start the direct WebSocket API server."""
    from klir.api.server import ApiServer

    if not config.api.token:
        from klir.config import update_config_file_async

        token = secrets.token_urlsafe(32)
        config.api.token = token
        await update_config_file_async(
            paths.config_path,
            api={**config.api.model_dump(), "token": token},
        )
        logger.info("Generated API auth token (persisted to config)")

    default_chat_id = config.api.chat_id or (
        config.allowed_user_ids[0] if config.allowed_user_ids else 1
    )
    server = ApiServer(config.api, default_chat_id=default_chat_id)
    server.set_message_handler(orch.handle_message_streaming)
    server.set_abort_handler(orch.abort)
    server.set_file_context(
        allowed_roots=resolve_allowed_roots(config.file_access, paths.workspace),
        upload_dir=paths.api_files_dir,
        workspace=paths.workspace,
    )
    server.set_provider_info(orch._providers.build_provider_info(orch._observers.codex_cache_obs))
    server.set_active_state_getter(
        lambda: orch._providers.resolve_runtime_target(orch._config.model)
    )

    # -- Dashboard hub ---------------------------------------------------------
    dashboard_hub = None
    if config.api.dashboard.enabled:
        try:
            from klir.api.dashboard import DashboardHub
        except ImportError:
            logger.warning("Dashboard enabled but klir.api.dashboard not available")
        else:
            dashboard_hub = DashboardHub(max_clients=config.api.dashboard.max_clients)
            server.set_dashboard_hub(dashboard_hub)
            server.set_snapshot_sources(
                session_mgr=orch._sessions,
                named_registry=orch._named_sessions,
                agent_health_getter=lambda: orch._supervisor.health if orch._supervisor else {},
                cron_mgr=orch._cron_manager,
                task_registry_getter=lambda: orch._task_hub._registry if orch._task_hub else None,
                task_cancel=_make_task_cancel(orch),
                process_registry=orch._process_registry,
                observer_status_getter=lambda: {
                    "heartbeat": orch._observers.heartbeat is not None,
                    "cron": orch._observers.cron is not None,
                    "webhook": orch._observers.webhook is not None,
                    "background": orch._observers.background is not None,
                },
                config_summary_getter=lambda: {
                    "provider": orch._config.provider,
                    "model": orch._config.model,
                    "language": orch._config.language,
                },
                history_store=orch._message_history,
                db=orch._db,
                skills_dir=paths.skills_dir,
                has_supervisor=lambda: orch._supervisor is not None,
            )
            logger.info(
                "Dashboard hub initialized (max_clients=%d)",
                config.api.dashboard.max_clients,
            )

    try:
        await server.start()
    except OSError:
        logger.exception(
            "Failed to start API server on %s:%d",
            config.api.host,
            config.api.port,
        )
        return

    # Start Tailscale serve/funnel if configured
    ts_mode = config.api.tailscale.mode
    if ts_mode != "off":
        from klir.infra.tailscale import start_tailscale_exposure

        hostname = await start_tailscale_exposure(ts_mode, config.api.port)
        if hostname:
            logger.info(
                "Dashboard available at https://%s/dashboard/",
                hostname,
            )

    orch._api_stop = server.stop
    orch._dashboard_hub = dashboard_hub


async def shutdown(orch: Orchestrator) -> None:
    """Cleanup on bot shutdown."""
    # Persist server manager state BEFORE any cleanup so running
    # ``opencode serve`` instances survive the klir restart.
    _save_server_state(orch._paths)

    killed = await orch._process_registry.kill_all_active()
    if killed:
        logger.info("Shutdown terminated %d active CLI process(es)", killed)

    # Reset Tailscale exposure before stopping the API server
    ts_cfg = orch._config.api.tailscale
    if ts_cfg.mode != "off" and ts_cfg.reset_on_exit:
        try:
            from klir.infra.tailscale import stop_tailscale_exposure

            await stop_tailscale_exposure(ts_cfg.mode)
        except Exception:
            logger.exception("Failed to reset Tailscale exposure during shutdown")

    if orch._api_stop is not None:
        await orch._api_stop()
    try:
        orch._memory_store.close()
    except Exception:
        logger.exception("Failed to close memory store")
    await asyncio.to_thread(cleanup_klir_links, orch._paths)
    await orch._observers.stop_all()
    try:
        await orch.db.close()
    except Exception:
        logger.exception("Failed to close database")
    logger.info("Orchestrator shutdown")


async def _restore_server_state(paths: KlirPaths) -> None:
    """Restore previously-saved ``opencode serve`` instances on startup.

    Loads the saved state from disk and calls
    :meth:`OpenCodeServerManager.restore_from_state`.  Each recorded
    instance is health-checked: alive instances are re-attached (without
    a process handle), dead instances are re-created.
    """
    from klir.cli.server_manager import get_server_manager
    from klir.infra.json_store import load_json

    state = load_json(paths.server_state_path)
    if not state:
        logger.debug("No saved server state to restore")
        return

    mgr = get_server_manager()
    logger.info("Restoring %d server instance(s) from saved state", len(state))
    await mgr.restore_from_state(state)


def _save_server_state(paths: KlirPaths) -> None:
    """Persist ``opencode serve`` state for next startup recovery.

    Calls :meth:`OpenCodeServerManager.save_state` and writes the
    resulting dict to an atomic JSON file.
    """
    from klir.cli.server_manager import get_server_manager
    from klir.infra.json_store import atomic_json_save

    mgr = get_server_manager()
    state = mgr.save_state()
    if state:
        atomic_json_save(paths.server_state_path, state)
        logger.info("Saved server state: %d instance(s)", len(state))
    else:
        logger.debug("No server instances to save")
