"""Service management CLI subcommands (``klir service ...``)."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_console = Console()

_SERVICE_SUBCOMMANDS = frozenset(
    {"install", "status", "stop", "start", "restart", "logs", "uninstall"}
)


def _parse_service_subcommand(args: list[str]) -> str | None:
    """Extract the subcommand after 'service' from CLI args."""
    found_service = False
    for a in args:
        if a.startswith("-"):
            continue
        if not found_service and a == "service":
            found_service = True
            continue
        if found_service:
            return a if a in _SERVICE_SUBCOMMANDS else None
    return None


def print_service_help() -> None:
    """Print the service subcommand help table."""
    _console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold green", min_width=30)
    table.add_column()
    table.add_row("klir service install", "Install and start background service")
    table.add_row("klir service status", "Show service status")
    table.add_row("klir service start", "Start the service")
    table.add_row("klir service stop", "Stop the service")
    table.add_row("klir service restart", "Restart the service")
    table.add_row("klir service logs", "View live logs")
    table.add_row("klir service uninstall", "Remove the service")
    _console.print(
        Panel(table, title="[bold]Service Commands[/bold]", border_style="blue", padding=(1, 0)),
    )
    _console.print()


def cmd_service(args: list[str]) -> None:
    """Handle 'klir service <subcommand>'."""
    from klir.infra.service import (
        install_service,
        print_service_logs,
        print_service_status,
        restart_service,
        start_service,
        stop_service,
        uninstall_service,
    )

    sub = _parse_service_subcommand(args)
    if sub is None:
        print_service_help()
        return

    def _install() -> None:
        install_service(_console)

    def _status() -> None:
        print_service_status(_console)

    def _start() -> None:
        start_service(_console)

    def _stop() -> None:
        stop_service(_console)

    def _restart() -> None:
        restart_service(_console)

    def _logs() -> None:
        print_service_logs(_console)

    def _uninstall_service_cmd() -> None:
        uninstall_service(_console)

    dispatch: dict[str, Callable[[], None]] = {
        "install": _install,
        "status": _status,
        "start": _start,
        "stop": _stop,
        "restart": _restart,
        "logs": _logs,
        "uninstall": _uninstall_service_cmd,
    }
    _console.print()
    dispatch[sub]()
    _console.print()
