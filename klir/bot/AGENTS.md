# BOT MODULE

**Overview:** Telegram frontend — 34 files, aiogram 3.x. Message pipeline: update → middleware → handlers → orchestrator → CLI → streaming/sender.

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Add message handler | `handlers.py` | Register in `app.py` router |
| Auth / access control | `middleware.py` (502 lines) | Sequential processing + user filtering |
| Bot setup / lifecycle | `app.py` (1778 lines) | BotApp class — OVERSIZED, needs splitting |
| Streaming output | `streaming.py`, `edit_streaming.py` | Real-time edit-based message updates |
| Send rich text / files | `sender.py` | File references, markdown, code blocks |
| Inline buttons | `buttons.py`, `callbacks.py` | Keyboard builders + callback handlers |
| Message forwarding | `forward_parser.py`, `forward_sender.py`, `forward_context.py` | 3-file forwarding subsystem |
| Approval workflow | `approval_handler.py` | Human-in-the-loop for AI actions |
| Device pairing | `pair_handler.py` | Pair user/device |
| File browser UI | `file_browser.py` | In-chat file browsing |
| Poll creation | `poll_parser.py`, `poll_sender.py` | 2-file poll subsystem |
| Typing indicators | `typing.py` | Manage typing state |
| Forum topics | `topic.py` | Forum topic management |
| Message dedup | `dedup.py` | Prevent duplicate processing |
| Retry failed sends | `retry.py` | Exponential backoff for Telegram API |
| CLI session creation | `session_factory.py` | Bridge bot context → CLI session |
| Startup/shutdown | `startup.py` | aiogram lifecycle hooks |
| Welcome message | `welcome.py` | New user greeting |
| Message footer | `footer.py` | Consistent message footer |
| Conflict detection | `conflict_detector.py` | Detect conflicting operations |
| Resource cleanup | `binding_cleanup.py` | Cleanup bound resources |
| Upgrade notices | `upgrade_handler.py` | Handle version upgrade |
| Abort operations | `abort.py` | Cancel running operations |
| Text formatting | `formatting.py` | Markdown/code block helpers |
| Media handling | `media.py` | File/media upload+download |
| Reactions | `reactions.py` | Telegram message reactions |
| Chat state | `chat_tracker.py` | Track active conversations |

## CONVENTIONS

- **aiogram 3.x ONLY** — do NOT use v4 APIs
- **Flat namespace** — all 34 files are siblings, no sub-packages
- **Router pattern** — handlers registered on aiogram Router, assembled in `app.py`
- **Pipeline**: message → `middleware.py` (auth+queue) → `handlers.py` → `orchestrator` → CLI → `sender.py`/`streaming.py`
- **Everything async** — all handlers are `async def`
- **No business logic here** — bot/ is a thin frontend; logic lives in `orchestrator/` and `cli/`

## ANTI-PATTERNS

- **DO NOT** add business logic to handler functions — delegate to orchestrator
- **DO NOT** create nested directories — keep flat
- **DO NOT** import from `cli/` directly — go through `orchestrator/` or `session_factory.py`
- **DO NOT** use `aiogram` v2 or v4 APIs
- **DO NOT** bypass `middleware.py` for auth checks
- **DO NOT** expand `app.py` further — it's already 1778 lines
