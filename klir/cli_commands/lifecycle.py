"""Bot lifecycle CLI commands (stop, start, restart, uninstall, upgrade)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from typing import NoReturn

from rich.console import Console
from rich.panel import Panel

from klir.infra.fs import robust_rmtree
from klir.infra.platform import is_windows
from klir.infra.restart import EXIT_RESTART
from klir.workspace.paths import resolve_paths

_console = Console()


def _re_exec_bot() -> NoReturn:
    """Re-exec the bot process (cross-platform).

    Spawns a new Python process running ``klir`` and exits the current one.
    Under a service manager the caller should ``sys.exit(EXIT_RESTART)`` instead.
    """
    subprocess.Popen([sys.executable, "-m", "klir"])
    sys.exit(0)


def _stop_service_if_running() -> None:
    """Stop the system service if installed and running."""
    import contextlib

    with contextlib.suppress(Exception):
        from klir.infra.service import is_service_installed, is_service_running, stop_service

        if is_service_installed() and is_service_running():
            stop_service(_console)


def stop_bot() -> None:
    """Stop all running klir instances.

    1. Stop the system service (prevents Task Scheduler/systemd/launchd respawn)
    2. Kill the PID-file instance
    3. Kill any remaining klir processes system-wide
    4. Wait for file locks to release (Windows only)
    """
    from klir.infra.pidlock import _is_process_alive, _kill_and_wait

    # 1. Stop service to prevent respawn
    _stop_service_if_running()

    # 2. Kill PID-file instance
    paths = resolve_paths()
    pid_file = paths.klir_home / "bot.pid"
    stopped = False

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = None
        if pid is not None and _is_process_alive(pid):
            _console.print(f"[dim]Stopping bot (pid={pid})...[/dim]")
            _kill_and_wait(pid)
            pid_file.unlink(missing_ok=True)
            _console.print("[green]Bot stopped.[/green]")
            stopped = True
        else:
            pid_file.unlink(missing_ok=True)

    # 3. Kill all remaining klir processes system-wide
    from klir.infra.process_tree import kill_all_klir_processes

    extra = kill_all_klir_processes()
    if extra:
        _console.print(f"[dim]Killed {extra} remaining klir process(es).[/dim]")
        stopped = True

    if not stopped:
        _console.print("[dim]No running bot instance found.[/dim]")

    # 4. Brief wait for file locks to release on Windows
    if is_windows() and stopped:
        time.sleep(1.0)


def start_bot(verbose: bool = False) -> None:
    """Load config and start the Telegram bot."""
    import logging

    from klir.__main__ import load_config, run_telegram
    from klir.logging_config import setup_logging

    paths = resolve_paths()
    setup_logging(verbose=verbose, log_dir=paths.logs_dir)
    config = load_config()
    if not verbose:
        config_level = getattr(logging, config.log_level.upper(), logging.INFO)
        if config_level != logging.INFO:
            setup_logging(level=config_level, log_dir=paths.logs_dir)
    try:
        exit_code = asyncio.run(run_telegram(config))
    except KeyboardInterrupt:
        exit_code = 0
    if exit_code == EXIT_RESTART:
        if os.environ.get("KLIR_SUPERVISOR") or os.environ.get("INVOCATION_ID"):
            sys.exit(EXIT_RESTART)
        _re_exec_bot()
    elif exit_code:
        sys.exit(exit_code)


def cmd_restart() -> None:
    """Stop and re-exec the bot."""
    stop_bot()
    _re_exec_bot()


def uninstall() -> None:
    """Full uninstall: stop bot, delete workspace, uninstall package."""
    import questionary

    _console.print()
    _console.print(
        Panel(
            "[bold red]This will permanently remove klir from your system.[/bold red]\n\n"
            "  1. Stop the running bot (if active)\n"
            "  2. Delete all data in ~/.klir/\n"
            "  3. Uninstall the klir package",
            title="[bold red]Uninstall klir[/bold red]",
            border_style="red",
            padding=(1, 2),
        ),
    )

    confirmed: bool | None = questionary.confirm(
        "Are you sure you want to uninstall everything?",
        default=False,
    ).ask()
    if not confirmed:
        _console.print("\n[dim]Uninstall cancelled.[/dim]\n")
        return

    # 1. Stop bot + all klir processes
    stop_bot()

    # 2. Delete workspace
    paths = resolve_paths()
    klir_home = paths.klir_home
    if klir_home.exists():
        robust_rmtree(klir_home)
        if klir_home.exists():
            _console.print(
                f"[yellow]Warning: Could not fully delete {klir_home} "
                "(some files may be locked). Remove manually.[/yellow]"
            )
        else:
            _console.print(f"[green]Deleted {klir_home}[/green]")

    # 4. Uninstall package
    _console.print("[dim]Uninstalling klir package...[/dim]")
    if shutil.which("pipx"):
        subprocess.run(
            ["pipx", "uninstall", "klir"],
            capture_output=True,
            check=False,
        )
    else:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", "klir"],
            capture_output=True,
            check=False,
        )

    _console.print(
        Panel(
            "[bold green]klir has been completely removed.[/bold green]\n\n"
            "Thank you for using klir!",
            title="[bold green]Uninstalled[/bold green]",
            border_style="green",
            padding=(1, 2),
        ),
    )
    _console.print()


def upgrade() -> None:
    """Stop bot, upgrade package, restart."""
    from klir.infra.install import detect_install_mode
    from klir.infra.updater import perform_upgrade_pipeline
    from klir.infra.version import get_current_version

    mode = detect_install_mode()
    if mode == "dev":
        _console.print(
            Panel(
                "[bold yellow]Running from source (editable install).[/bold yellow]\n\n"
                "Self-upgrade is not available.\n"
                "Update with [bold]git pull[/bold] in your project directory.",
                title="[bold]Upgrade[/bold]",
                border_style="yellow",
                padding=(1, 2),
            ),
        )
        return

    _console.print()
    _console.print(
        Panel(
            "[bold cyan]Upgrading klir...[/bold cyan]\n\n"
            "  1. Stop running bot gracefully\n"
            "  2. Upgrade to latest version\n"
            "  3. Restart",
            title="[bold]Upgrade[/bold]",
            border_style="cyan",
            padding=(1, 2),
        ),
    )

    current = get_current_version()

    # 1. Graceful stop
    stop_bot()

    # 2. Upgrade + verification pipeline
    _console.print("[dim]Upgrading package...[/dim]")
    changed, actual, output = asyncio.run(
        perform_upgrade_pipeline(current_version=current),
    )
    if output:
        _console.print(f"[dim]{output}[/dim]")

    if not changed:
        _console.print(
            f"[bold yellow]Version unchanged after upgrade ({actual}).[/bold yellow]\n"
            "Automatic retry was attempted, but no new installed version could be verified yet."
        )
        return

    _console.print(f"[green]Upgrade complete: {current} -> {actual}[/green]")

    # 3. Re-exec with new version
    _console.print("[dim]Restarting...[/dim]")
    _re_exec_bot()
