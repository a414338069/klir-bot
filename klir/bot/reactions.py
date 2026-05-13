"""Telegram message reaction service — fire-and-forget emoji feedback."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot

    from klir.config import AgentConfig, ReactionConfig

logger = logging.getLogger(__name__)


class ReactionService:
    """Send emoji reactions to acknowledge, complete, or flag errors on messages.

    All methods are fire-and-forget: API failures are logged and swallowed.
    """

    def __init__(self, bot: Bot, config: AgentConfig) -> None:
        self._bot = bot
        self._config = config

    @property
    def _cfg(self) -> ReactionConfig:
        return self._config.reactions

    @property
    def _level(self) -> str:
        return self._cfg.level

    async def _set(self, chat_id: int, message_id: int, emoji: str) -> None:
        from aiogram.types import ReactionTypeEmoji

        try:
            await self._bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
        except Exception:
            logger.debug("Failed to set reaction %s", emoji, exc_info=True)

    async def ack(self, chat_id: int, message_id: int) -> None:
        """Acknowledge receipt — sent when message first arrives."""
        if self._level == "off":
            return
        await self._set(chat_id, message_id, self._cfg.ack_emoji)

    async def done(self, chat_id: int, message_id: int) -> None:
        """Mark completion — replaces ack reaction after successful processing."""
        if self._level == "off":
            return
        await self._set(chat_id, message_id, self._cfg.done_emoji)

    async def error(self, chat_id: int, message_id: int) -> None:
        """Signal error — replaces any existing reaction."""
        if self._level == "off":
            return
        await self._set(chat_id, message_id, self._cfg.error_emoji)

    async def clear(self, chat_id: int, message_id: int) -> None:
        """Remove all bot reactions from a message."""
        try:
            await self._bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[],
            )
        except Exception:
            logger.debug("Failed to clear reactions", exc_info=True)
