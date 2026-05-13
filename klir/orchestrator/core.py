"""Core orchestrator: routes messages through command and conversation flows."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from klir.background import (
    BackgroundSubmit,
    BackgroundTask,
)
from klir.cli.process_registry import ProcessRegistry
from klir.cli.service import CLIService, CLIServiceConfig
from klir.config import AgentConfig
from klir.config_resolver import ChatConfigResolver
from klir.cron.manager import CronManager
from klir.errors import (
    CLIError,
    CronError,
    SessionError,
    StreamError,
    WebhookError,
    WorkspaceError,
)
from klir.history import MessageHistory
from klir.infra.db import KlirDB
from klir.infra.inflight import InflightTracker
from klir.memory.files import MemoryFileManager
from klir.memory.store import MemoryStore
from klir.orchestrator.commands import (
    cmd_agents,
    cmd_compact,
    cmd_cron,
    cmd_cwd,
    cmd_diagnose,
    cmd_hooks,
    cmd_mcp,
    cmd_memory,
    cmd_model,
    cmd_reset,
    cmd_sessions,
    cmd_skills,
    cmd_status,
    cmd_tasks,
    cmd_think,
    cmd_update_plugins,
    cmd_upgrade,
)
from klir.orchestrator.directives import parse_directives
from klir.orchestrator.flows import (
    StreamingCallbacks,
    heartbeat_flow,
    named_session_flow,
    named_session_streaming,
    normal,
    normal_streaming,
)
from klir.orchestrator.hooks import (
    DELEGATION_BRIEF,
    DELEGATION_REMINDER,
    MAINMEMORY_REMINDER,
    MessageHookRegistry,
)
from klir.orchestrator.observers import ObserverManager
from klir.orchestrator.providers import ProviderManager
from klir.orchestrator.registry import CommandRegistry, OrchestratorResult
from klir.orchestrator.user_hooks import UserHookEvaluator
from klir.security import detect_suspicious_patterns
from klir.session import SessionKey, SessionManager
from klir.session.manager import SessionData
from klir.session.named import NamedSessionRegistry
from klir.webhook.manager import WebhookManager
from klir.workspace.paths import KlirPaths

if TYPE_CHECKING:
    from klir.api.dashboard import DashboardHub
    from klir.background import BackgroundObserver
    from klir.bus.bus import MessageBus
    from klir.bus.envelope import Envelope
    from klir.cli.tool_activity import ToolActivity
    from klir.config import ModelRegistry
    from klir.multiagent.bus import AsyncInterAgentResult
    from klir.multiagent.supervisor import AgentSupervisor
    from klir.session.named import NamedSession
    from klir.tasks.hub import TaskHub

logger = logging.getLogger(__name__)


_TextCallback = Callable[[str], Awaitable[None]]
_SystemStatusCallback = Callable[[str | None], Awaitable[None]]


@dataclass(slots=True)
class NamedSessionRequest:
    """Parameters for submitting a named background session."""

    message_id: int
    thread_id: int | None
    provider_override: str | None = None
    model_override: str | None = None


@dataclass(slots=True)
class _MessageDispatch:
    """Normalized input for one orchestrator message routing pass."""

    key: SessionKey
    text: str
    cmd: str
    streaming: bool = False
    on_text_delta: _TextCallback | None = None
    on_tool_activity: Callable[[ToolActivity], Awaitable[None]] | None = None
    on_system_status: _SystemStatusCallback | None = None

    def streaming_callbacks(self) -> StreamingCallbacks:
        """Bundle the streaming callbacks into a StreamingCallbacks instance."""
        return StreamingCallbacks(
            on_text_delta=self.on_text_delta,
            on_tool_activity=self.on_tool_activity,
            on_system_status=self.on_system_status,
        )


class Orchestrator:
    """Routes messages through command dispatch and conversation flows."""

    def __init__(
        self,
        config: AgentConfig,
        paths: KlirPaths,
        *,
        agent_name: str = "main",
        interagent_port: int = 8799,
    ) -> None:
        self._config = config
        self._paths: KlirPaths = paths
        self._resolver = ChatConfigResolver(config)
        self._providers = ProviderManager(config)
        self._db = KlirDB(paths.db_path)
        self._sessions = SessionManager(self._db, config)
        self._named_sessions = NamedSessionRegistry(paths.named_sessions_path)
        self._process_registry = ProcessRegistry()
        self._cwd_override: str | None = None  # Set by /cwd command
        self._cli_service = CLIService(
            config=CLIServiceConfig(
                working_dir=self.effective_working_dir,
                default_model=config.model,
                provider=config.provider,
                max_turns=config.max_turns,
                max_budget_usd=config.max_budget_usd,
                permission_mode=config.permission_mode,
                reasoning_effort=config.reasoning_effort,
                gemini_api_key=config.gemini_api_key,
                claude_cli_parameters=tuple(config.cli_parameters.claude),
                codex_cli_parameters=tuple(config.cli_parameters.codex),
                gemini_cli_parameters=tuple(config.cli_parameters.gemini),
                opencode_cli_parameters=tuple(config.cli_parameters.opencode),
                http_cli_parameters=tuple(config.cli_parameters.http),
                allowed_tools=tuple(config.allowed_tools),
                disallowed_tools=tuple(config.disallowed_tools),
                tool_loop_threshold=config.tool_loop_threshold,
                agent_name=agent_name,
                interagent_port=interagent_port,
            ),
            models=self._providers.models,
            available_providers=frozenset(),
            process_registry=self._process_registry,
        )
        self._message_history = MessageHistory(self._db)
        self._cron_manager = CronManager(jobs_path=paths.cron_jobs_path)
        self._webhook_manager = WebhookManager(hooks_path=paths.webhooks_path)
        self._observers = ObserverManager(
            config, paths, self._db, message_history=self._message_history
        )

        async def _hb_handler(
            chat_id: int,
            topic_id: int | None = None,
            prompt_override: str | None = None,
        ) -> str | None:
            return await self.handle_heartbeat(
                SessionKey(chat_id=chat_id, topic_id=topic_id),
                prompt_override=prompt_override,
            )

        self._observers.heartbeat.set_heartbeat_handler(_hb_handler)
        self._observers.heartbeat.set_busy_check(self._process_registry.has_active)
        stale_max = config.cli_timeout * 2
        self._observers.heartbeat.set_stale_cleanup(
            lambda: self._process_registry.kill_stale(stale_max)
        )
        self._api_stop: Callable[[], Awaitable[None]] | None = None
        self._dashboard_hub: DashboardHub | None = None
        self._inflight_tracker = InflightTracker(paths.inflight_turns_path)
        self._hook_registry = MessageHookRegistry()
        self._hook_registry.register(MAINMEMORY_REMINDER)
        self._hook_registry.register(DELEGATION_BRIEF)
        self._hook_registry.register(DELEGATION_REMINDER)
        self._memory_store = MemoryStore(paths.memory_index_path)
        self._memory_files = MemoryFileManager(paths.memory_system_dir, self._memory_store)
        self._user_hooks = UserHookEvaluator(config.message_hooks)
        self._supervisor: AgentSupervisor | None = None  # Set by AgentSupervisor after creation
        self._task_hub: TaskHub | None = None  # Set by supervisor or __main__.py
        self._http_agent_name: str = "main"  # Agent selected via /agents for opencode_http
        self._command_registry = CommandRegistry()
        self._register_commands()

    @property
    def paths(self) -> KlirPaths:
        """Public access to resolved workspace paths."""
        return self._paths

    @property
    def db(self) -> KlirDB:
        """Public access to the SQLite database."""
        return self._db

    @property
    def message_history(self) -> MessageHistory:
        """Public access to the message history store."""
        return self._message_history

    @property
    def effective_working_dir(self) -> str:
        """Return the cwd for AI subprocesses (override > config > default workspace)."""
        return (
            self._cwd_override
            or self._config.working_dir
            or str(self._paths.workspace)
        )

    @property
    def task_hub(self) -> TaskHub | None:
        """Public access to the task hub (None when tasks are disabled)."""
        return self._task_hub

    @property
    def config(self) -> AgentConfig:
        """Public access to the agent config."""
        return self._config

    @property
    def resolver(self) -> ChatConfigResolver:
        """Public access to the per-chat config resolver."""
        return self._resolver

    @property
    def inflight_tracker(self) -> InflightTracker:
        """Public access to the inflight turn tracker."""
        return self._inflight_tracker

    @property
    def named_sessions(self) -> NamedSessionRegistry:
        """Public access to the named session registry."""
        return self._named_sessions

    @property
    def memory_store(self) -> MemoryStore:
        """Public access to the memory store."""
        return self._memory_store

    @property
    def memory_files(self) -> MemoryFileManager:
        """Public access to the memory file manager."""
        return self._memory_files

    @property
    def available_providers(self) -> frozenset[str]:
        """Public access to the set of authenticated providers."""
        return self._providers.available_providers

    @property
    def cli_service(self) -> CLIService:
        """Public access to the CLI service."""
        return self._cli_service

    @property
    def process_registry(self) -> ProcessRegistry:
        """Public access to the process registry."""
        return self._process_registry

    @property
    def bg_observer(self) -> BackgroundObserver | None:
        """Public access to the background observer."""
        return self._observers.background

    @property
    def supervisor(self) -> AgentSupervisor | None:
        """Public access to the agent supervisor."""
        return self._supervisor

    @supervisor.setter
    def supervisor(self, value: AgentSupervisor | None) -> None:
        self._supervisor = value

    def set_task_hub(self, hub: TaskHub) -> None:
        """Inject the task hub (called by supervisor or startup wiring)."""
        self._task_hub = hub
        hub.start_maintenance()

    @classmethod
    async def create(
        cls,
        config: AgentConfig,
        *,
        agent_name: str = "main",
    ) -> Orchestrator:
        """Async factory: build Orchestrator.

        Workspace must already be initialized by the caller (``__main__.load_config``).
        """
        from klir.orchestrator.lifecycle import create_orchestrator

        return await create_orchestrator(config, agent_name=agent_name)

    @property
    def models(self) -> ModelRegistry:
        """Public access to the model registry (delegates to ProviderManager)."""
        return self._providers.models

    @property
    def gemini_api_key_mode(self) -> bool:
        """Return cached Gemini API-key mode status."""
        return self._providers.gemini_api_key_mode

    @property
    def active_provider_name(self) -> str:
        """Human-readable name for the active CLI provider."""
        return self._providers.active_provider_name

    async def handle_message(self, key: SessionKey, text: str) -> OrchestratorResult:
        """Main entry point: route message to appropriate handler."""
        dispatch = _MessageDispatch(key=key, text=text, cmd=text.strip().lower())
        return await self._handle_message_impl(dispatch)

    async def handle_message_streaming(
        self,
        key: SessionKey,
        text: str,
        *,
        on_text_delta: _TextCallback | None = None,
        on_tool_activity: Callable[[ToolActivity], Awaitable[None]] | None = None,
        on_system_status: _SystemStatusCallback | None = None,
    ) -> OrchestratorResult:
        """Main entry point with streaming support."""
        dispatch = _MessageDispatch(
            key=key,
            text=text,
            cmd=text.strip().lower(),
            streaming=True,
            on_text_delta=on_text_delta,
            on_tool_activity=on_tool_activity,
            on_system_status=on_system_status,
        )
        return await self._handle_message_impl(dispatch)

    async def _handle_message_impl(self, dispatch: _MessageDispatch) -> OrchestratorResult:
        self._process_registry.clear_abort(dispatch.key.chat_id)
        logger.info("Message received text=%s", dispatch.cmd[:80])

        patterns = detect_suspicious_patterns(dispatch.text)
        if patterns:
            logger.warning("Suspicious input patterns: %s", ", ".join(patterns))

        try:
            return await self._route_message(dispatch)
        except asyncio.CancelledError:
            raise
        except (CLIError, StreamError, SessionError, CronError, WebhookError, WorkspaceError):
            logger.exception("Domain error in handle_message")
            return OrchestratorResult(text="An internal error occurred. Please try again.")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
            logger.exception("Unexpected error in handle_message")
            return OrchestratorResult(text="An internal error occurred. Please try again.")

    async def _route_message(self, dispatch: _MessageDispatch) -> OrchestratorResult:
        result = await self._command_registry.dispatch(
            dispatch.cmd,
            self,
            dispatch.key,
            dispatch.text,
        )
        if result is not None:
            return result

        # Telegram commands can't contain hyphens, so skill names like
        # "dispatching-parallel-agents" become "/dispatching_parallel_agents"
        # in the menu.  Restore hyphens so the CLI can find the skill.
        text = dispatch.text
        if text.startswith("/"):
            parts = text.split(None, 1)
            restored = parts[0].replace("_", "-")
            if restored != parts[0]:
                text = restored if len(parts) == 1 else f"{restored} {parts[1]}"
                dispatch = replace(dispatch, text=text)

        directives = parse_directives(dispatch.text, self._providers._known_model_ids)

        # Check if a leading @directive matches a named session
        if directives.raw_directives:
            first_key = next(iter(directives.raw_directives))
            ns = self._named_sessions.get(dispatch.key.chat_id, first_key)
            if ns is not None:
                session_prompt = directives.cleaned or dispatch.text
                if dispatch.streaming:
                    return await named_session_streaming(
                        self,
                        dispatch.key,
                        first_key,
                        session_prompt,
                        cbs=dispatch.streaming_callbacks(),
                    )
                return await named_session_flow(self, dispatch.key, first_key, session_prompt)

        if directives.is_directive_only and directives.has_model:
            return OrchestratorResult(
                text=f"Next message will use: {directives.model}\n"
                f"(Send a message with @{directives.model} <text> to use it.)",
            )

        prompt_text = directives.cleaned or dispatch.text

        if dispatch.streaming:
            return await normal_streaming(
                self,
                dispatch.key,
                prompt_text,
                model_override=directives.model,
                cbs=dispatch.streaming_callbacks(),
            )

        return await normal(
            self,
            dispatch.key,
            prompt_text,
            model_override=directives.model,
        )

    def _register_commands(self) -> None:
        reg = self._command_registry
        reg.register_async("/new", cmd_reset)
        reg.register_async("/compact", cmd_compact)
        # Both entries are intentional: "/think" (exact) handles the bare
        # command, "/think " (trailing space, prefix match) handles
        # "/think high" etc. — same pattern as "/model" + "/model ".
        reg.register_async("/think", cmd_think)
        reg.register_async("/think ", cmd_think)
        # /stop is handled entirely by the Middleware abort path (before the lock)
        # and never reaches the orchestrator command registry.
        reg.register_async("/status", cmd_status)
        reg.register_async("/model", cmd_model)
        reg.register_async("/model ", cmd_model)
        reg.register_async("/memory", cmd_memory)
        reg.register_async("/cron", cmd_cron)
        reg.register_async("/diagnose", cmd_diagnose)
        reg.register_async("/upgrade", cmd_upgrade)
        reg.register_async("/sessions", cmd_sessions)
        reg.register_async("/tasks", cmd_tasks)
        reg.register_async("/hooks", cmd_hooks)
        reg.register_async("/cwd", cmd_cwd)
        reg.register_async("/cwd ", cmd_cwd)
        reg.register_async("/update_plugins", cmd_update_plugins)
        # opencode_http provider commands — check provider internally and
        # fall back to multi-agent / named-session delegates when applicable.
        reg.register_async("/agents", cmd_agents)
        reg.register_async("/skills", cmd_skills)
        reg.register_async("/mcp", cmd_mcp)

    def _rebuild_user_hooks(self) -> None:
        """Rebuild user hook evaluator from current config (hot-reload)."""
        self._user_hooks = UserHookEvaluator(self._config.message_hooks)

    def register_multiagent_commands(self) -> None:
        """Register /agents, /agent_start, /agent_stop, /agent_restart commands.

        Called by the AgentSupervisor after setting ``_supervisor``.
        """
        from klir.multiagent.commands import (
            cmd_agent_restart,
            cmd_agent_start,
            cmd_agent_stop,
            cmd_agents,
        )

        reg = self._command_registry
        reg.register_async("/agents", cmd_agents)
        reg.register_async("/agent_start", cmd_agent_start)
        reg.register_async("/agent_start ", cmd_agent_start)
        reg.register_async("/agent_stop", cmd_agent_stop)
        reg.register_async("/agent_stop ", cmd_agent_stop)
        reg.register_async("/agent_restart", cmd_agent_restart)
        reg.register_async("/agent_restart ", cmd_agent_restart)
        logger.info("Multi-agent commands registered")

    async def reset_session(self, key: SessionKey) -> None:
        """Reset the session for a given key."""
        await self._sessions.reset_session(key)
        logger.info("Session reset")

    async def reset_active_provider_session(self, key: SessionKey) -> str:
        """Reset only the active provider session bucket for a given key."""
        active = await self._sessions.get_active(key)
        if active is not None:
            provider = active.provider
            model = active.model
        else:
            model, provider = self.resolve_runtime_target(self._config.model)

        await self._sessions.reset_provider_session(
            key,
            provider=provider,
            model=model,
        )
        logger.info("Active provider session reset provider=%s", provider)
        return provider

    async def abort(self, chat_id: int) -> int:
        """Kill all active CLI processes and background tasks for chat_id."""
        killed = await self._process_registry.kill_all(chat_id)
        if self._observers.background:
            killed += await self._observers.background.cancel_all(chat_id)
        self._named_sessions.end_all(chat_id)
        return killed

    def interrupt(self, chat_id: int) -> int:
        """Send SIGINT to active CLI processes for *chat_id*.

        Unlike :meth:`abort` this does not kill or unregister the processes.
        It sends a soft interrupt so the CLI can cancel the current tool
        execution (equivalent to pressing ESC in the terminal).
        """
        return self._process_registry.interrupt_all(chat_id)

    def resolve_runtime_target(self, requested_model: str | None = None) -> tuple[str, str]:
        """Resolve requested model to the effective ``(model, provider)`` pair."""
        return self._providers.resolve_runtime_target(requested_model)

    def wire_observers_to_bus(
        self,
        bus: MessageBus,
        *,
        wake_handler: Callable[[int, str], Awaitable[str | None]] | None = None,
    ) -> None:
        """Wire all observer result callbacks to the message bus."""
        self._observers.wire_to_bus(bus, wake_handler=wake_handler)
        bus.set_injector(self)

        history = self._message_history

        async def _audit_record_outbound(envelope: Envelope) -> None:
            if envelope.result_text:
                try:
                    await history.record_outbound(envelope)
                except Exception:
                    logger.exception("Failed to record outbound message")

        bus.set_audit_hook(_audit_record_outbound)

    async def handle_heartbeat(
        self, key: SessionKey, *, prompt_override: str | None = None
    ) -> str | None:
        """Run a heartbeat turn in the main session. Returns alert text or None."""
        logger.debug("Heartbeat flow starting")
        return await heartbeat_flow(self, key, prompt_override=prompt_override)

    def submit_named_session(
        self,
        chat_id: int,
        prompt: str,
        request: NamedSessionRequest,
    ) -> tuple[str, str]:
        """Submit a new named background session. Returns (task_id, session_name)."""
        from klir.cli.param_resolver import resolve_cli_config

        if self._observers.background is None:
            msg = "Background observer not initialized"
            raise RuntimeError(msg)

        model_name, provider_name = self.resolve_runtime_target(self._config.model)
        if request.provider_override:
            provider_name = request.provider_override
            model_name = request.model_override or self.default_model_for_provider(
                request.provider_override
            )

        ns = self._named_sessions.create(chat_id, provider_name, model_name, prompt)
        exec_config = resolve_cli_config(self._config, self._observers.codex_cache)
        sub = BackgroundSubmit(
            chat_id=chat_id,
            prompt=prompt,
            message_id=request.message_id,
            thread_id=request.thread_id,
            session_name=ns.name,
            provider_override=provider_name,
            model_override=model_name,
        )
        task_id = self._observers.background.submit(sub, exec_config)
        return task_id, ns.name

    def submit_named_followup_bg(
        self,
        chat_id: int,
        session_name: str,
        prompt: str,
        message_id: int,
        thread_id: int | None,
    ) -> str:
        """Submit a background follow-up to an existing named session. Returns task_id."""
        from klir.cli.param_resolver import resolve_cli_config

        if self._observers.background is None:
            msg = "Background observer not initialized"
            raise RuntimeError(msg)

        ns = self._named_sessions.get(chat_id, session_name)
        if ns is None:
            msg = f"Session '{session_name}' not found"
            raise ValueError(msg)
        if ns.status == "ended":
            msg = f"Session '{session_name}' has ended"
            raise ValueError(msg)
        if ns.status == "running":
            msg = f"Session '{session_name}' is still processing"
            raise ValueError(msg)

        self._named_sessions.mark_running(chat_id, session_name, prompt)
        exec_config = resolve_cli_config(self._config, self._observers.codex_cache)
        sub = BackgroundSubmit(
            chat_id=chat_id,
            prompt=prompt,
            message_id=message_id,
            thread_id=thread_id,
            session_name=session_name,
            resume_session_id=ns.session_id,
            provider_override=ns.provider,
            model_override=ns.model,
        )
        return self._observers.background.submit(sub, exec_config)

    async def end_named_session(self, chat_id: int, name: str) -> bool:
        """Kill process and end a named session."""
        ns = self._named_sessions.get(chat_id, name)
        if ns is None:
            return False
        await self._process_registry.kill_by_label(chat_id, f"ns:{name}")
        self._process_registry.clear_label_abort(chat_id, f"ns:{name}")
        return self._named_sessions.end_session(chat_id, name)

    def is_known_model(self, candidate: str) -> bool:
        """Return True if *candidate* is a recognized model ID for any provider."""
        return self._providers.is_known_model(candidate)

    def default_model_for_provider(self, provider: str) -> str:
        """Return the default model ID for a provider, or empty string if unknown."""
        return self._providers.default_model_for_provider(provider)

    def resolve_session_directive(self, key: str) -> tuple[str, str] | None:
        """Resolve a ``@key`` directive to ``(provider, model)`` or ``None``."""
        return self._providers.resolve_session_directive(key)

    def get_named_session(self, chat_id: int, name: str) -> NamedSession | None:
        """Look up a named session."""
        return self._named_sessions.get(chat_id, name)

    def list_named_sessions(self, chat_id: int) -> list[NamedSession]:
        """List active named sessions for a chat."""
        return self._named_sessions.list_active(chat_id)

    async def list_topic_sessions(self, chat_id: int) -> list[SessionData]:
        """Return fresh topic sessions for *chat_id*."""
        all_sessions = await self._sessions.list_active_for_chat(chat_id)
        return [s for s in all_sessions if s.topic_id is not None]

    def active_background_tasks(self, chat_id: int | None = None) -> list[BackgroundTask]:
        """Return active background tasks, optionally filtered by chat_id."""
        if self._observers.background is None:
            return []
        return self._observers.background.active_tasks(chat_id)

    def is_chat_busy(self, chat_id: int) -> bool:
        """Check if a chat has active CLI processes."""
        return self._process_registry.has_active(chat_id)

    def set_config_hot_reload_handler(
        self,
        handler: Callable[[AgentConfig, dict[str, object]], None],
    ) -> None:
        """Register an external hot-reload callback (e.g. TelegramBot auth update)."""
        self._config_hot_reload_handler = handler

    def _on_config_hot_reload(self, config: AgentConfig, hot: dict[str, object]) -> None:
        """Apply hot-reloaded config fields to dependent services."""
        if any(
            k in hot
            for k in (
                "model",
                "provider",
                "max_turns",
                "max_budget_usd",
                "permission_mode",
                "reasoning_effort",
                "cli_parameters",
                "allowed_tools",
                "disallowed_tools",
                "tool_loop_threshold",
            )
        ):
            self._cli_service.update_config(
                CLIServiceConfig(
                    working_dir=self.effective_working_dir,
                    default_model=config.model,
                    provider=config.provider,
                    max_turns=config.max_turns,
                    max_budget_usd=config.max_budget_usd,
                    permission_mode=config.permission_mode,
                    reasoning_effort=config.reasoning_effort,
                    gemini_api_key=config.gemini_api_key,
                    claude_cli_parameters=tuple(config.cli_parameters.claude),
                    codex_cli_parameters=tuple(config.cli_parameters.codex),
                    gemini_cli_parameters=tuple(config.cli_parameters.gemini),
                    opencode_cli_parameters=tuple(config.cli_parameters.opencode),
                    http_cli_parameters=tuple(config.cli_parameters.http),
                    allowed_tools=tuple(config.allowed_tools),
                    disallowed_tools=tuple(config.disallowed_tools),
                    tool_loop_threshold=config.tool_loop_threshold,
                )
            )

        if "model" in hot:
            self._providers.refresh_known_model_ids()

        if "chat_overrides" in hot:
            self._resolver.reload(config)

        if "message_hooks" in hot:
            self._rebuild_user_hooks()

        if "language" in hot:
            from klir.i18n import load_translations

            load_translations(config.language)
            logger.info("i18n: switched to language %r", config.language)

        handler = getattr(self, "_config_hot_reload_handler", None)
        if handler is not None:
            handler(config, hot)

        logger.info("Hot-reload applied to orchestrator services")

    # -- Inter-agent communication ------------------------------------------

    async def handle_interagent_message(
        self,
        sender: str,
        message: str,
        *,
        new_session: bool = False,
    ) -> tuple[str, str, str]:
        """Process a message from another agent via the InterAgentBus."""
        from klir.orchestrator.injection import (
            handle_interagent_message as _handle_ia,
        )

        return await _handle_ia(self, sender, message, new_session=new_session)

    async def handle_async_interagent_result(
        self,
        result: AsyncInterAgentResult,
        *,
        chat_id: int = 0,
    ) -> str:
        """Inject an async inter-agent result into the current active session."""
        from klir.orchestrator.injection import (
            handle_async_interagent_result as _handle_async_ia,
        )

        return await _handle_async_ia(self, result, chat_id=chat_id)

    async def inject_prompt(
        self,
        prompt: str,
        chat_id: int,
        label: str,
        *,
        topic_id: int | None = None,
    ) -> str:
        """Execute *prompt* in the active session (fulfils ``SessionInjector`` protocol)."""
        from klir.orchestrator.injection import _inject_prompt

        return await _inject_prompt(self, prompt, chat_id, label, topic_id=topic_id)

    async def shutdown(self) -> None:
        """Cleanup on bot shutdown."""
        from klir.orchestrator.lifecycle import shutdown

        await shutdown(self)
