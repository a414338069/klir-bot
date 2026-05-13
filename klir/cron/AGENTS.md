# CRON MODULE

**Overview:** Cron scheduler — 8 files. Crontab-style scheduling via cronsim library. Manages periodic AI tasks with backoff, dependency queuing, and run logging.

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Add/modify cron scheduling | `manager.py` | CronManager — schedule/register jobs |
| Job execution | `execution.py` | OneShotCommand — run a cron job |
| Observe cron state | `observer.py` (587) | CronObserver — monitor job lifecycle |
| Alert on failures | `alerts.py` | Cron failure alerting |
| Backoff strategy | `backoff.py` | Exponential backoff for failed jobs |
| Job dependencies | `dependency_queue.py` | DependencyQueue — order jobs by deps |
| Run history | `run_log.py` | SQLite-backed run log |

## CONVENTIONS

- **Cronsim-based**: Uses `cronsim` library for crontab expression parsing
- **Manager + Observer**: `CronManager` owns scheduling, `CronObserver` watches lifecycle
- **OneShotCommand**: Each execution is a discrete command object
- **Dependency queue**: Jobs can depend on other jobs completing first
- **SQLite run log**: All runs logged to SQLite for audit/replay
- **Backoff on failure**: Failed jobs back off exponentially

## ANTI-PATTERNS

- **DO NOT** add scheduling logic outside `manager.py`
- **DO NOT** run cron jobs without logging via `run_log.py`
- **DO NOT** bypass `DependencyQueue` for dependent jobs
- **DO NOT** modify `observer.py` without understanding its 587-line complexity
