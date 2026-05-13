# klir Docs

klir routes chat input to official provider CLIs (`claude`, `codex`, `gemini`), streams responses back to Telegram, persists session state, and runs cron/heartbeat/webhook/cleanup automation in-process. It also supports a direct WebSocket API transport with authenticated file upload/download.

## Onboarding (Read in This Order)

1. `docs/system_overview.md` -- fastest end-to-end mental model.
2. `docs/developer_quickstart.md` -- shortest path for contributors/junior devs.
3. `docs/modules/setup_wizard.md` -- CLI commands, onboarding, restart/upgrade lifecycle.
4. `docs/architecture.md` -- startup, routing, streaming, callbacks, observers.
5. `docs/config.md` -- config schema, merge behavior, hot-reload boundaries.
6. `docs/modules/config_reload.md` -- runtime config reload details.
7. `docs/modules/orchestrator.md` -- routing core, flows, selectors, lifecycle split.
8. `docs/modules/bot.md` -- Telegram ingress, middleware, topic routing, chat tracking.
9. `docs/modules/bus.md` -- unified Envelope/MessageBus delivery architecture.
10. `docs/modules/session.md` -- `SessionKey(chat_id, topic_id)` isolation model.
11. `docs/modules/tasks.md` -- delegated task system (`TaskHub`) and `/tasks/*` API.
12. `docs/modules/api.md` -- direct WebSocket ingress and HTTP file endpoints.
13. `docs/modules/cli.md` -- provider wrappers, stream parsing, process control.
14. `docs/modules/cli_commands.md` -- CLI command split from `__main__.py`.
15. `docs/modules/workspace.md` -- `~/.klir` seeding, rules sync, skill sync.
16. `docs/modules/multiagent.md` -- supervisor, bus bridge, sub-agent runtime.
17. Remaining module docs (`background`, `cron`, `webhook`, `heartbeat`, `cleanup`, `infra`, `supervisor`, `security`, `logging`, `files`, `text`, `skill_system`).

## System in 60 Seconds

- `klir/__main__.py`: thin CLI entrypoint (dispatch) + config loading.
- `klir/cli_commands/`: concrete CLI subcommand implementations (`agents`, `service`, `api`, lifecycle/status helpers).
- `klir/bot/`: aiogram handlers, auth/sequencing middleware, streaming dispatch, callback routing, group audit/chat tracking.
- `klir/orchestrator/`: command registry, directives/hooks, normal + streaming + heartbeat flows, provider/session/task wiring.
- `klir/bus/`: central `MessageBus` + `Envelope` + `LockPool` + Telegram transport formatting.
- `klir/session/`: provider-isolated session state keyed by `SessionKey(chat_id, topic_id)` plus named-session registry.
- `klir/tasks/`: shared background task delegation (`TaskHub`) and persistent task registry.
- `klir/api/`: WebSocket ingress (`/ws`) and HTTP file endpoints (`/files`, `/upload`).
- `klir/cli/`: Claude/Codex/Gemini wrappers, stream-event normalization, auth checks, model caches, process registry.
- `klir/cron/`, `webhook/`, `heartbeat/`, `cleanup/`: in-process automation observers.
- `klir/workspace/`: path source-of-truth, home defaults sync, rules deployment/sync, skill sync.
- `klir/multiagent/`: supervisor, inter-agent bus, internal localhost API bridge, shared-knowledge sync.
- `klir/infra/`: PID lock, restart/update state, service backends, observer/task utilities.

Runtime behavior notes:

- `/new` resets only the active provider bucket of the active session key (topic-aware).
- Forum topics are isolated: each topic has its own `SessionKey(chat_id, topic_id)` state.
- Normal CLI errors do not auto-reset sessions; context is preserved unless explicit reset/recovery path applies.
- Startup can recover interrupted foreground turns and safely resume eligible named sessions.

## Documentation Index

- [Architecture](architecture.md)
- [System Overview](system_overview.md)
- [Installation](installation.md)
- [Automation Quickstart](automation.md)
- [Developer Quickstart](developer_quickstart.md)
- [Configuration](config.md)
- Module docs:
  - [setup_wizard](modules/setup_wizard.md)
  - [cli_commands](modules/cli_commands.md)
  - [config_reload](modules/config_reload.md)
  - [bot](modules/bot.md)
  - [bus](modules/bus.md)
  - [background](modules/background.md)
  - [session](modules/session.md)
  - [tasks](modules/tasks.md)
  - [api](modules/api.md)
  - [files](modules/files.md)
  - [text](modules/text.md)
  - [cli](modules/cli.md)
  - [orchestrator](modules/orchestrator.md)
  - [workspace](modules/workspace.md)
  - [skill_system](modules/skill_system.md)
  - [cron](modules/cron.md)
  - [webhook](modules/webhook.md)
  - [heartbeat](modules/heartbeat.md)
  - [cleanup](modules/cleanup.md)
  - [infra](modules/infra.md)
  - [supervisor](modules/supervisor.md)
  - [multiagent](modules/multiagent.md)
  - [security](modules/security.md)
  - [logging](modules/logging.md)
