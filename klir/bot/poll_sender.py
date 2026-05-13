"""Send Telegram polls from parsed directives."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

    from klir.bot.poll_parser import PollDirective

logger = logging.getLogger(__name__)


async def send_poll(
    bot: Bot,
    chat_id: int,
    directive: PollDirective,
    *,
    thread_id: int | None = None,
    is_anonymous: bool = True,
) -> Message | None:
    """Send a Telegram poll. Returns the sent Message or None on failure."""
    try:
        return await bot.send_poll(
            chat_id=chat_id,
            question=directive.question,
            options=list(directive.options),
            is_anonymous=is_anonymous,
            allows_multiple_answers=directive.allows_multiple,
            message_thread_id=thread_id,
        )
    except Exception:
        logger.warning("Failed to send poll: %s", directive.question, exc_info=True)
        return None
