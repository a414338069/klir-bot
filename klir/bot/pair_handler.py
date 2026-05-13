"""Handler for /pair command — admin generates pairing codes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.types import Message

    from klir.pairing import PairingService

logger = logging.getLogger(__name__)


async def handle_pair(message: Message, pairing_svc: PairingService) -> None:
    """Generate a pairing code for the admin to share with a new user."""
    if message.chat.type != "private":
        await message.reply("Pairing codes can only be generated in private DMs.")
        return

    user_id = message.from_user.id if message.from_user else 0
    code = pairing_svc.generate_code(admin_user_id=user_id)

    if code is None:
        await message.reply(
            "Maximum active pairing codes reached. Wait for existing codes to expire."
        )
        return

    ttl = pairing_svc._cfg.code_ttl_minutes
    ttl_text = f"{ttl} minutes" if ttl != 60 else "1 hour"
    await message.reply(
        f"<b>Pairing code:</b> <code>{code}</code>\n\n"
        f"Share this code with the user. They send it to the bot in DMs to pair.\n"
        f"The code is single-use and expires in {ttl_text}.",
    )
