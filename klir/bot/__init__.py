"""Telegram bot interface."""

from klir.bot.abort import ABORT_WORDS, is_abort_message, is_abort_trigger
from klir.bot.app import TelegramBot
from klir.bot.dedup import DedupeCache, build_dedup_key
from klir.bot.edit_streaming import EditStreamEditor
from klir.bot.formatting import (
    TELEGRAM_MSG_LIMIT,
    markdown_to_telegram_html,
    split_html_message,
)
from klir.bot.middleware import AuthMiddleware, SequentialMiddleware
from klir.bot.sender import send_file, send_rich
from klir.bot.streaming import StreamEditor, StreamEditorProtocol, create_stream_editor
from klir.bot.typing import TypingContext
from klir.files.tags import extract_file_paths

__all__ = [
    "ABORT_WORDS",
    "TELEGRAM_MSG_LIMIT",
    "AuthMiddleware",
    "DedupeCache",
    "EditStreamEditor",
    "SequentialMiddleware",
    "StreamEditor",
    "StreamEditorProtocol",
    "TelegramBot",
    "TypingContext",
    "build_dedup_key",
    "create_stream_editor",
    "extract_file_paths",
    "is_abort_message",
    "is_abort_trigger",
    "markdown_to_telegram_html",
    "send_file",
    "send_rich",
    "split_html_message",
]
