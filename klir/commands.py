"""Bot command definitions shared across layers.

Commands are ordered by usage frequency (most used first).
Descriptions are kept ≤22 chars so mobile clients don't truncate.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# -- Core commands (every agent, shown in Telegram popup) ------------------
# Sorted by typical usage: daily actions → power-user → rare maintenance.

BOT_COMMANDS: list[tuple[str, str]] = [
    # Daily
    ("new", "Start new chat"),
    ("stop", "Stop running agent"),
    ("interrupt", "Soft interrupt (ESC)"),
    ("model", "Show/switch model"),
    ("think", "Set thinking level"),
    ("compact", "Compress session"),
    ("cwd", "Show/change work dir"),
    ("status", "Session info"),
    ("memory", "Show main memory"),
    # Automation & multi-agent
    ("session", "Background sessions"),
    ("tasks", "Background tasks"),
    ("cron", "Manage cron jobs"),
    ("agent_commands", "Multi-agent system"),
    # Browse & info
    ("where", "Tracked chats"),
    ("leave", "Leave a group"),
    ("showfiles", "Browse files"),
    ("hooks", "Message hooks"),
    ("agents", "List available agents"),
    ("skills", "List skills/commands"),
    ("mcp", "MCP server status"),
    ("sessions", "View and switch OpenCode sessions"),
    ("info", "Docs, links & about"),
    ("help", "Show all commands"),
    # Maintenance (rare)
    ("diagnose", "System diagnostics"),
    ("upgrade", "Check for updates"),
    ("update_plugins", "Update all plugins"),
    ("restart", "Restart bot"),
    ("pair", "Generate pairing code"),
]

# Commands shown in group/supergroup chats — admin/maintenance commands filtered out.
_GROUP_EXCLUDED: frozenset[str] = frozenset(
    {"diagnose", "upgrade", "update_plugins", "restart", "agent_commands", "pair"}
)

GROUP_COMMANDS: list[tuple[str, str]] = [
    (cmd, desc) for cmd, desc in BOT_COMMANDS if cmd not in _GROUP_EXCLUDED
]

# Sub-commands registered as handlers but NOT shown in the Telegram popup.
# Users discover them via /agent_commands or /help.
MULTIAGENT_SUB_COMMANDS: list[tuple[str, str]] = [
    ("agents", "List all agents"),
    ("agent_start", "Start a sub-agent"),
    ("agent_stop", "Stop a sub-agent"),
    ("agent_restart", "Restart a sub-agent"),
    ("stop_all", "Stop all agents"),
]

# -- Built-in command names (used to avoid duplicates with skills) ----------
_BUILTIN_COMMANDS: frozenset[str] = frozenset(
    cmd for cmd, _ in BOT_COMMANDS + MULTIAGENT_SUB_COMMANDS
)

# Telegram command description limit.
_MAX_DESC_LEN = 64


def _truncate(text: str, limit: int = _MAX_DESC_LEN) -> str:
    """Truncate *text* to *limit* chars, adding ellipsis if needed."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _parse_frontmatter(md_path: Path) -> dict[str, str] | None:
    """Extract YAML frontmatter from a Markdown file as a flat dict."""
    try:
        raw = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not raw.startswith("---"):
        return None
    end = raw.find("---", 3)
    if end == -1:
        return None
    try:
        meta = yaml.safe_load(raw[3:end])
    except yaml.YAMLError:
        return None
    return meta if isinstance(meta, dict) else None


def _scan_skill_dir(
    skills_dir: Path,
    *,
    prefix: str = "",
    seen: set[str],
) -> list[tuple[str, str]]:
    """Scan a skills directory for ``SKILL.md`` entries."""
    if not skills_dir.is_dir():
        return []
    results: list[tuple[str, str]] = []
    for entry in sorted(skills_dir.iterdir()):
        if entry.name.startswith(".") or not (entry.is_dir() or entry.is_symlink()):
            continue
        meta = _parse_frontmatter(entry / "SKILL.md")
        if not meta or not meta.get("description"):
            continue
        command = prefix + entry.name.replace("-", "_")
        if command in _BUILTIN_COMMANDS or command in seen:
            continue
        seen.add(command)
        results.append((command, _truncate(str(meta["description"]))))
    return results


def _scan_command_dir(
    commands_dir: Path,
    *,
    prefix: str = "",
    seen: set[str],
) -> list[tuple[str, str]]:
    """Scan a plugin ``commands/`` directory for ``.md`` command files."""
    if not commands_dir.is_dir():
        return []
    results: list[tuple[str, str]] = []
    for entry in sorted(commands_dir.iterdir()):
        if not entry.name.endswith(".md") or entry.name.startswith("."):
            continue
        meta = _parse_frontmatter(entry)
        if not meta or not meta.get("description"):
            continue
        command = prefix + entry.stem.replace("-", "_")
        if command in _BUILTIN_COMMANDS or command in seen:
            continue
        seen.add(command)
        results.append((command, _truncate(str(meta["description"]))))
    return results


def _enabled_plugin_keys() -> frozenset[str]:
    """Return the set of enabled plugin keys from Claude Code settings."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return frozenset()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    return frozenset(key for key, enabled in data.get("enabledPlugins", {}).items() if enabled)


def _discover_plugin_commands(seen: set[str]) -> list[tuple[str, str]]:
    """Discover skills and commands from enabled Claude Code plugins.

    Reads ``~/.claude/plugins/installed_plugins.json``, filters by enabled
    plugins in settings, and scans each plugin's ``source/skills/``,
    ``skills/``, and ``commands/`` directories.  Commands are prefixed
    with the plugin name (e.g. ``impeccable_animate``, ``commit_commands_commit``).
    """
    installed_json = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    if not installed_json.exists():
        return []
    try:
        data = json.loads(installed_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    enabled = _enabled_plugin_keys()
    results: list[tuple[str, str]] = []

    for plugin_key, entries in data.get("plugins", {}).items():
        if plugin_key not in enabled:
            continue
        plugin_name = plugin_key.split("@", 1)[0].replace("-", "_")
        prefix = f"{plugin_name}_"
        for entry in entries:
            install_path = Path(entry.get("installPath", ""))
            # Skills: source/skills/ and skills/ directories
            for skills_subdir in ("source/skills", "skills"):
                results.extend(
                    _scan_skill_dir(install_path / skills_subdir, prefix=prefix, seen=seen)
                )
            # Commands: commands/ directory (.md files)
            results.extend(_scan_command_dir(install_path / "commands", prefix=prefix, seen=seen))
    return results


# Telegram Bot API allows max 100 commands per scope.
_MAX_TELEGRAM_COMMANDS = 100


def discover_skill_commands(skills_dir: Path) -> list[tuple[str, str]]:
    """Discover all skills and plugin commands for the Telegram popup.

    Sources (in priority order):
    1. Standalone workspace skills (from *skills_dir*, no prefix)
    2. Enabled plugin skills (``source/skills/``, ``skills/``)
    3. Enabled plugin commands (``commands/*.md``)

    Results are capped at the Telegram 100-command limit minus built-in commands.
    """
    seen: set[str] = set()
    results = _scan_skill_dir(skills_dir, seen=seen)
    results.extend(_discover_plugin_commands(seen))

    budget = _MAX_TELEGRAM_COMMANDS - len(BOT_COMMANDS)
    return results[:budget]
