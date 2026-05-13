"""API server management CLI subcommands (``klir api ...``)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from klir.workspace.paths import resolve_paths

_console = Console()

_API_SUBCOMMANDS = frozenset({"token", "dashboard"})


def _parse_api_subcommand(args: list[str]) -> str | None:
    """Extract the subcommand after 'api' from CLI args."""
    found = False
    for a in args:
        if a.startswith("-"):
            continue
        if not found and a == "api":
            found = True
            continue
        if found:
            return a if a in _API_SUBCOMMANDS else None
    return None


def print_api_help() -> None:
    """Print the API subcommand help table with current status."""
    _console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold green", min_width=30)
    table.add_column()
    table.add_row("klir api token", "Show your API token and dashboard URL")
    table.add_row("klir api dashboard", "Change dashboard access (Tailscale/network)")

    # Show current status
    paths = resolve_paths()
    status = "[dim]not configured[/dim]"
    if paths.config_path.exists():
        try:
            data = json.loads(paths.config_path.read_text(encoding="utf-8"))
            api_cfg = data.get("api", {})
            if isinstance(api_cfg, dict) and api_cfg.get("enabled"):
                port = api_cfg.get("port", 8741)
                status = f"[green]enabled[/green] (port {port})"
            elif isinstance(api_cfg, dict):
                status = "[dim]disabled[/dim]"
        except (json.JSONDecodeError, OSError):
            pass

    _console.print(
        Panel(
            table,
            title="[bold]API Commands[/bold] [dim](beta)[/dim]",
            border_style="blue",
            padding=(1, 0),
        ),
    )
    _console.print(f"  Status: {status}")
    _console.print()


def _read_config() -> tuple[Path, dict[str, object]] | None:
    """Read the config file and return (path, data) or None on failure."""
    paths = resolve_paths()
    config_path = paths.config_path
    if not config_path.exists():
        _console.print("[red]Config file not found. Run klir first.[/red]")
        return None
    try:
        data: dict[str, object] = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _console.print("[red]Failed to read config file.[/red]")
        return None
    return config_path, data


def api_token() -> None:
    """Display the current API token and dashboard URL."""
    result = _read_config()
    if result is None:
        return
    _config_path, data = result

    api = data.get("api", {})
    if not isinstance(api, dict):
        api = {}
    token = api.get("token", "")
    if not token:
        _console.print("[dim]No API token configured. Run [bold]klir[/bold] to set up.[/dim]")
        return

    port = api.get("port", 8741)
    ts_cfg = api.get("tailscale", {})
    ts_mode = ts_cfg.get("mode", "off") if isinstance(ts_cfg, dict) else "off"

    lines = [f"  Token:      [cyan]{token}[/cyan]"]

    if ts_mode in ("serve", "funnel"):
        import asyncio

        from klir.infra.tailscale import get_tailnet_hostname

        hostname = asyncio.run(get_tailnet_hostname())
        if hostname:
            lines.append(f"  URL:        [cyan]https://{hostname}/dashboard/[/cyan]")
        else:
            lines.append(f"  Local URL:  [cyan]http://localhost:{port}/dashboard/[/cyan]")
        lines.append(f"  Tailscale:  [green]{ts_mode}[/green]")
    else:
        lines.append(f"  URL:        [cyan]http://localhost:{port}/dashboard/[/cyan]")

    _console.print(
        Panel(
            "\n".join(lines),
            title="[bold]API Access[/bold]",
            border_style="blue",
            padding=(1, 2),
        ),
    )


_TAILSCALE_CHOICES = [
    {"name": "Off — localhost only", "value": "off"},
    {"name": "Serve — private HTTPS for your tailnet", "value": "serve"},
    {"name": "Funnel — public HTTPS via Tailscale", "value": "funnel"},
]

_NETWORK_CHOICES = [
    {"name": "Localhost — dashboard on 127.0.0.1 only", "value": "localhost"},
    {"name": "Network — bind to all interfaces (use with VPN/firewall)", "value": "network"},
]


def _ask_tailscale_mode(api: dict[str, object], current_mode: str) -> str | None:
    """Prompt for Tailscale mode selection. Returns chosen mode or None on cancel."""
    import questionary

    _console.print(
        Panel(
            f"[bold]Current mode:[/bold] [cyan]{current_mode}[/cyan]\n\n"
            "  [bold]Off[/bold]      Dashboard on localhost only\n"
            "  [bold]Serve[/bold]    Private HTTPS via your tailnet\n"
            "  [bold]Funnel[/bold]   Public HTTPS via Tailscale Funnel",
            title="[bold]Dashboard Access[/bold]",
            border_style="blue",
            padding=(1, 2),
        ),
    )

    choices = [questionary.Choice(c["name"], value=c["value"]) for c in _TAILSCALE_CHOICES]
    mode: str | None = questionary.select(
        "How should the dashboard be exposed?",
        choices=choices,
        default=current_mode,
    ).ask()
    if mode is None:
        return None

    if mode in ("serve", "funnel"):
        api["host"] = "127.0.0.1"
        api["tailscale"] = {"mode": mode, "reset_on_exit": True}
        api["allow_public"] = mode == "funnel"
    else:
        api["host"] = "127.0.0.1"
        api["tailscale"] = {"mode": "off", "reset_on_exit": True}
        api.pop("allow_public", None)
    return mode


def _ask_network_binding(api: dict[str, object]) -> str | None:
    """Prompt for network binding when Tailscale is unavailable. Returns mode or None."""
    import questionary

    current_host = api.get("host", "127.0.0.1")
    current_binding = "network" if current_host == "0.0.0.0" else "localhost"  # noqa: S104

    _console.print(
        Panel(
            f"[bold]Current binding:[/bold] [cyan]{current_binding}[/cyan]\n\n"
            "  [bold]Localhost[/bold]   Dashboard on 127.0.0.1 only\n"
            "  [bold]Network[/bold]    Bind to all interfaces (0.0.0.0)\n\n"
            "[dim]Install Tailscale for secure private HTTPS access.[/dim]",
            title="[bold]Dashboard Access[/bold]",
            border_style="blue",
            padding=(1, 2),
        ),
    )

    choices = [questionary.Choice(c["name"], value=c["value"]) for c in _NETWORK_CHOICES]
    binding: str | None = questionary.select(
        "How should the dashboard be exposed?",
        choices=choices,
        default=current_binding,
    ).ask()
    if binding is None:
        return None

    if binding == "network":
        api["host"] = "0.0.0.0"  # noqa: S104
        api["allow_public"] = True
    else:
        api["host"] = "127.0.0.1"
        api.pop("allow_public", None)
    api["tailscale"] = {"mode": "off", "reset_on_exit": True}
    return "off"


def _show_dashboard_result(api: dict[str, object]) -> None:
    """Display the saved dashboard configuration."""
    import asyncio

    from klir.infra.tailscale import get_tailnet_hostname

    port = api.get("port", 8741)
    ts_cfg = api.get("tailscale", {})
    final_mode = ts_cfg.get("mode", "off") if isinstance(ts_cfg, dict) else "off"

    if final_mode in ("serve", "funnel"):
        hostname = asyncio.run(get_tailnet_hostname())
        url = (
            f"https://{hostname}/dashboard/" if hostname else f"http://localhost:{port}/dashboard/"
        )
        _console.print(
            f"\n  [green]✓[/green] Dashboard set to [bold]{final_mode}[/bold]"
            f"\n  URL: [cyan]{url}[/cyan]"
            "\n\n  [dim]Restart klir for changes to take effect.[/dim]"
        )
    else:
        host = api.get("host", "127.0.0.1")
        _console.print(
            f"\n  [green]✓[/green] Dashboard set to [bold]localhost[/bold] ({host})"
            f"\n  URL: [cyan]http://localhost:{port}/dashboard/[/cyan]"
            "\n\n  [dim]Restart klir for changes to take effect.[/dim]"
        )


def api_dashboard() -> None:
    """Interactively reconfigure dashboard access (Tailscale mode or network binding)."""
    from klir.infra.json_store import atomic_json_save
    from klir.infra.tailscale import detect_tailscale

    result = _read_config()
    if result is None:
        return
    config_path, data = result

    api = data.get("api", {})
    if not isinstance(api, dict):
        api = {}

    ts_cfg = api.get("tailscale", {})
    current_mode = ts_cfg.get("mode", "off") if isinstance(ts_cfg, dict) else "off"

    if detect_tailscale():
        mode = _ask_tailscale_mode(api, current_mode)
    else:
        mode = _ask_network_binding(api)

    if mode is None:
        return

    data["api"] = api
    atomic_json_save(config_path, data)
    _show_dashboard_result(api)


def cmd_api(args: list[str]) -> None:
    """Handle 'klir api <subcommand>'."""
    sub = _parse_api_subcommand(args)
    if sub is None:
        print_api_help()
        return

    dispatch: dict[str, Callable[[], None]] = {
        "token": api_token,
        "dashboard": api_dashboard,
    }
    _console.print()
    dispatch[sub]()
    _console.print()
