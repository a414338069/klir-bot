# API MODULE

**Overview:** WebSocket server + dashboard — 6 files, 1988 lines. aiohttp server on port 8741. E2E encrypted sessions via NaCl.

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| WebSocket server | `server.py` (931) | ApiServer — OVERSIZED, aiohttp WebSocket |
| Request routing | `routes.py` | Route definitions (closures, not class methods) |
| Request handling | `controller.py` | DashboardController — business logic |
| E2E encryption | `crypto.py` | E2ESession — NaCl-based encryption |
| Dashboard integration | `dashboard.py` | DashboardHub — WebSocket dashboard |
| Dashboard controller | `controller.py` | Shared controller for API + dashboard |

## CONVENTIONS

- **aiohttp server**: Runs on port 8741 alongside Telegram bot (same process)
- **WebSocket protocol**: All real-time communication via WebSocket, not REST
- **E2E encryption**: `crypto.py` uses NaCl for end-to-end encrypted sessions
- **Closure-based routes**: `routes.py` uses closures (not class methods) for handlers
- **DashboardHub**: Bridges WebSocket connections to dashboard UI

## ANTI-PATTERNS

- **DO NOT** expand `server.py` further — 931 lines already, extract sub-modules
- **DO NOT** add REST endpoints — this is a WebSocket-first server
- **DO NOT** bypass E2E encryption for sensitive data
- **DO NOT** use class-based route handlers — follow closure pattern in `routes.py`
- **DO NOT** expose port 8741 without auth — see `crypto.py`
