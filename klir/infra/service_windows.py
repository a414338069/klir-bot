"""Windows Task Scheduler service management for klir."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element, SubElement, tostring

from rich.panel import Panel

from klir.infra.service_base import (
    ensure_console,
    find_klir_binary,
    print_binary_not_found,
    print_install_success,
    print_no_service,
    print_not_installed,
    print_not_running,
    print_removed,
    print_start_failed,
    print_started,
    print_stop_failed,
    print_stopped,
)
from klir.infra.service_logs import print_file_service_logs
from klir.workspace.paths import resolve_paths

if TYPE_CHECKING:
    from rich.console import Console

logger = logging.getLogger(__name__)

_TASK_NAME = "klir"
_CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_ACCESS_DENIED_HINTS = ("access is denied", "zugriff verweigert", "zugriff wurde verweigert")

_ADMIN_HINT_PANEL = Panel(
    "[bold yellow]Administrator privileges required.[/bold yellow]\n\n"
    "Open a terminal as Administrator and try again:\n\n"
    "  [bold]1.[/bold] Right-click on CMD or PowerShell\n"
    "  [bold]2.[/bold] Select [cyan]'Run as administrator'[/cyan]\n"
    "  [bold]3.[/bold] Run: [cyan]klir service install[/cyan]",
    title="[bold yellow]Admin Required[/bold yellow]",
    border_style="yellow",
    padding=(1, 2),
)


def _run_schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a schtasks command."""
    cmd = ["schtasks.exe", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )


def _is_access_denied(result: subprocess.CompletedProcess[str]) -> bool:
    """Check if a schtasks result indicates an access-denied error."""
    text = (result.stderr + result.stdout).lower()
    return any(hint in text for hint in _ACCESS_DENIED_HINTS)


def _find_pythonw() -> str | None:
    """Find pythonw.exe for windowless execution.

    pythonw.exe is the GUI-subsystem Python interpreter that does not
    allocate a console window.  Ships with every standard CPython install.
    """
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    return None


