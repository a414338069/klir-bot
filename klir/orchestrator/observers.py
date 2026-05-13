"""Observer lifecycle management for the Orchestrator.

Consolidates creation, start, and teardown of all background observers
(cron, webhook, heartbeat, cleanup, model caches, config reloader,
rule/skill sync watchers) into a single manager.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from klir.background import BackgroundObserver, BackgroundResult

if TYPE_CHECKING:
    from klir.bus.bus import MessageBus
    from klir.history.store import MessageHistory
from klir.cleanup import CleanupObserver
from klir.cli.codex_cache import CodexModelCache
from klir.cli.codex_cache_observer import CodexCacheObserver
from klir.cli.gemini_cache_observer import GeminiCacheObserver
from klir.cli.service import CLIService
from klir.config import AgentConfig, get_gemini_models
from klir.config_reload import ConfigReloader
from klir.cron.manager import CronManager
from klir.cron.observer import CronObserver
from klir.heartbeat import HeartbeatObserver
from klir.infra.db import KlirDB
from klir.webhook.manager import WebhookManager
from klir.webhook.models import WebhookResult
from klir.webhook.observer import WebhookObserver
from klir.workspace.init import watch_rule_files
from klir.workspace.paths import KlirPaths
from klir.workspace.skill_sync import watch_skill_sync

logger = logging.getLogger(__name__)


class ObserverManager:
    """Owns all background observers and manages their lifecycle."""

    def __init__(
        self,
        config: AgentConfig,
        paths: KlirPaths,
        db: KlirDB,
        *,
        message_history: MessageHistory | None = None,
    ) -> None:
        self._config = config
        self._paths = paths
        self.heartbeat = HeartbeatObserver(config)
        self.cleanup = CleanupObserver(config, paths, db, message_history=message_history)

        self.cron: CronObserver | None = None
        self.webhook: WebhookObserver | None = None
        self.background: BackgroundObserver | None = None
        self.codex_cache: CodexModelCache | None = None
        self.codex_cache_obs: CodexCacheObserver | None = None
        self.gemini_cache_obs: GeminiCacheObserver | None = None

        self._config_reloader: ConfigReloader | None = None
        self._rule_sync_task: asyncio.Task[None] | None = None
        self._skill_sync_task: asyncio.Task[None] | None = None
        self._reconcile_task: asyncio.Task[None] | None = None

    # -- Model cache initialization -------------------------------------------

    async def init_model_caches(
        self,
        *,
        on_gemini_refresh: Callable[[tuple[str, ...]], None],
    ) -> CodexModelCache:
        """Start Gemini and Codex cache observers, return Codex cache."""
        # Gemini
        gemini_cache_path = self._paths.config_path.parent / "gemini_models.json"
        gemini_observer = GeminiCacheObserver(gemini_cache_path, on_refresh=on_gemini_refresh)
        await gemini_observer.start()
        self.gemini_cache_obs = gemini_observer

        if not get_gemini_models():
            logger.warning("Gemini cache is empty after startup (Gemini may not be installed)")

        # Codex
        codex_cache_path = self._paths.config_path.parent / "codex_models.json"
        codex_observer = CodexCacheObserver(codex_cache_path)
        await codex_observer.start()
        self.codex_cache_obs = codex_observer
        codex_cache = codex_observer.get_cache()

        if not codex_cache or not codex_cache.models:
            logger.warning("Codex cache is empty after startup (Codex may not be authenticated)")

        return codex_cache or CodexModelCache("", [])

    # -- Task observer initialization -----------------------------------------

    def init_task_observers(
        self,
        *,
        cron_manager: CronManager,
        webhook_manager: WebhookManager,
        cli_service: CLIService,
        codex_cache: CodexModelCache,
        db: KlirDB,
    ) -> None:
        """Create Background, Cron, and Webhook observers (after caches are ready)."""
        config, paths = self._config, self._paths
        self.codex_cache = codex_cache
        self.background = BackgroundObserver(
            paths, timeout_seconds=config.timeouts.background, cli_service=cli_service
        )
        self.cron = CronObserver(paths, cron_manager, config=config, codex_cache=codex_cache, db=db)
        self.webhook = WebhookObserver(
            paths, webhook_manager, config=config, codex_cache=codex_cache
        )

    # -- Start / stop ---------------------------------------------------------

    async def start_all(self) -> None:
        """Start all observers and background watchers."""
        if self.cron:
            await self.cron.start()
        await self.heartbeat.start()
        if self.webhook:
            await self.webhook.start()
        await self.cleanup.start()

        self._rule_sync_task = asyncio.create_task(watch_rule_files(self._paths.workspace))
        logger.info("Rule file watcher started (CLAUDE.md <-> AGENTS.md <-> GEMINI.md)")

        self._skill_sync_task = asyncio.create_task(watch_skill_sync(self._paths))
        logger.info("Skill sync watcher started")

    async def start_config_reloader(
        self,
        *,
        on_hot_reload: Callable[[AgentConfig, dict[str, object]], None],
        on_restart_needed: Callable[[list[str]], None],
    ) -> None:
        """Start the config file watcher."""
        original_on_hot_reload = on_hot_reload

        def _on_hot_reload_extended(config: AgentConfig, hot_fields: dict[str, object]) -> None:
            original_on_hot_reload(config, hot_fields)
            if "heartbeat" in hot_fields:
                self._reconcile_task = asyncio.create_task(self.heartbeat.reconcile_target_tasks())

        self._config_reloader = ConfigReloader(
            self._paths.config_path,
            self._config,
            on_hot_reload=_on_hot_reload_extended,
            on_restart_needed=on_restart_needed,
        )
        await self._config_reloader.start()

    async def stop_all(self) -> None:
        """Stop all background observers and caches."""
        if self._config_reloader:
            await self._config_reloader.stop()
        if self.background:
            await self.background.shutdown()
        await self.heartbeat.stop()
        if self.webhook:
            await self.webhook.stop()
        if self.cron:
            await self.cron.stop()
        await self.cleanup.stop()
        if self.codex_cache_obs:
            await self.codex_cache_obs.stop()
            self.codex_cache_obs = None
        if self.gemini_cache_obs:
            await self.gemini_cache_obs.stop()
            self.gemini_cache_obs = None
        for task in (self._rule_sync_task, self._skill_sync_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # -- Chat validation -------------------------------------------------------

    def set_heartbeat_chat_validator(
        self,
        bot_instance: Any,
    ) -> None:
        """Wire chat validation using the bot instance for get_chat."""

        async def _validate(chat_id: int) -> bool:
            try:
                await bot_instance.get_chat(chat_id)
            except Exception:
                return False
            else:
                return True

        self.heartbeat.set_chat_validator(_validate)

    # -- Bus wiring (single entry point) --------------------------------------

    def wire_to_bus(
        self,
        bus: MessageBus,
        *,
        wake_handler: Callable[[int, str], Awaitable[str | None]] | None = None,
    ) -> None:
        """Wire all observer result callbacks to the message bus.

        Replaces the five individual setter methods with a single call.
        """
        from klir.bus.adapters import (
            from_background_result,
            from_cron_result,
            from_heartbeat,
            from_webhook_cron_result,
        )

        if self.cron:

            async def _on_cron(  # noqa: PLR0913
                title: str,
                result: str,
                status: str,
                routing_chat_id: int | None,
                routing_topic_id: int | None,
                routing_transport: str | None,
            ) -> None:
                await bus.submit(
                    from_cron_result(
                        title,
                        result,
                        status,
                        routing_chat_id,
                        routing_topic_id,
                        routing_transport,
                    )
                )

            self.cron.set_result_handler(_on_cron)

        async def _on_heartbeat(chat_id: int, text: str, topic_id: int | None = None) -> None:
            await bus.submit(from_heartbeat(chat_id, text, topic_id))

        self.heartbeat.set_result_handler(_on_heartbeat)

        if self.background:

            async def _on_bg(result: BackgroundResult) -> None:
                await bus.submit(from_background_result(result))

            self.background.set_result_handler(_on_bg)

        if self.webhook:

            async def _on_webhook(result: WebhookResult) -> None:
                if result.mode != "wake":
                    await bus.submit(from_webhook_cron_result(result))

            self.webhook.set_result_handler(_on_webhook)
            if wake_handler:
                self.webhook.set_wake_handler(wake_handler)
