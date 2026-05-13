"""Shared message execution flows for TelegramBot (streaming and non-streaming)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from klir.bot.sender import SendRichOpts, send_files_from_text, send_rich
from klir.bot.streaming import StreamContext, create_stream_editor
from klir.bot.typing import TypingContext
from klir.cli.coalescer import CoalesceConfig, StreamCoalescer
from klir.config import ReplyToMode
from klir.session.key import SessionKey

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

    from klir.cli.tool_activity import ToolActivity
    from klir.config import StreamingConfig
    from klir.orchestrator.core import Orchestrator

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NonStreamingDispatch:
    """Input payload for one non-streaming message turn."""

    bot: Bot
    orchestrator: Orchestrator
    key: SessionKey
    text: str
    allowed_roots: list[Path] | None
    reply_to: Message | None = None
    thread_id: int | None = None
    polls_enabled: bool = False
    polls_anonymous: bool = True
    reply_to_mode: ReplyToMode = "first"
    forwarding_enabled: bool = False
    forwarding_targets: set[int] = field(default_factory=set)


@dataclass(slots=True)
class StreamingDispatch:
    """Input payload for one streaming message turn."""

    bot: Bot
    orchestrator: Orchestrator
    message: Message
    key: SessionKey
    text: str
    streaming_cfg: StreamingConfig
    allowed_roots: list[Path] | None
    thread_id: int | None = None
    polls_enabled: bool = False
    polls_anonymous: bool = True
    reply_to_mode: ReplyToMode = "first"
    forwarding_enabled: bool = False
    forwarding_targets: set[int] = field(default_factory=set)


async def run_non_streaming_message(
    dispatch: NonStreamingDispatch,
) -> str:
    """Execute one non-streaming turn and deliver the result to Telegram."""
    async with TypingContext(dispatch.bot, dispatch.key.chat_id, thread_id=dispatch.thread_id):
        result = await dispatch.orchestrator.handle_message(dispatch.key, dispatch.text)

    display_text = result.text
    if result.footer is not None:
        from klir.bot.footer import build_footer_markdown

        md = build_footer_markdown(result.footer)
        if md:
            display_text += md

    reply_id = dispatch.reply_to.message_id if dispatch.reply_to else None
    await send_rich(
        dispatch.bot,
        dispatch.key.chat_id,
        display_text,
        SendRichOpts(
            reply_to_message_id=reply_id,
            allowed_roots=dispatch.allowed_roots,
            thread_id=dispatch.thread_id,
            polls_enabled=dispatch.polls_enabled,
            polls_anonymous=dispatch.polls_anonymous,
            reply_to_mode=dispatch.reply_to_mode,
            forwarding_enabled=dispatch.forwarding_enabled,
            forwarding_targets=dispatch.forwarding_targets,
        ),
    )
    return display_text


async def run_streaming_message(
    dispatch: StreamingDispatch,
) -> str:
    """Execute one streaming turn and deliver text/files to Telegram."""
    logger.info("Streaming flow started")

    editor = create_stream_editor(
        dispatch.bot,
        dispatch.key.chat_id,
        StreamContext(
            reply_to=dispatch.message,
            cfg=dispatch.streaming_cfg,
            thread_id=dispatch.thread_id,
            reply_to_mode=dispatch.reply_to_mode,
        ),
    )
    coalescer = StreamCoalescer(
        config=CoalesceConfig(
            min_chars=dispatch.streaming_cfg.min_chars,
            max_chars=dispatch.streaming_cfg.max_chars,
            idle_ms=dispatch.streaming_cfg.idle_ms,
            sentence_break=dispatch.streaming_cfg.sentence_break,
        ),
        on_flush=editor.append_text,
    )

    async def on_text(delta: str) -> None:
        await coalescer.feed(delta)

    async def on_tool(activity: ToolActivity) -> None:
        await coalescer.flush(force=True)
        await editor.append_tool(activity)

    async def on_system(status: str | None) -> None:
        system_map: dict[str, str] = {
            "thinking": "THINKING",
            "compacting": "COMPACTING",
            "recovering": "Please wait, recovering...",
            "timeout_warning": "TIMEOUT APPROACHING",
            "timeout_extended": "TIMEOUT EXTENDED",
        }
        label = system_map.get(status or "")
        if label is None:
            return
        await coalescer.flush(force=True)
        await editor.append_system(label)

    async with TypingContext(dispatch.bot, dispatch.key.chat_id, thread_id=dispatch.thread_id):
        result = await dispatch.orchestrator.handle_message_streaming(
            dispatch.key,
            dispatch.text,
            on_text_delta=on_text,
            on_tool_activity=on_tool,
            on_system_status=on_system,
        )

    footer_html = ""
    if result.footer is not None:
        from klir.bot.footer import build_footer_html

        footer_html = build_footer_html(result.footer)

    await coalescer.flush(force=True)
    coalescer.stop()
    await editor.finalize(result.text, footer_html=footer_html)

    logger.info(
        "Streaming flow completed fallback=%s content=%s",
        result.stream_fallback,
        editor.has_content,
    )

    if result.stream_fallback or not editor.has_content:
        display_text = result.text
        if result.footer is not None:
            from klir.bot.footer import build_footer_markdown

            md = build_footer_markdown(result.footer)
            if md:
                display_text += md

        await send_rich(
            dispatch.bot,
            dispatch.key.chat_id,
            display_text,
            SendRichOpts(
                reply_to_message_id=dispatch.message.message_id,
                allowed_roots=dispatch.allowed_roots,
                thread_id=dispatch.thread_id,
                polls_enabled=dispatch.polls_enabled,
                polls_anonymous=dispatch.polls_anonymous,
                reply_to_mode=dispatch.reply_to_mode,
                forwarding_enabled=dispatch.forwarding_enabled,
                forwarding_targets=dispatch.forwarding_targets,
            ),
        )
    else:
        await send_files_from_text(
            dispatch.bot,
            dispatch.key.chat_id,
            result.text,
            allowed_roots=dispatch.allowed_roots,
            thread_id=dispatch.thread_id,
        )

    return result.text
