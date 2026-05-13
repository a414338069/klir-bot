# ORCHESTRATOR MODULE

**Overview:** Central message routing hub — 12 files + 7 selectors, 4941 total lines. Dispatches between bot, CLI providers, and subsystems. The "brain" of the application.

## STRUCTURE

```
orchestrator/
├── core.py (800)        # Main Orchestrator class — message dispatch
├── flows.py (832)       # Flow definitions — multi-step interaction sequences
├── commands.py (770)    # Command handlers — /start, /stop, etc.
├── hooks.py             # Hook system — pre/post processing hooks
├── injection.py         # Dependency injection for orchestrator
├── lifecycle.py         # Lifecycle management (start/stop/shutdown)
├── observers.py         # Observer pattern for state changes
├── providers.py         # Provider registration/management
├── registry.py          # Service registry
├── directives.py        # Processing directives (routing hints)
├── user_hooks.py        # User-defined hook support
└── selectors/           # Selection strategies
    ├── model_selector.py (453)   # AI model selection logic
    ├── session_selector.py       # Session routing
    ├── cron_selector.py          # Cron-triggered message routing
    ├── task_selector.py          # Task-based routing
    ├── models.py                 # Selector data models
    └── utils.py                  # Shared selector utilities
```

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Change message routing | `core.py` | Main dispatch loop — 800 lines |
| Add multi-step flow | `flows.py` | Flow definitions — 832 lines |
| Add/modify command | `commands.py` | /command handlers — 770 lines |
| Change model selection | `selectors/model_selector.py` (453) | Model routing logic |
| Add lifecycle hook | `lifecycle.py`, `hooks.py` | Start/stop/shutdown hooks |
| Register new provider | `providers.py`, `registry.py` | Provider + service registry |
| Add routing directive | `directives.py` | Routing hints for dispatch |
| Session routing logic | `selectors/session_selector.py` | Which session gets the message |
| Cron-triggered dispatch | `selectors/cron_selector.py` | Route cron messages |
| Task-based routing | `selectors/task_selector.py` | Route task messages |

## CONVENTIONS

- **Core dispatch pattern**: `core.py::Orchestrator` receives messages → routes via selectors → dispatches to commands/flows
- **Selector pattern**: `selectors/` contains independent selection strategies, each implements selection interface
- **Commands vs Flows**: Commands are single-shot; flows are multi-step sequences
- **Hook system**: `hooks.py` + `user_hooks.py` for pre/post processing
- **Dependency injection**: `injection.py` wires dependencies, avoid manual construction
- **All 3 core files >770 lines** — complex but well-structured

## ANTI-PATTERNS

- **DO NOT** add routing logic outside `core.py` — all dispatch goes through Orchestrator
- **DO NOT** bypass selectors for routing — extend selector pattern instead
- **DO NOT** put business logic in commands/flows — delegate to cli/ or infra/
- **DO NOT** create circular imports between core/commands/flows — use registry/injection
- **DO NOT** expand core.py further without extracting sub-modules
