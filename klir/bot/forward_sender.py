"""Execute forward/copy message directives via Telegram Bot API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message, MessageId

    from klir.bot.forward_parser import ForwardDirective

logger = logging.getLogger(__name__)


async def send_forward(
    bot: Bot,
    from_chat_id: int,
    directive: ForwardDirective,
    *,
    allowed_targets: set[int],
) -> Message | MessageId | None:
    """Forward or copy a message. Returns None if blocked or on failure."""
    if directive.chat_id not in allowed_targets:
        logger.warning(
            "Blocked %s to unauthorized chat %d",
            directive.mode,
            directive.chat_id,
        )
        return None

    try:
        if directive.mode == "copy":
            return await bot.copy_message(
                chat_id=directive.chat_id,
                from_chat_id=from_chat_id,
                message_id=directive.message_id,
            )
        return await bot.forward_message(
            chat_id=directive.chat_id,
            from_chat_id=from_chat_id,
            message_id=directive.message_id,
        )
    except Exception:
        logger.warning(
            "Failed to %s message %d to %d",
            directive.mode,
            directive.message_id,
            directive.chat_id,
            exc_info=True,
        )
        return None
