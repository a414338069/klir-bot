# INFRA MODULE

**Overview:** Infrastructure/utility catch-all — 30 files, 10+ unrelated domains. The project's "junk drawer" for code that doesn't fit elsewhere. No unified architecture; each file is independent.

## WHERE TO LOOK

| Domain | Files | Notes |
|--------|-------|-------|
| Database & Storage | `db.py`, `json_store.py`, `migrations/` | SQLite via aiosqlite + JSON fallback; 3 migration SQL files |
| File System | `fs.py`, `file_watcher.py`, `atomic_io.py` | Path utils, inotify-based watcher, atomic write patterns |
| Process & Service Mgmt | `process_tree.py`, `pidlock.py` | Kill tree, PID file locking with acquire/release |
| Service (dispatching) | `service.py` | Platform-dispatching wrapper → delegates to backend below |
| Service (backends) | `service_linux.py`, `service_macos.py`, `service_windows.py` | systemd / launchd / Task Scheduler lifecycle |
| Service (shared) | `service_base.py`, `service_logs.py` | Shared helpers & log rotation for all backends |
| Install | `install.py` | Detect pipx / pip / dev install method |
| System / Platform | `platform.py`, `boot_id.py`, `startup_state.py` | OS detection, boot session ID, state persistence |
| Network | `proxy.py`, `tailscale.py` | HTTP proxy config, Tailscale status/integration |
| Security | `env_secrets.py` | Env var secrets — DO NOT use `sk-xxx` placeholders |
| Recovery & Observability | `recovery.py`, `restart.py`, `inflight.py` | Crash recovery planner, restart sentinels, in-flight tracking |
| Observers | `base_observer.py`, `base_task_observer.py` | Abstract asyncio periodic observer classes |
| Misc | `task_runner.py`, `updater.py`, `version.py` | Generic runner, self-update checker, version helpers |

## CONVENTIONS

- **All async**: SQLite, file I/O, observers — everything uses asyncio
- **Observer pattern**: `base_observer.BaseObserver` is abstract; subclass implements `_tick()`. Used by `updater.py`, `file_watcher.py`, and observability code.
- **Service backends**: Each OS backend (`service_linux.py`, etc.) implements the same interface; `service.py` dispatches by `sys.platform`.
- **PID lock + restart sentinel**: Exposed at package level via `__init__.py` — these are the module's "public API."
- **Migrations**: Sequential SQL files in `migrations/`, run by `db.py`.

## ANTI-PATTERNS

- **DO NOT** add new files here unless no other package fits — this module is already too broad
- **DO NOT** create cross-file dependencies within infra — files should be independently importable
- **DO NOT** replicate observer/service patterns in ad-hoc ways — use `BaseObserver` or service backends
- **DO NOT** use `sk-xxx` anywhere in `env_secrets.py` or related files — security scanners flag it
- **DO NOT** expect migrations to run automatically — `db.py` applies them at startup
- **DO NOT** add Docker or CI/CD logic here — deploy scripts live in `deploy/` at project root
