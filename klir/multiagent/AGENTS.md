# MULTIAGENT MODULE

**Overview:** Multi-agent orchestration — 10 files. AgentSupervisor manages sub-agent lifecycle from agents.json registry. Inter-agent communication via InterAgentBus.

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Sub-agent lifecycle | `supervisor.py` (648) | AgentSupervisor — spawn/stop/health agents |
| Inter-agent messaging | `bus.py` (451) | InterAgentBus — pub/sub between agents |
| Agent commands | `commands.py` | Command handlers for agent operations |
| Health monitoring | `health.py` | AgentHealth — liveness/readiness checks |
| Internal API | `internal_api.py` (449) | HTTP API on port 8799 for agent coordination |
| Data models | `models.py` | Agent config/state models |
| Agent registry | `registry.py` | agents.json loading + validation |
| Shared knowledge | `shared_knowledge.py` | SharedKnowledgeSync between agents |
| Agent stack | `stack.py` | AgentStack — manage agent hierarchy |

## CONVENTIONS

- **Supervisor pattern**: `AgentSupervisor` owns all sub-agent processes
- **Registry-driven**: Agents defined in `agents.json`, loaded by `registry.py`
- **Bus architecture**: `InterAgentBus` handles all inter-agent communication (no direct calls)
- **Internal API**: Port 8799 HTTP for coordination (not exposed externally)
- **Shared knowledge**: `SharedKnowledgeSync` for cross-agent context sharing
- **Stack management**: `AgentStack` tracks parent-child agent relationships

## ANTI-PATTERNS

- **DO NOT** spawn agents outside `AgentSupervisor` — lifecycle must be managed
- **DO NOT** bypass `InterAgentBus` for agent communication
- **DO NOT** hardcode agent configs — use `agents.json` via `registry.py`
- **DO NOT** expose port 8799 externally — internal coordination only
- **DO NOT** share mutable state between agents — use `SharedKnowledgeSync`
