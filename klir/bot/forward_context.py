"""Extract forward origin metadata from inbound Telegram messages."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.types import Message

logger = logging.getLogger(__name__)


def extract_forward_context(message: Message) -> str | None:
    """Build a context string from a forwarded message's origin metadata.

    Returns ``None`` when the message is not forwarded.
    """
    from aiogram.types import (
        MessageOriginChannel,
        MessageOriginChat,
        MessageOriginHiddenUser,
        MessageOriginUser,
    )

    origin = message.forward_origin
    if origin is None:
        return None

    date_str = origin.date.isoformat() if origin.date else "unknown"

    if isinstance(origin, MessageOriginUser):
        name = origin.sender_user.full_name
        uid = origin.sender_user.id
        return f"[Forwarded from {name} (user {uid}) at {date_str}]"
    if isinstance(origin, MessageOriginChannel):
        title = origin.chat.title
        cid = origin.chat.id
        mid = origin.message_id
        return f'[Forwarded from channel "{title}" (chat {cid}, message {mid}) at {date_str}]'
    if isinstance(origin, MessageOriginHiddenUser):
        name = origin.sender_user_name
        return f"[Forwarded from {name} (hidden user) at {date_str}]"
    # MessageOriginChat is the only remaining variant.
    title = (
        origin.sender_chat.title
        if isinstance(origin, MessageOriginChat) and origin.sender_chat
        else "unknown"
    )
    return f'[Forwarded from chat "{title}" at {date_str}]'


def prepend_forward_context(context: str, text: str) -> str:
    """Prepend forward context to the resolved prompt text."""
    return f"{context}\n\n{text}"
