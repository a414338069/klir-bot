"""Proxy URL resolution for Telegram API connections."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

if TYPE_CHECKING:
    from klir.config import AgentConfig

logger = logging.getLogger(__name__)

_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


def sanitize_proxy_url(url: str) -> str:
    """Strip credentials from a proxy URL for safe logging."""
    parsed = urlparse(url)
    if not parsed.username:
        return url
    host = parsed.hostname or ""
    port_suffix = f":{parsed.port}" if parsed.port else ""
    return urlunparse(parsed._replace(netloc=f"***@{host}{port_suffix}"))


def resolve_proxy_url(config: AgentConfig) -> str | None:
    """Resolve proxy URL: config > env vars > None."""
    if config.proxy.is_configured:
        logger.info("Using proxy from config: %s", sanitize_proxy_url(config.proxy.url))
        return config.proxy.url

    for var in _ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            logger.info("Using proxy from %s: %s", var, sanitize_proxy_url(value))
            return value

    return None
