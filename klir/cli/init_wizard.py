"""Interactive onboarding wizard for first-time setup."""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from klir.cli.auth import AuthStatus, check_claude_auth, check_codex_auth, check_gemini_auth
from klir.config import DEFAULT_EMPTY_GEMINI_API_KEY, AgentConfig, deep_merge_config
from klir.workspace.init import init_workspace
from klir.workspace.paths import resolve_paths

_BANNER_PATH = Path(__file__).resolve().parent.parent / "_banner.txt"
logger = logging.getLogger(__name__)


def _load_banner() -> str:
    """Read ASCII art from bundled file."""
    try:
        return _BANNER_PATH.read_text(encoding="utf-8").rstrip()
    except OSError:
        return "klir"


_TOKEN_PATTERN = re.compile(r"^\d{8,}:[A-Za-z0-9_-]{30,}$")

_TIMEZONES: list[str] = [
    # Europe
    "Europe/Berlin",
    "Europe/London",
    "Europe/Paris",
    "Europe/Zurich",
    "Europe/Moscow",
    "Europe/Amsterdam",
    "Europe/Rome",
    "Europe/Madrid",
    # Americas
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "America/Toronto",
    # Asia & Middle East
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    # Oceania & Other
    "Australia/Sydney",
    "Pacific/Auckland",
    "UTC",
]

_MANUAL_TZ_OPTION = "-> Enter manually"


def _abort() -> NoReturn:
    """Print abort message and exit."""
    Console().print("\n[dim]Setup cancelled.[/dim]\n")
    sys.exit(0)


def _show_banner(console: Console) -> None:
    """Display the ASCII art banner."""
    banner = Text(_load_banner(), style="bold cyan")
    console.print(
        Panel(
            banner,
            subtitle="[dim]klir[/dim]",
            border_style="cyan",
            padding=(0, 2),
        ),
    )


_STATUS_ICON = {
    AuthStatus.AUTHENTICATED: "[bold green]authenticated[/bold green]",
    AuthStatus.INSTALLED: "[bold yellow]installed but not logged in[/bold yellow]",
    AuthStatus.NOT_FOUND: "[dim]not found[/dim]",
}


def _check_clis(console: Console) -> None:
    """Detect CLI availability and require at least one authenticated provider."""
    claude = check_claude_auth()
    codex = check_codex_auth()
    gemini = check_gemini_auth()

    lines = [
        "[bold]Detected AI Backends:[/bold]\n",
        f"  Claude Code CLI   {_STATUS_ICON[claude.status]}",
        f"  OpenAI Codex CLI  {_STATUS_ICON[codex.status]}",
        f"  Google Gemini CLI {_STATUS_ICON[gemini.status]}",
    ]

    has_auth = claude.is_authenticated or codex.is_authenticated or gemini.is_authenticated

    if has_auth:
        border = "green"
    else:
        border = "red"
        lines.append(
            "\n[bold red]At least one CLI must be installed and authenticated.[/bold red]\n\n"
            "  Claude: [dim]https://docs.anthropic.com/en/docs/claude-code[/dim]\n"
            "  Codex:  [dim]https://github.com/openai/codex[/dim]\n"
            "  Gemini: [dim]https://github.com/google-gemini/gemini-cli[/dim]"
        )

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]CLI Backends[/bold]",
            border_style=border,
            padding=(1, 2),
        ),
    )

    if not has_auth:
        console.print()
        _abort()


