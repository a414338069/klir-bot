"""Shared helpers for platform-specific service backends."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from rich.panel import Panel

if TYPE_CHECKING:
    from rich.console import Console

# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------


def ensure_console(console: Console | None) -> Console:
    """Return an initialized Rich console instance."""
    if console is not None:
        return console

    from rich.console import Console as RichConsole

    return RichConsole()


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def find_klir_binary() -> str | None:
    """Find the klir binary in PATH. Shared across all backends."""
    return shutil.which("klir")


# ---------------------------------------------------------------------------
# NVM
# ---------------------------------------------------------------------------


def collect_nvm_bin_dirs(home: Path) -> list[str]:
    """Return bin directories for all NVM-managed Node.js versions."""
    nvm_dir = home / ".nvm"
    if not nvm_dir.is_dir():
        return []
    return [str(node_dir) for node_dir in sorted(nvm_dir.glob("versions/node/*/bin"), reverse=True)]


# ---------------------------------------------------------------------------
# Standardised messages
# ---------------------------------------------------------------------------

_NOT_INSTALLED_MSG = "[dim]Service not installed. Run [bold]klir service install[/bold].[/dim]"
_NOT_RUNNING_MSG = "[dim]Service is not running.[/dim]"
_NO_SERVICE_MSG = "[dim]No service installed.[/dim]"
_BINARY_NOT_FOUND_MSG = "[bold red]Could not find the klir binary in PATH.[/bold red]"
_REMOVED_MSG = "[green]Service removed.[/green]"
_STARTED_MSG = "[green]Service started.[/green]"
_STOPPED_MSG = "[green]Service stopped.[/green]"


def print_not_installed(console: Console) -> None:
    """Print the 'service not installed' hint."""
    console.print(_NOT_INSTALLED_MSG)


def print_not_running(console: Console) -> None:
    """Print the 'service is not running' hint."""
    console.print(_NOT_RUNNING_MSG)


def print_no_service(console: Console) -> None:
    """Print the 'no service installed' hint (for uninstall)."""
    console.print(_NO_SERVICE_MSG)


def print_binary_not_found(console: Console) -> None:
    """Print the 'klir binary not found' error."""
    console.print(_BINARY_NOT_FOUND_MSG)


def print_removed(console: Console) -> None:
    """Print the 'service removed' confirmation."""
    console.print(_REMOVED_MSG)


def print_started(console: Console) -> None:
    """Print the 'service started' confirmation."""
    console.print(_STARTED_MSG)


def print_stopped(console: Console) -> None:
    """Print the 'service stopped' confirmation."""
    console.print(_STOPPED_MSG)


def print_start_failed(console: Console, stderr: str) -> None:
    """Print a start-failure message with stderr detail."""
    console.print(f"[red]Failed to start: {stderr}[/red]")


def print_stop_failed(console: Console, stderr: str) -> None:
    """Print a stop-failure message with stderr detail."""
    console.print(f"[red]Failed to stop: {stderr}[/red]")


def print_install_success(
    console: Console,
    *,
    detail: str,
    logs_hint: str = "View recent logs",
) -> None:
    """Print the standard success panel after service installation.

    *detail* is the platform-specific restart/boot sentence (second line).
    *logs_hint* is the description next to ``klir service logs``.
    """
    console.print(
        Panel(
            "[bold green]klir is now running as a background service.[/bold green]\n\n"
            f"{detail}\n\n"
            "[bold]Useful commands:[/bold]\n\n"
            "  [cyan]klir service status[/cyan]     Check if it's running\n"
            "  [cyan]klir service stop[/cyan]       Stop the service\n"
            f"  [cyan]klir service logs[/cyan]       {logs_hint}\n"
            "  [cyan]klir service uninstall[/cyan]  Remove the service",
            title="[bold green]Service Installed[/bold green]",
            border_style="green",
            padding=(1, 2),
        ),
    )
