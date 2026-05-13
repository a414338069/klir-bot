"""Per-chat configuration resolution with override layering."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from klir.config import ChatOverrides, ReplyToMode

if TYPE_CHECKING:
    from klir.config import AgentConfig

logger = logging.getLogger(__name__)


class ChatConfigResolver:
    """Resolve per-chat config by layering: chat-specific > wildcard > global."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._overrides: dict[str, ChatOverrides] = {}
        self._parse_overrides(config)

    def _parse_overrides(self, config: AgentConfig) -> None:
        self._overrides.clear()
        for key, raw in config.chat_overrides.items():
            try:
                self._overrides[key] = ChatOverrides.model_validate(raw)
            except Exception:
                logger.warning("Invalid chat override for key=%s, skipping", key)

    def reload(self, config: AgentConfig) -> None:
        """Update from new config (called on hot-reload)."""
        self._config = config
        self._parse_overrides(config)

    def _resolve(self, chat_id: int) -> ChatOverrides | None:
        """Find the most specific override for a chat."""
        key = str(chat_id)
        if key in self._overrides:
            return self._overrides[key]
        if "*" in self._overrides:
            return self._overrides["*"]
        return None

    def provider(self, chat_id: int) -> str:
        ov = self._resolve(chat_id)
        if ov and ov.provider is not None:
            return ov.provider
        return self._config.provider

    def model(self, chat_id: int) -> str:
        ov = self._resolve(chat_id)
        if ov and ov.model is not None:
            return ov.model
        return self._config.model

    def group_mention_only(self, chat_id: int) -> bool:
        ov = self._resolve(chat_id)
        if ov and ov.group_mention_only is not None:
            return ov.group_mention_only
        return self._config.group_mention_only

    def is_enabled(self, chat_id: int) -> bool:
        ov = self._resolve(chat_id)
        if ov and ov.enabled is not None:
            return ov.enabled
        return True

    def reply_to_mode(self, chat_id: int) -> ReplyToMode:
        ov = self._resolve(chat_id)
        if ov and ov.reply_to_mode is not None:
            return ov.reply_to_mode
        return self._config.reply_to_mode
