# Agent Tools

Tools for inter-agent communication and shared knowledge.
Agent creation and removal are only available on the main agent.

## How users interact with sub-agents

Each sub-agent is a **separate Telegram bot** with its own chat. The user has two options:

1. **Direct chat** — The user opens the sub-agent's Telegram bot and chats
   directly. This is the primary way to use a sub-agent.
2. **Delegation** — The user asks the main agent to send a task via agent tools.
   The response comes back to the main agent's chat, NOT the sub-agent's chat.

**After creating a sub-agent, tell the user to open its Telegram chat to
talk to it directly.** Do NOT show `python3 tools/...` commands to the user —
those are internal tools for agent-to-agent communication only.

## Available Tools (internal, not user-facing)

| Tool | Purpose | Availability |
|------|---------|-------------|
| `ask_agent.py` | Ask a sub-agent a question (sync, blocks until response) | All agents |
| `ask_agent_async.py` | Give a sub-agent a task (async, response comes back to YOU) | All agents |
| `edit_shared_knowledge.py` | View or edit SHAREDMEMORY.md (synced to all agents) | All agents |
| `create_agent.py` | Create a new sub-agent (writes to `agents.json`, auto-detected) | Main only |
| `remove_agent.py` | Remove a sub-agent from the registry | Main only |
| `list_agents.py` | List all sub-agents and their configuration | Main only |

## How agent-to-agent communication works

**The response ALWAYS comes back to the calling agent.** There is no way
to make a sub-agent reply directly in its own Telegram chat via these tools.

    You (main) → send task to sub-agent → sub-agent processes → response returns to YOU

**Never tell the user** that a sub-agent will "answer in its own chat" or
"respond directly to the user" — that is not how these tools work.

## Creating Sub-Agents

When creating a sub-agent:

1. The user **must provide** a Telegram bot token (created via Telegram BotFather)
2. Choose a descriptive lowercase name (no spaces, e.g. `finanzius`, `researcher`)
3. Use **specific model names**, not provider names:
   - Claude: `opus`, `sonnet`, `haiku`
   - Codex: `gpt-5.3-codex`, `gpt-5.2-codex`, `gpt-5.1-codex-mini` (check `config/codex_models.json`)
   - Gemini: `gemini-2.5-pro`, `gemini-2.5-flash` (check `config/gemini_models.json`)
4. Provider is `claude`, `openai`, or `gemini`
5. The workspace is created automatically under `agents/<name>/`
6. The sub-agent starts automatically within seconds (FileWatcher)

**IMPORTANT:** Never use `codex` or `gemini` as model names — those are providers.
The `--model` must be a specific model ID from the lists above.

```bash
python3 tools/agent_tools/create_agent.py \
  --name "agent-name" \
  --token "BOT_TOKEN" \
  --users "USER_ID1,USER_ID2" \
  --provider openai \
  --model gpt-5.3-codex
```

## Inter-Agent Communication

Each agent has its own memory, workspace, and session — they are independent.
Two modes are available:

### Synchronous (blocking)

Use `ask_agent.py` for quick lookups or simple questions. Your CLI turn
blocks until the sub-agent responds. The response is returned to you
directly as tool output.

```bash
python3 tools/agent_tools/ask_agent.py "agent-name" "Quick question"
```

### Asynchronous (long-running tasks)

Use `ask_agent_async.py` for tasks that take longer. Returns immediately
with a task_id. The sub-agent's response is delivered back to **your**
Telegram chat (the calling agent's chat) when ready.

```bash
python3 tools/agent_tools/ask_agent_async.py "agent-name" "Complex request that takes time"
```

Use async for code generation, analysis, research, or anything that may
take more than a few seconds. Use sync for quick lookups.

### Starting a fresh session (`--new`)

By default, follow-up messages to the same agent resume the existing
inter-agent session (context is preserved). To start a completely new
task with no prior context, use the `--new` flag:

```bash
python3 tools/agent_tools/ask_agent_async.py --new "agent-name" "Brand new task"
python3 tools/agent_tools/ask_agent.py --new "agent-name" "Brand new question"
```

### Named Sessions for inter-agent work

Each inter-agent conversation creates a **Named Session** on the recipient
agent called `ia-{sender}` (e.g. if `main` sends to `codex`, the session
is `ia-main` on codex).

These sessions:
- Persist across messages and survive bot restarts
- Run in the background, independent of the recipient's direct Telegram chat
- Can be continued by the user directly in the recipient's Telegram chat
  via `@ia-{sender} <message>` (e.g. `@ia-main tell me more`)
- Are visible in the recipient's `/sessions` list

When reporting async results to the user, mention the session name so they
can follow up directly with the sub-agent if needed.

## Shared Knowledge

`SHAREDMEMORY.md` contains knowledge shared across all agents. Changes are
automatically synced into every agent's MAINMEMORY.md via the supervisor.

```bash
# View current shared knowledge
python3 tools/agent_tools/edit_shared_knowledge.py --show

# Append a fact
python3 tools/agent_tools/edit_shared_knowledge.py --append "New shared fact"

# Replace entire content
python3 tools/agent_tools/edit_shared_knowledge.py --set "Full new content"
```

When you learn something that is relevant to ALL agents (server facts, user
preferences, infrastructure changes), update shared knowledge instead of
only your own MAINMEMORY.md.

## Removing Sub-Agents

Removing a sub-agent stops its Telegram bot but **preserves its workspace**.
The workspace can be reused if the agent is re-created with the same name.

```bash
python3 tools/agent_tools/remove_agent.py "agent-name"
```