def _show_disclaimer(console: Console) -> None:
    """Display the risk disclaimer and require confirmation."""
    disclaimer = (
        "[bold]Important -- please read before continuing.[/bold]\n\n"
        "klir connects to [bold]Anthropic Claude CLI[/bold] and "
        "[bold]OpenAI Codex CLI[/bold] as AI agent backends.\n\n"
        "The bot operates in [bold yellow]full permission bypass mode[/bold yellow]. "
        "The agent can read, write, and delete files, execute commands, "
        "and interact with your system without asking for confirmation.\n\n"
        "While safeguards are in place, [bold red]unintended actions can occur[/bold red] "
        "-- including data loss, unexpected file changes, or unintended command execution."
    )
    console.print(
        Panel(
            disclaimer,
            title="[bold yellow]Disclaimer[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )

    accepted = questionary.confirm(
        "I understand the risks and want to continue.",
        default=False,
    ).ask()
    if not accepted:
        _abort()


def _ask_telegram_token(console: Console) -> str:
    """Prompt for the Telegram bot token with instructions."""
    instructions = (
        "[bold]How to get your Telegram Bot Token:[/bold]\n\n"
        "  1. Open Telegram and search for [bold cyan]@BotFather[/bold cyan]\n"
        "  2. Send [bold]/newbot[/bold] and follow the prompts\n"
        "  3. Choose a name and username for your bot\n"
        "  4. BotFather will reply with your bot token\n\n"
        "[dim]Token format: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz[/dim]"
    )
    console.print(
        Panel(
            instructions,
            title="[bold]Telegram Bot Token[/bold]",
            border_style="blue",
            padding=(1, 2),
        )
    )

    while True:
        token: str | None = questionary.text("Paste your bot token:").ask()
        if token is None:
            _abort()
        token = token.strip()
        if _TOKEN_PATTERN.match(token):
            return str(token)
        console.print(
            "[red]Invalid token format. Expected: digits:alphanumeric (e.g. 123456:ABC-xyz)[/red]"
        )


def _ask_user_id(console: Console) -> list[int]:
    """Prompt for the Telegram user ID with instructions."""
    instructions = (
        "[bold]How to find your Telegram User ID:[/bold]\n\n"
        "  1. Open Telegram and search for [bold cyan]@userinfobot[/bold cyan]\n"
        "  2. Send [bold]/start[/bold] to the bot\n"
        "  3. It will reply with your numeric user ID\n\n"
        "[dim]Only messages from this user ID will be accepted by the bot.[/dim]"
    )
    console.print(
        Panel(
            instructions, title="[bold]Telegram User ID[/bold]", border_style="blue", padding=(1, 2)
        )
    )

    while True:
        raw = questionary.text("Enter your numeric user ID:").ask()
        if raw is None:
            _abort()
        raw = raw.strip()
        try:
            uid = int(raw)
        except ValueError:
            console.print("[red]Please enter a valid number.[/red]")
            continue
        if uid <= 0:
            console.print("[red]User ID must be a positive number.[/red]")
            continue
        return [uid]


def _ask_timezone(console: Console) -> str:
    """Prompt for timezone selection."""
    console.print(
        Panel(
            "Your timezone is used for cron scheduling, heartbeat quiet hours,\n"
            "and daily session resets.",
            title="[bold]Timezone[/bold]",
            border_style="blue",
            padding=(1, 2),
        ),
    )

    choices = [*_TIMEZONES, _MANUAL_TZ_OPTION]
    selected: str | None = questionary.select("Select your timezone:", choices=choices).ask()
    if selected is None:
        _abort()

    if selected != _MANUAL_TZ_OPTION:
        return str(selected)

    while True:
        manual: str | None = questionary.text("Enter IANA timezone (e.g. Europe/Berlin):").ask()
        if manual is None:
            _abort()
        manual = manual.strip()
        try:
            ZoneInfo(manual)
        except (ZoneInfoNotFoundError, KeyError):
            console.print(f"[red]Unknown timezone: {manual}[/red]")
            continue
        return str(manual)


def _offer_service_install(console: Console) -> bool:
    """Ask whether to install klir as a background service."""
    from klir.infra.service import is_service_available

    if not is_service_available():
        return False

    is_windows = sys.platform == "win32"
    is_macos = sys.platform == "darwin"
    if is_windows:
        mechanism = "scheduled task"
        trigger = "login"
    elif is_macos:
        mechanism = "launch agent"
        trigger = "login"
    else:
        mechanism = "systemd service"
        trigger = "boot"

    console.print(
        Panel(
            f"[bold]Run klir as a background service?[/bold]\n\n"
            f"This creates a {mechanism} that:\n\n"
            f"  - Starts klir on {trigger}\n"
            "  - Restarts automatically on crash\n"
            "  - Keeps running in the background\n\n"
            "[dim]Recommended for VPS or always-on setups.[/dim]",
            title="[bold]Background Service[/bold]",
            border_style="blue",
            padding=(1, 2),
        ),
    )

    enabled: bool | None = questionary.confirm(
        "Install as background service? (Recommended for VPS)",
        default=True,
    ).ask()
    if enabled is None:
        _abort()
    console.print()
    return bool(enabled)


_TAILSCALE_CHOICES = [
    questionary.Choice("Off — localhost only", value="off"),
    questionary.Choice("Serve — private HTTPS for your tailnet", value="serve"),
    questionary.Choice("Funnel — public HTTPS via Tailscale", value="funnel"),
]


def _ask_dashboard_access(console: Console) -> dict[str, object]:
    """Ask how the dashboard should be exposed and return API config overrides."""
    from klir.infra.tailscale import detect_tailscale

    has_tailscale = detect_tailscale()

    if has_tailscale:
        console.print(
            Panel(
                "[bold]Tailscale detected.[/bold]\n\n"
                "klir includes a web dashboard (Mission Control) for monitoring\n"
                "sessions, cron jobs, and agents.\n\n"
                "  [bold]Off[/bold]      Dashboard on localhost only (http://localhost:8741)\n"
                "  [bold]Serve[/bold]    Private HTTPS via your tailnet (recommended)\n"
                "  [bold]Funnel[/bold]   Public HTTPS via Tailscale Funnel",
                title="[bold]Dashboard Access[/bold]",
                border_style="blue",
                padding=(1, 2),
            ),
        )

        mode: str | None = questionary.select(
            "How should the dashboard be exposed?",
            choices=_TAILSCALE_CHOICES,
            default="serve",
        ).ask()
        if mode is None:
            _abort()

        overrides: dict[str, object] = {}
        if mode in ("serve", "funnel"):
            # Tailscale proxies to localhost — bind loopback
            overrides["host"] = "127.0.0.1"
            overrides["tailscale"] = {"mode": mode, "reset_on_exit": True}
            overrides["allow_public"] = mode == "funnel"
        else:
            overrides["host"] = "127.0.0.1"
            overrides["tailscale"] = {"mode": "off", "reset_on_exit": True}

        console.print()
        return overrides

    # No Tailscale detected
    console.print(
        Panel(
            "[bold]Dashboard Access[/bold]\n\n"
            "klir includes a web dashboard for monitoring sessions,\n"
            "cron jobs, and agents.\n\n"
            "  [bold]Localhost[/bold]   Dashboard on http://localhost:8741 only\n"
            "  [bold]Network[/bold]    Bind to all interfaces (use with VPN/firewall)\n\n"
            "[dim]Install Tailscale for secure private HTTPS access.[/dim]",
            title="[bold]Dashboard Access[/bold]",
            border_style="blue",
            padding=(1, 2),
        ),
    )

    bind_all: bool | None = questionary.confirm(
        "Expose dashboard on all network interfaces?",
        default=False,
    ).ask()
    if bind_all is None:
        _abort()

    overrides = {}
    if bind_all:
        overrides["host"] = "0.0.0.0"  # noqa: S104
        overrides["allow_public"] = True
    else:
        overrides["host"] = "127.0.0.1"

    overrides["tailscale"] = {"mode": "off", "reset_on_exit": True}
    console.print()
    return overrides


def _write_config(
    *,
    telegram_token: str,
    allowed_user_ids: list[int],
    user_timezone: str,
    api_overrides: dict[str, object] | None = None,
) -> Path:
    """Write the config file with wizard values merged into defaults."""
    paths = resolve_paths()
    config_path = paths.config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        try:
            existing: dict[str, object] = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Ignoring invalid config file during onboarding: %s", config_path)
            existing = {}
    else:
        existing = {}

    defaults = AgentConfig().model_dump(mode="json")
    defaults["gemini_api_key"] = DEFAULT_EMPTY_GEMINI_API_KEY
    merged, _ = deep_merge_config(existing, defaults)
    if merged.get("gemini_api_key") is None:
        merged["gemini_api_key"] = DEFAULT_EMPTY_GEMINI_API_KEY

    merged["telegram_token"] = telegram_token
    merged["allowed_user_ids"] = allowed_user_ids
    merged["user_timezone"] = user_timezone

    # Ensure API token exists
    import secrets as _secrets

    api = merged.get("api", {})
    if not isinstance(api, dict):
        api = {}
    if not api.get("token"):
        api["token"] = _secrets.token_urlsafe(32)

    # Apply dashboard access overrides from wizard
    if api_overrides:
        api.update(api_overrides)

    merged["api"] = api

    from klir.infra.json_store import atomic_json_save

    atomic_json_save(config_path, merged)

    init_workspace(paths)
    return config_path


def run_onboarding() -> bool:
    """Run onboarding and return True only when service install succeeded."""
    console = Console()
    console.print()
    _show_banner(console)

    _check_clis(console)
    console.print()

    _show_disclaimer(console)
    console.print()

    token = _ask_telegram_token(console)
    console.print()

    user_ids = _ask_user_id(console)
    console.print()

    timezone = _ask_timezone(console)
    console.print()

    api_overrides = _ask_dashboard_access(console)

    config_path = _write_config(
        telegram_token=token,
        allowed_user_ids=user_ids,
        user_timezone=timezone,
        api_overrides=api_overrides,
    )

    paths = resolve_paths()

    # Offer background service setup on Linux with systemd
    run_as_service = _offer_service_install(console)

    # Read back the API config for display
    api_cfg: dict[str, object] = {}
    try:
        final_config = json.loads(config_path.read_text(encoding="utf-8"))
        raw_api = final_config.get("api", {})
        if isinstance(raw_api, dict):
            api_cfg = raw_api
    except (json.JSONDecodeError, OSError):
        pass

    api_token = str(api_cfg.get("token", ""))
    ts_raw = api_cfg.get("tailscale", {})
    ts_mode = ts_raw.get("mode", "off") if isinstance(ts_raw, dict) else "off"

    api_token_line = f"  Token:      [cyan]{api_token}[/cyan]\n" if api_token else ""

    if ts_mode in ("serve", "funnel"):
        import asyncio

        from klir.infra.tailscale import get_tailnet_hostname

        hostname = asyncio.run(get_tailnet_hostname())
        if hostname:
            url_line = f"  URL:        [cyan]https://{hostname}/dashboard/[/cyan]\n"
        else:
            url_line = "  URL:        [cyan]https://<tailnet-host>/dashboard/[/cyan]\n"
        url_note = f"  [dim]Tailscale {ts_mode} provides HTTPS access.[/dim]\n"
    else:
        port = api_cfg.get("port", 8741)
        url_line = f"  URL:        [cyan]http://localhost:{port}/dashboard/[/cyan]\n"
        url_note = ""

    console.print(
        Panel(
            "[bold green]Setup complete![/bold green]\n\n"
            "[bold]Your klir files:[/bold]\n\n"
            f"  Home:       [cyan]{paths.klir_home}[/cyan]\n"
            f"  Config:     [cyan]{config_path}[/cyan]\n"
            f"  Workspace:  [cyan]{paths.workspace}[/cyan]\n"
            f"  Logs:       [cyan]{paths.logs_dir}[/cyan]\n\n"
            "[bold]Dashboard:[/bold]\n\n"
            + url_line
            + api_token_line
            + url_note
            + "\n"
            + ("Installing service..." if run_as_service else "Starting bot..."),
            title="[bold green]Ready[/bold green]",
            border_style="green",
            padding=(1, 2),
        ),
    )
    console.print()

    service_installed = False
    if run_as_service:
        from klir.infra.service import install_service

        service_installed = install_service(console)

    return service_installed


def run_smart_reset(klir_home: Path) -> None:
    """Delete workspace after confirmation."""
    console = Console()
    console.print()

    # Warning panel
    console.print(
        Panel(
            "[bold yellow]You already have a configured setup.[/bold yellow]\n\n"
            "Re-running onboarding will perform a [bold red]full reset[/bold red]:\n\n"
            f"  [dim]{klir_home}[/dim] will be deleted entirely.\n"
            "  All sessions, configs, memory, and cron tasks will be lost.",
            title="[bold yellow]Existing Setup Detected[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        ),
    )

    # Final confirmation
    console.print()
    confirmed: bool | None = questionary.confirm(
        "Delete everything and start fresh?",
        default=False,
    ).ask()
    if not confirmed:
        _abort()

    from klir.infra.fs import robust_rmtree

    robust_rmtree(klir_home)
    if klir_home.exists():
        console.print(
            f"[yellow]Warning: Could not fully delete {klir_home}. Remove manually.[/yellow]\n"
        )
    else:
        console.print("[dim]Workspace deleted.[/dim]\n")
