# Memory System

Long-term memory organized into categories under `memory_system/`.

## Directory Structure

- `user/profile.md` — About the user (always loaded on new sessions)
- `user/preferences/` — Tools, styles, approaches the user prefers
- `user/entities/` — Projects, repos, teams, services
- `user/events/` — Specific incidents, decisions with dates
- `agent/cases/` — Problem+solution pairs
- `agent/patterns/` — Reusable workflows and conventions

## Memory File Format

Every memory file uses frontmatter:

```
---
abstract: One-line summary
category: profile|preferences|entities|events|cases|patterns
---

Detailed content here.
```

## Silence Is Mandatory

Never tell the user you are reading or writing memory.
Memory operations are invisible.

## When to Write

- Durable personal facts or preferences
- Decisions that should affect future behavior
- User explicitly asks to remember
- Repeating workflow patterns
- Problem+solution pairs worth remembering

## When Not to Write

- One-off throwaway requests
- Temporary debugging noise
- Facts already recorded

## Format Rules

- Keep entries short and actionable
- One memory per file
- Use descriptive filenames (e.g., `testing-preferences.md`)
- Merge duplicates; remove stale facts

## Shared Knowledge (SHAREDMEMORY.md)

When you learn something relevant to ALL agents, update shared knowledge:

```bash
python3 tools/agent_tools/edit_shared_knowledge.py --append "New shared fact"
```

## Cleanup Rules

- If user says data is wrong or should be forgotten, remove/update immediately
- Do not leave "deleted" markers; keep files clean
