# CLI MODULE

**Overview:** AI CLI provider layer — 31 files. Factory + strategy pattern. Each AI tool is a `*_provider.py` implementing `BaseCLI` abstract interface.

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Add new AI provider | `*_provider.py` + `factory.py` | Implement BaseCLI, register in create_cli() |
| Base class / interface | `base.py` | BaseCLI abstract class — subprocess lifecycle |
| Provider selection | `factory.py` | `create_cli()` dispatches by config |
| Claude provider | `claude_provider.py` | Claude Code CLI wrapper |
| Codex provider | `codex_provider.py` | OpenAI Codex CLI wrapper |
| Codex cache/events | `codex_cache.py`, `codex_cache_observer.py`, `codex_events.py` | 3-file cache subsystem |
| Codex discovery | `codex_discovery.py` | Detect installed Codex version |
| Gemini provider | `gemini_provider.py` (592 lines) | Google Gemini CLI wrapper |
| Gemini cache/events | `gemini_cache.py`, `gemini_cache_observer.py`, `gemini_events.py` | 3-file cache subsystem |
| Gemini utilities | `gemini_utils.py` | Shared Gemini helpers |
| OpenCode (local) | `opencode_provider.py` | OpenCode CLI wrapper |
| OpenCode (HTTP) | `opencode_http_provider.py` | Custom HTTP-based provider (fork addition) |
| OpenCode events | `opencode_events.py` | Event types for OpenCode |
| Subprocess execution | `executor.py` | Subprocess lifecycle management |
| Auth handling | `auth.py` | CLI authentication |
| Stream coalescing | `coalescer.py` | StreamCoalescer — merge rapid updates |
| Tool loop detection | `tool_loop_detector.py` | Detect infinite tool-use loops |
| Tool activity | `tool_activity.py` | Track active tool calls |
| Stream events | `stream_events.py` | Stream event type definitions |
| CLI init wizard | `init_wizard.py` (571 lines) | Interactive first-run setup |
| Model cache | `model_cache.py` | Cache available models |
| Parameter resolution | `param_resolver.py` | Resolve CLI parameters from config |
| Process registry | `process_registry.py` | Track running CLI processes |
| Server management | `server_manager.py` | Manage CLI server instances |
| CLI service | `service.py` | CLI as system service |
| Timeout control | `timeout_controller.py` | Enforce CLI timeouts |
| Type definitions | `types.py` | Shared type aliases/protocols |

## CONVENTIONS

- **Provider pattern**: Each `*_provider.py` extends `BaseCLI` from `base.py`
- **Factory dispatch**: `factory.py::create_cli()` selects provider by config
- **Cache trio pattern**: Providers with caching have 3 files: `*_cache.py`, `*_cache_observer.py`, `*_events.py`
- **Subprocess model**: All providers launch CLI tools as subprocesses via `executor.py`
- **Stream processing**: Output flows through `stream_events.py` → `coalescer.py` → bot streaming
- **Tool loop protection**: `tool_loop_detector.py` watches for infinite tool use cycles

## ANTI-PATTERNS

- **DO NOT** bypass `factory.py` to instantiate providers directly
- **DO NOT** add new provider without also updating `factory.py`
- **DO NOT** share state between provider instances — use ProcessRegistry
- **DO NOT** hardcode CLI paths — use discovery mechanisms
- **DO NOT** modify `opencode_http_provider.py` without also updating `deploy/klir-package/`
