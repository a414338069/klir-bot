"""Forum topic support utilities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from klir.session.key import SessionKey

if TYPE_CHECKING:
    from aiogram.types import Message

    from klir.config import AgentConfig, ThreadBindingConfig
    from klir.session.manager import SessionData

logger = logging.getLogger(__name__)


def get_thread_id(message: Message | None) -> int | None:
    """Extract ``message_thread_id`` from a forum topic message.

    Returns the thread ID only when the message originates from a forum
    topic (``is_topic_message is True``).  Mirrors aiogram's internal
    logic in ``Message.answer()``.
    """
    if message is None:
        return None
    if message.is_topic_message:
        return message.message_thread_id
    return None


def get_session_key(
    message: Message,
    *,
    config: AgentConfig | None = None,
) -> SessionKey:
    """Build a transport-agnostic ``SessionKey`` from a Telegram message.

    Forum topic messages get per-topic keys (``topic_id=message_thread_id``).
    Regular chats and non-topic supergroup messages get flat keys
    (``topic_id=None``).

    When *config* has ``peer_isolation`` enabled, the sender's user ID is
    included in the key so each user gets an isolated session.
    """
    topic_id = message.message_thread_id if message.is_topic_message else None
    if message.message_thread_id is not None:
        logger.debug(
            "Topic fields: is_topic_message=%s message_thread_id=%s -> topic_id=%s",
            message.is_topic_message,
            message.message_thread_id,
            topic_id,
        )
    user_id = None
    if config and config.peer_isolation and message.from_user:
        user_id = message.from_user.id
    return SessionKey(chat_id=message.chat.id, topic_id=topic_id, user_id=user_id)


def get_topic_name_from_message(message: Message) -> str | None:
    """Extract the topic name from ``forum_topic_created`` or ``forum_topic_edited``."""
    if message.forum_topic_created:
        return message.forum_topic_created.name
    if message.forum_topic_edited and message.forum_topic_edited.name:
        return message.forum_topic_edited.name
    return None


@dataclass
class BindingMeta:
    """Lifecycle metadata for a thread/topic binding."""

    name: str
    created_at: datetime
    last_active_at: datetime


class TopicNameCache:
    """In-memory cache for forum topic names with lifecycle metadata.

    Telegram Bot API has no ``getForumTopic`` -- names are only available
    from service messages (``forum_topic_created`` / ``forum_topic_edited``).
    This cache collects them so logs and ``/status`` can show human-readable
    topic names.
    """

    def __init__(self) -> None:
        self._bindings: dict[tuple[int, int], BindingMeta] = {}

    def set(self, chat_id: int, topic_id: int, name: str) -> None:
        """Store or update a topic name. Preserves created_at on update."""
        key = (chat_id, topic_id)
        now = datetime.now(UTC)
        existing = self._bindings.get(key)
        if existing is not None:
            existing.name = name
            existing.last_active_at = now
        else:
            self._bindings[key] = BindingMeta(
                name=name,
                created_at=now,
                last_active_at=now,
            )

    def get(self, chat_id: int, topic_id: int) -> str | None:
        """Look up a cached topic name (or ``None``)."""
        meta = self._bindings.get((chat_id, topic_id))
        return meta.name if meta is not None else None

    def get_meta(self, chat_id: int, topic_id: int) -> BindingMeta | None:
        """Look up full binding metadata (or ``None``)."""
        return self._bindings.get((chat_id, topic_id))

    def resolve(self, chat_id: int, topic_id: int) -> str:
        """Return the cached name or a fallback ``"Topic #N"``."""
        meta = self._bindings.get((chat_id, topic_id))
        return meta.name if meta is not None else f"Topic #{topic_id}"

    def find_by_name(self, chat_id: int, name: str) -> int | None:
        """Reverse lookup: return topic_id for *name* (case-insensitive) or ``None``."""
        lower = name.lower()
        for (cid, tid), meta in self._bindings.items():
            if cid == chat_id and meta.name.lower() == lower:
                return tid
        return None

    def expired_bindings(self, config: ThreadBindingConfig) -> list[tuple[int, int]]:
        """Return keys of bindings that exceed idle timeout or max age.

        Returns empty list when lifecycle management is disabled.
        A timeout/age value of 0 disables that specific check.
        """
        if not config.enabled:
            return []
        now = datetime.now(UTC)
        expired: list[tuple[int, int]] = []
        for key, meta in self._bindings.items():
            if config.idle_timeout_minutes > 0:
                idle_seconds = (now - meta.last_active_at).total_seconds()
                if idle_seconds >= config.idle_timeout_minutes * 60:
                    expired.append(key)
                    continue
            if config.max_age_minutes > 0:
                age_seconds = (now - meta.created_at).total_seconds()
                if age_seconds >= config.max_age_minutes * 60:
                    expired.append(key)
        return expired

    def remove_bindings(self, keys: list[tuple[int, int]]) -> int:
        """Remove bindings by key. Returns count of actually removed entries."""
        removed = 0
        for key in keys:
            if self._bindings.pop(key, None) is not None:
                removed += 1
        return removed

    def touch(self, chat_id: int, topic_id: int) -> None:
        """Update ``last_active_at`` for an existing binding. No-op if unknown."""
        meta = self._bindings.get((chat_id, topic_id))
        if meta is not None:
            meta.last_active_at = datetime.now(UTC)

    def seed_from_sessions(self, sessions: list[SessionData]) -> int:
        """Populate the cache from persisted sessions that have ``topic_name``.

        Returns the number of entries seeded.
        """
        count = 0
        for s in sessions:
            if s.topic_id is not None and s.topic_name:
                self.set(s.chat_id, s.topic_id, s.topic_name)
                count += 1
        return count
