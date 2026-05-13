"""Telegram command handlers for multi-agent management.

Registered only on the main agent's Orchestrator when a supervisor is present.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from klir.orchestrator.registry import OrchestratorResult
from klir.text.response_format import SEP, fmt

if TYPE_CHECKING:
    from klir.orchestrator.core import Orchestrator
    from klir.session.key import SessionKey

logger = logging.getLogger(__name__)

_STATUS_EMOJI = {
    "running": "●",
    "starting": "◐",
    "crashed": "✖",
    "stopped": "○",
}


async def cmd_agents(orch: Orchestrator, _key: SessionKey, _text: str) -> OrchestratorResult:
    """Handle /agents: list all agents with status."""
    supervisor = orch.supervisor
    if supervisor is None:
        return OrchestratorResult(text="Multi-agent mode is not active.")

    lines: list[str] = []
    for name in sorted(supervisor.health.keys()):
        health = supervisor.health[name]
        stack = supervisor.stacks.get(name)
        emoji = _STATUS_EMOJI.get(health.status, "?")
        role = "main" if (stack and stack.is_main) else "sub"

        info = f"  {emoji} **{name}** [{role}] — {health.status}"
        if health.status == "running" and health.uptime_human:
            info += f" ({health.uptime_human})"
        if stack:
            model_label = stack.config.model
            effort = stack.config.reasoning_effort
            if effort:
                model_label += f" ({effort})"
            info += f" | {model_label}"
        if health.restart_count > 0:
            info += f" (restarts: {health.restart_count})"
        if health.status == "crashed" and health.last_crash_error:
            info += f"\n      Error: {health.last_crash_error[:100]}"
        lines.append(info)

    if not lines:
        return OrchestratorResult(text=fmt("**Agents**", SEP, "No agents."))

    return OrchestratorResult(text=fmt("**Agents**", SEP, "\n".join(lines)))


async def cmd_agent_stop(orch: Orchestrator, _key: SessionKey, text: str) -> OrchestratorResult:
    """Handle /agent_stop <name>: stop a sub-agent."""
    supervisor = orch.supervisor
    if supervisor is None:
        return OrchestratorResult(text="Multi-agent mode is not active.")

    parts = text.split(None, 1)
    if len(parts) < 2:
        return OrchestratorResult(text="Usage: /agent_stop <name>")

    name = parts[1].strip().lower()
    if name == "main":
        return OrchestratorResult(text="Cannot stop the main agent.")

    if name not in supervisor.stacks:
        return OrchestratorResult(text=f"Agent '{name}' is not running.")

    await supervisor.stop_agent(name)
    return OrchestratorResult(text=f"Agent '{name}' stopped.")


async def cmd_agent_start(orch: Orchestrator, _key: SessionKey, text: str) -> OrchestratorResult:
    """Handle /agent_start <name>: start a sub-agent from the registry."""
    supervisor = orch.supervisor
    if supervisor is None:
        return OrchestratorResult(text="Multi-agent mode is not active.")

    parts = text.split(None, 1)
    if len(parts) < 2:
        return OrchestratorResult(text="Usage: /agent_start <name>")

    name = parts[1].strip().lower()
    result = await supervisor.start_agent_by_name(name)
    return OrchestratorResult(text=result)


async def cmd_agent_restart(orch: Orchestrator, _key: SessionKey, text: str) -> OrchestratorResult:
    """Handle /agent_restart <name>: restart a sub-agent."""
    supervisor = orch.supervisor
    if supervisor is None:
        return OrchestratorResult(text="Multi-agent mode is not active.")

    parts = text.split(None, 1)
    if len(parts) < 2:
        return OrchestratorResult(text="Usage: /agent_restart <name>")

    name = parts[1].strip().lower()
    result = await supervisor.restart_agent(name)
    return OrchestratorResult(text=result)
