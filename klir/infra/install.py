"""Detect how klir was installed (pipx, pip, or dev/source)."""

from __future__ import annotations

import json
import logging
import sys
from importlib.metadata import distribution
from typing import Literal

logger = logging.getLogger(__name__)

InstallMode = Literal["uv", "pipx", "pip", "dev"]

_PACKAGE_NAME = "klir-bot"


def detect_install_mode() -> InstallMode:
    """Detect installation method at runtime.

    Returns:
        ``"pipx"`` -- installed via ``pipx install klir``
        ``"pip"``  -- installed via ``pip install klir`` (from PyPI)
        ``"dev"``  -- editable install (``pip install -e .``) or running from source
    """
    # uv tool install creates venvs under .../uv/tools/<pkg>/
    prefix_lower = sys.prefix.lower().replace("\\", "/")
    if "uv/tools" in prefix_lower:
        return "uv"

    if "pipx" in sys.prefix:
        return "pipx"

    try:
        dist = distribution(_PACKAGE_NAME)
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            url_info = json.loads(direct_url_text)
            if url_info.get("dir_info", {}).get("editable", False):
                return "dev"
    except Exception:
        return "dev"

    return "pip"


def is_upgradeable() -> bool:
    """Return True if the bot can self-upgrade (pipx or pip, not dev)."""
    return detect_install_mode() != "dev"
