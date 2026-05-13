"""Factory for aiogram Bot sessions with optional proxy and retry support."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiogram.client.session.aiohttp import AiohttpSession

from klir.config import ResilienceConfig
from klir.infra.proxy import sanitize_proxy_url

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.methods import TelegramMethod
    from aiogram.methods.base import TelegramType

logger = logging.getLogger(__name__)


class ResilientSession(AiohttpSession):
    """AiohttpSession subclass that retries recoverable Telegram errors."""

    def __init__(
        self,
        *,
        resilience_config: ResilienceConfig,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> None:
        if proxy:
            kwargs["proxy"] = proxy
        super().__init__(**kwargs)
        self._resilience_config = resilience_config

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> TelegramType:
        """Wrap the parent make_request with retry logic."""
        from klir.bot.retry import retry_async

        return await retry_async(
            super().make_request,
            bot,
            method,
            timeout,
            config=self._resilience_config,
            context=type(method).__name__,
        )


def create_bot_session(
    proxy_url: str | None,
    resilience_config: ResilienceConfig | None = None,
) -> AiohttpSession | None:
    """Create an aiogram session with optional proxy and retry support."""
    if resilience_config is not None:
        if proxy_url:
            logger.info("Creating resilient proxied bot session: %s", sanitize_proxy_url(proxy_url))
        else:
            logger.info("Creating resilient bot session")
        return ResilientSession(
            resilience_config=resilience_config,
            proxy=proxy_url or None,
        )

    if proxy_url:
        logger.info("Creating proxied bot session: %s", sanitize_proxy_url(proxy_url))
        return AiohttpSession(proxy=proxy_url)

    return None
