"""Transport-agnostic composite session key."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionKey:
    """Composite session identifier: chat + optional topic/channel + optional user.

    For Telegram forum topics, ``topic_id`` is ``message_thread_id``.
    For the WebSocket API, ``topic_id`` maps to ``channel_id``.
    When ``topic_id`` is ``None``, this is a flat (legacy) session key.
    When ``user_id`` is set (peer isolation mode), each user gets a separate session.
    """

    chat_id: int
    topic_id: int | None = None
    user_id: int | None = None

    @property
    def storage_key(self) -> str:
        """JSON-serializable key for ``sessions.json`` persistence."""
        parts = [str(self.chat_id)]
        if self.topic_id is not None:
            parts.append(str(self.topic_id))
        if self.user_id is not None:
            parts.append(f"u{self.user_id}")
        return ":".join(parts)

    @property
    def lock_key(self) -> tuple[int, int | None, int | None]:
        """Hashable key for per-session lock dictionaries."""
        return (self.chat_id, self.topic_id, self.user_id)

    @classmethod
    def parse(cls, raw: str) -> SessionKey:
        """Parse a storage key back to ``SessionKey``.

        Handles legacy ``"12345"``, composite ``"12345:99"``,
        peer-isolated ``"12345:u42"``, and full ``"12345:99:u42"`` formats.
        """
        parts = raw.split(":")
        chat_id = int(parts[0])
        topic_id = None
        user_id = None
        for part in parts[1:]:
            if part.startswith("u"):
                user_id = int(part[1:])
            else:
                topic_id = int(part)
        return cls(chat_id=chat_id, topic_id=topic_id, user_id=user_id)