def _generate_task_xml(command: str, arguments: str = "") -> str:
    """Generate the XML definition for a Windows Scheduled Task.

    Creates a task that:
    - Starts 10s after user logon
    - Restarts on failure (up to 3 times, 1 min interval)
    - Runs indefinitely without time limit
    - Runs as current user with lowest privileges
    """
    ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"

    task = Element("Task", version="1.4", xmlns=ns)

    # -- Registration info --
    reg = SubElement(task, "RegistrationInfo")
    SubElement(reg, "Description").text = "klir - Telegram bot powered by AI CLIs"

    # -- Triggers: start on logon --
    triggers = SubElement(task, "Triggers")
    logon = SubElement(triggers, "LogonTrigger")
    SubElement(logon, "Enabled").text = "true"
    SubElement(logon, "Delay").text = "PT10S"

    # -- Settings --
    settings = SubElement(task, "Settings")
    SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
    SubElement(settings, "DisallowStartIfOnBatteries").text = "false"
    SubElement(settings, "StopIfGoingOnBatteries").text = "false"
    SubElement(settings, "AllowHardTerminate").text = "true"
    SubElement(settings, "StartWhenAvailable").text = "true"
    SubElement(settings, "RunOnlyIfNetworkAvailable").text = "true"
    SubElement(settings, "ExecutionTimeLimit").text = "PT0S"
    SubElement(settings, "AllowStartOnDemand").text = "true"
    SubElement(settings, "Enabled").text = "true"
    SubElement(settings, "Hidden").text = "false"
    # Restart on failure
    restart = SubElement(settings, "RestartOnFailure")
    SubElement(restart, "Interval").text = "PT1M"
    SubElement(restart, "Count").text = "3"

    # -- Principal: run as current user, lowest privileges --
    principals = SubElement(task, "Principals")
    principal = SubElement(principals, "Principal", id="Author")
    SubElement(principal, "LogonType").text = "InteractiveToken"
    SubElement(principal, "RunLevel").text = "LeastPrivilege"

    # -- Action: execute command --
    actions = SubElement(task, "Actions", Context="Author")
    exe_action = SubElement(actions, "Exec")
    SubElement(exe_action, "Command").text = command
    if arguments:
        SubElement(exe_action, "Arguments").text = arguments

    body = tostring(task, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + body


def _task_xml_path() -> Path:
    """Temp path for the task XML definition."""
    return resolve_paths().klir_home / "klir_task.xml"


def is_service_available() -> bool:
    """Check if Windows Task Scheduler is available."""
    return sys.platform == "win32" and shutil.which("schtasks.exe") is not None


def is_service_installed() -> bool:
    """Check if the klir scheduled task exists."""
    result = _run_schtasks("/Query", "/TN", _TASK_NAME, "/FO", "LIST")
    return result.returncode == 0


def is_service_running() -> bool:
    """Check if the klir scheduled task is currently running."""
    if not is_service_installed():
        return False
    result = _run_schtasks("/Query", "/TN", _TASK_NAME, "/FO", "CSV", "/V")
    if result.returncode != 0:
        return False
    return "Running" in result.stdout


def install_service(console: Console | None = None) -> bool:
    """Install and start the klir scheduled task.

    Returns True on success.
    """
    console = ensure_console(console)

    if not is_service_available():
        console.print(
            "[bold red]Task Scheduler not available. Service install requires Windows.[/bold red]"
        )
        return False

    # Resolve command: prefer pythonw.exe (no console window) over klir binary
    pythonw = _find_pythonw()
    if pythonw:
        command = pythonw
        arguments = "-m klir"
    else:
        binary = find_klir_binary()
        if not binary:
            print_binary_not_found(console)
            return False
        command = binary
        arguments = ""
        console.print("[dim]Note: pythonw.exe not found. A console window may be visible.[/dim]")

    # Remove existing task if present (clean re-install)
    if is_service_installed():
        delete_result = _run_schtasks("/Delete", "/TN", _TASK_NAME, "/F")
        if delete_result.returncode != 0 and _is_access_denied(delete_result):
            console.print(_ADMIN_HINT_PANEL)
            return False

    # Write XML and create task
    xml_path = _task_xml_path()
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_content = _generate_task_xml(command, arguments)
    xml_path.write_text(xml_content, encoding="utf-16")
    logger.info("Task XML written: %s", xml_path)

    result = _run_schtasks("/Create", "/TN", _TASK_NAME, "/XML", str(xml_path), "/F")
    xml_path.unlink(missing_ok=True)

    if result.returncode != 0:
        if _is_access_denied(result):
            console.print(_ADMIN_HINT_PANEL)
        else:
            console.print(
                f"[bold red]Failed to create scheduled task:[/bold red] {result.stderr.strip()}"
            )
        return False

    logger.info("Scheduled task created: %s", _TASK_NAME)

    # Start it immediately
    run_result = _run_schtasks("/Run", "/TN", _TASK_NAME)
    if run_result.returncode != 0:
        console.print(
            f"[bold red]Task created but failed to start:[/bold red] {run_result.stderr.strip()}"
        )
        return False

    print_install_success(
        console,
        detail="It starts 10s after login and restarts on crash (up to 3 retries, 1 min apart).",
    )
    return True


def uninstall_service(console: Console | None = None) -> bool:
    """Stop and remove the klir scheduled task."""
    console = ensure_console(console)

    if not is_service_installed():
        print_no_service(console)
        return False

    # /End stops the running instance, /Delete removes the definition
    _run_schtasks("/End", "/TN", _TASK_NAME)
    result = _run_schtasks("/Delete", "/TN", _TASK_NAME, "/F")

    if result.returncode != 0:
        if _is_access_denied(result):
            console.print(_ADMIN_HINT_PANEL)
        else:
            console.print(f"[red]Failed to remove task: {result.stderr.strip()}[/red]")
        return False

    print_removed(console)
    return True


def start_service(console: Console | None = None) -> None:
    """Start the scheduled task."""
    console = ensure_console(console)

    if not is_service_installed():
        print_not_installed(console)
        return

    result = _run_schtasks("/Run", "/TN", _TASK_NAME)
    if result.returncode == 0:
        print_started(console)
    else:
        print_start_failed(console, result.stderr.strip())


def stop_service(console: Console | None = None) -> None:
    """Stop the scheduled task."""
    console = ensure_console(console)

    if not is_service_running():
        print_not_running(console)
        return

    result = _run_schtasks("/End", "/TN", _TASK_NAME)
    if result.returncode == 0:
        print_stopped(console)
    else:
        print_stop_failed(console, result.stderr.strip())


def print_service_status(console: Console | None = None) -> None:
    """Print the scheduled task status."""
    console = ensure_console(console)

    if not is_service_installed():
        print_not_installed(console)
        return

    result = _run_schtasks("/Query", "/TN", _TASK_NAME, "/FO", "LIST", "/V")
    if result.returncode == 0:
        console.print(result.stdout)
    else:
        console.print(f"[red]Could not query task status: {result.stderr.strip()}[/red]")


def print_service_logs(console: Console | None = None) -> None:
    """Show recent log output.

    Windows has no journalctl equivalent, so we tail the klir log file.
    """
    console = ensure_console(console)
    print_file_service_logs(
        console,
        installed=is_service_installed(),
        logs_dir=resolve_paths().logs_dir,
    )
