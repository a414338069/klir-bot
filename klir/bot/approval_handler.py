"""Telegram inline button handler for tool execution approvals."""

from __future__ import annotations

import contextlib
import html as html_mod
import logging
from typing import TYPE_CHECKING, Any

from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

if TYPE_CHECKING:
    from aiogram import Bot

    from klir.approval import ApprovalService

logger = logging.getLogger(__name__)

APR_PREFIX = "apr:"


def parse_approval_callback(data: str) -> tuple[str, bool] | None:
    """Parse ``apr:<request_id>:yes|no`` callback data."""
    if not data.startswith(APR_PREFIX):
        return None
    rest = data[len(APR_PREFIX) :]
    colon = rest.rfind(":")
    if colon < 0:
        return None
    request_id = rest[:colon]
    action = rest[colon + 1 :]
    if action == "yes":
        return request_id, True
    if action == "no":
        return request_id, False
    return None


async def send_approval_request(  # noqa: PLR0913
    bot: Bot,
    approver_ids: list[int],
    request_id: str,
    tool_name: str,
    chat_id: int,
    parameters: dict[str, Any] | None = None,
) -> list[int]:
    """Send approval request to all approvers. Returns list of sent message IDs."""
    param_preview = ""
    if parameters:
        items = list(parameters.items())[:3]
        param_preview = "\n".join(
            f"  <code>{html_mod.escape(str(k))}</code>: {html_mod.escape(str(v)[:80])}"
            for k, v in items
        )

    text = (
        f"🔐 <b>Tool approval required</b>\n\n"
        f"<b>Tool:</b> <code>{html_mod.escape(tool_name)}</code>\n"
        f"<b>Chat:</b> <code>{chat_id}</code>\n"
    )
    if param_preview:
        text += f"<b>Parameters:</b>\n{param_preview}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Allow",
                    callback_data=f"{APR_PREFIX}{request_id}:yes",
                ),
                InlineKeyboardButton(
                    text="❌ Deny",
                    callback_data=f"{APR_PREFIX}{request_id}:no",
                ),
            ]
        ]
    )

    sent_ids = []
    for approver_id in approver_ids:
        try:
            msg = await bot.send_message(
                chat_id=approver_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            sent_ids.append(msg.message_id)
        except Exception:
            logger.warning("Failed to send approval to %d", approver_id, exc_info=True)

    if approver_ids and not sent_ids:
        logger.error("All approval sends failed for request %s", request_id)

    return sent_ids


async def handle_approval_callback(
    bot: Bot,
    svc: ApprovalService,
    callback_data: str,
    chat_id: int,
    message_id: int,
) -> bool:
    """Handle an approval/denial callback. Returns True if resolved."""
    parsed = parse_approval_callback(callback_data)
    if parsed is None:
        return False

    request_id, approved = parsed
    resolved = svc.resolve(request_id, approved=approved)

    action = "✅ Approved" if approved else "❌ Denied"
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            text=f"{action} (request <code>{html_mod.escape(request_id)}</code>)",
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )

    return resolved
