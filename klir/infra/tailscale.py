"""Tailscale detection, serve/funnel management."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_MACOS_APP_PATH = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def find_tailscale_binary() -> str | None:
    """Locate the ``tailscale`` binary using multiple strategies.

    1. PATH lookup via ``shutil.which``
    2. Known macOS app bundle path
    """
    # Strategy 1: PATH
    found = shutil.which("tailscale")
    if found:
        return found

    # Strategy 2: macOS app bundle
    if sys.platform == "darwin" and Path(_MACOS_APP_PATH).is_file():
        return _MACOS_APP_PATH

    return None


def detect_tailscale() -> bool:
    """Return True if the ``tailscale`` binary is available."""
    return find_tailscale_binary() is not None


async def get_tailnet_hostname() -> str | None:  # noqa: PLR0911
    """Return the Tailscale DNS hostname for this machine, or None."""
    binary = find_tailscale_binary()
    if not binary:
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (TimeoutError, OSError):
        return None

    if proc.returncode != 0 or not stdout:
        return None

    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    self_info = data.get("Self")
    if not isinstance(self_info, dict):
        return None

    dns = self_info.get("DNSName", "")
    if isinstance(dns, str) and dns:
        return dns.rstrip(".")

    ips = self_info.get("TailscaleIPs", [])
    if isinstance(ips, list) and ips:
        return str(ips[0])

    return None


async def _run_tailscale(*args: str) -> tuple[int, str, str]:
    """Run a tailscale command and return (returncode, stdout, stderr).

    Uses ``asyncio.create_subprocess_exec`` (no shell) to avoid injection.
    """
    binary = find_tailscale_binary() or "tailscale"
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    except (TimeoutError, OSError) as exc:
        return 1, "", str(exc)
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace") if stdout else "",
        stderr.decode("utf-8", errors="replace") if stderr else "",
    )


def _validate_port(port: int) -> None:
    """Raise ValueError if port is outside the valid range."""
    if not 1 <= port <= 65535:
        msg = f"Port must be 1-65535, got {port}"
        raise ValueError(msg)


async def enable_tailscale_serve(port: int) -> bool:
    """Enable ``tailscale serve`` for the given port. Returns True on success."""
    _validate_port(port)
    rc, stdout, stderr = await _run_tailscale("serve", "--bg", "--yes", str(port))
    if rc != 0:
        logger.warning("tailscale serve failed (rc=%d): %s", rc, stderr or stdout)
        return False
    logger.info("tailscale serve enabled on port %d", port)
    return True


async def disable_tailscale_serve() -> None:
    """Reset ``tailscale serve`` configuration."""
    rc, stdout, stderr = await _run_tailscale("serve", "reset")
    if rc != 0:
        logger.warning("tailscale serve reset failed (rc=%d): %s", rc, stderr or stdout)
    else:
        logger.info("tailscale serve reset")


async def enable_tailscale_funnel(port: int) -> bool:
    """Enable ``tailscale funnel`` for the given port. Returns True on success."""
    _validate_port(port)
    rc, stdout, stderr = await _run_tailscale("funnel", "--bg", "--yes", str(port))
    if rc != 0:
        logger.warning("tailscale funnel failed (rc=%d): %s", rc, stderr or stdout)
        return False
    logger.info("tailscale funnel enabled on port %d", port)
    return True


async def disable_tailscale_funnel() -> None:
    """Reset ``tailscale funnel`` configuration."""
    rc, stdout, stderr = await _run_tailscale("funnel", "reset")
    if rc != 0:
        logger.warning("tailscale funnel reset failed (rc=%d): %s", rc, stderr or stdout)
    else:
        logger.info("tailscale funnel reset")


async def start_tailscale_exposure(
    mode: str,
    port: int,
) -> str | None:
    """Start Tailscale serve or funnel. Returns the tailnet hostname on success."""
    if mode == "off":
        return None

    if mode == "serve":
        ok = await enable_tailscale_serve(port)
    elif mode == "funnel":
        ok = await enable_tailscale_funnel(port)
    else:
        logger.warning("Unknown tailscale mode: %s", mode)
        return None

    if not ok:
        return None

    hostname = await get_tailnet_hostname()
    if hostname:
        logger.info("Tailscale %s → https://%s (port %d)", mode, hostname, port)
    return hostname


async def stop_tailscale_exposure(mode: str) -> None:
    """Reset Tailscale serve or funnel."""
    if mode == "serve":
        await disable_tailscale_serve()
    elif mode == "funnel":
        await disable_tailscale_funnel()
