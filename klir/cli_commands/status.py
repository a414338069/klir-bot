"""Status display CLI commands (``klir status``, ``klir help``)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from klir.infra.platform import is_windows
from klir.workspace.paths import KlirPaths, resolve_paths

_console = Console()


@dataclass(slots=True)
class StatusSummary:
    """Runtime status inputs needed by the status panel renderer."""

    bot_running: bool
    bot_pid: int | None
    bot_uptime: str
    provider: str
    model: str
    error_count: int


def build_status_lines(status: StatusSummary, *, paths: KlirPaths) -> list[str]:
    """Assemble the status panel content lines."""
    lines: list[str] = []
    if status.bot_running:
        lines.append(
            f"[bold green]Running[/bold green]  pid={status.bot_pid}  uptime: {status.bot_uptime}"
        )
    else:
        lines.append("[dim]Not running[/dim]")
    lines.append(f"Provider:  [cyan]{status.provider}[/cyan] ({status.model})")
    if status.error_count > 0:
        lines.append(f"Errors:    [bold red]{status.error_count}[/bold red] in latest log")
    else:
        lines.append("Errors:    [green]0[/green]")
    lines.append("")
    lines.append("[bold]Paths:[/bold]")
    lines.append(f"  Home:       [cyan]{paths.klir_home}[/cyan]")
    lines.append(f"  Config:     [cyan]{paths.config_path}[/cyan]")
    lines.append(f"  Workspace:  [cyan]{paths.workspace}[/cyan]")
    lines.append(f"  Logs:       [cyan]{paths.logs_dir}[/cyan]")
    lines.append(f"  Database:   [cyan]{paths.db_path}[/cyan]")
    return lines


def count_log_errors(log_dir: Path) -> int:
    """Count ERROR entries in the most recent log file."""
    if not log_dir.is_dir():
        return 0
    log_files = sorted(
        log_dir.glob("klir*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not log_files:
        return 0
    try:
        return log_files[0].read_text(encoding="utf-8", errors="replace").count(" ERROR ")
    except OSError:
        return 0


def print_status() -> None:
    """Print bot status, paths, and runtime info including sub-agents."""
    from klir.cli_commands.agents import load_agents_registry, print_agents_status

    paths = resolve_paths()
    try:
        data: dict[str, object] = json.loads(
            paths.config_path.read_text(encoding="utf-8"),
        )
    except (json.JSONDecodeError, OSError):
        return

    provider = data.get("provider", "claude")
    model = data.get("model", "opus")

    # Running state
    pid_file = paths.klir_home / "bot.pid"
    bot_running = False
    bot_pid: int | None = None
    bot_uptime = ""
    if pid_file.exists():
        try:
            bot_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            bot_pid = None
        if bot_pid is not None:
            from klir.infra.pidlock import _is_process_alive

            bot_running = _is_process_alive(bot_pid)
            if bot_running:
                mtime = datetime.fromtimestamp(pid_file.stat().st_mtime, tz=UTC)
                delta = datetime.now(UTC) - mtime
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                bot_uptime = f"{hours}h {minutes}m"

    # Error count from latest log
    error_count = count_log_errors(paths.logs_dir)

    # Build status lines
    summary = StatusSummary(
        bot_running=bot_running,
        bot_pid=bot_pid,
        bot_uptime=bot_uptime,
        provider=str(provider),
        model=str(model),
        error_count=error_count,
    )
    lines = build_status_lines(summary, paths=paths)

    _console.print(
        Panel(
            "\n".join(lines),
            title="[bold]Status — main[/bold]",
            border_style="green",
            padding=(1, 2),
        ),
    )

    # Show sub-agents
    agents = load_agents_registry(paths)
    if agents:
        print_agents_status(agents, bot_running=bot_running)


def print_usage() -> None:
    """Print commands and smart status information."""
    from klir.__main__ import _is_configured

    _console.print()
    banner_path = Path(__file__).resolve().parent.parent / "_banner.txt"
    try:
        banner_text = banner_path.read_text(encoding="utf-8").rstrip()
    except OSError:
        banner_text = "klir"
    _console.print(
        Panel(
            Text(banner_text, style="bold cyan"),
            subtitle="[dim]klir[/dim]",
            border_style="cyan",
            padding=(0, 2),
        ),
    )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold green", min_width=24)
    table.add_column()
    table.add_row("klir", "Start the bot (runs onboarding if needed)")
    table.add_row("klir onboarding", "Setup wizard (resets if already configured)")
    table.add_row("klir stop", "Stop running bot")
    table.add_row("klir restart", "Restart the bot")
    table.add_row("klir reset", "Full reset and re-setup")
    table.add_row("klir upgrade", "Stop, upgrade to latest, restart")
    table.add_row("klir uninstall", "Remove everything and uninstall")
    is_macos = sys.platform == "darwin"
    svc_hint = "Task Scheduler" if is_windows() else ("launchd" if is_macos else "systemd")
    table.add_row("klir service install", f"Run as background service ({svc_hint})")
    table.add_row("klir service", "Service management (status/stop/logs/...)")
    table.add_row("klir agents", "Sub-agent management (list/add/remove)")
    table.add_row("klir api", "API server management [beta]")
    table.add_row("klir status", "Show bot status, paths, and agents")
    table.add_row("klir help", "Show this message")
    table.add_row("-v, --verbose", "Verbose logging output")

    _console.print(
        Panel(table, title="[bold]Commands[/bold]", border_style="blue", padding=(1, 0)),
    )

    if _is_configured():
        print_status()
    else:
        _console.print(
            Panel(
                "[bold yellow]Not configured.[/bold yellow]\n\n"
                "Run [bold]klir[/bold] to start the setup wizard.",
                title="[bold]Status[/bold]",
                border_style="yellow",
                padding=(1, 2),
            ),
        )
    _console.print()
