"""Tool execution approval service for Telegram-routed approvals."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from klir.config import AgentConfig

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    """A pending tool approval request."""

    request_id: str
    tool_name: str
    tool_id: str | None
    chat_id: int
    parameters: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    approved: bool | None = None  # None = pending


class ApprovalService:
    """Manage tool execution approval requests."""

    def __init__(self, config: AgentConfig) -> None:
        self._cfg = config.approval
        self._pending: dict[str, ApprovalRequest] = {}
        self._counter = 0

    def is_auto_approved(self, tool_name: str) -> bool:
        """Check if a tool is auto-approved (or service is disabled)."""
        if not self._cfg.enabled:
            return True
        return tool_name in self._cfg.auto_approve_tools

    def request_approval(
        self,
        tool_name: str,
        tool_id: str | None = None,
        chat_id: int = 0,
        parameters: dict[str, Any] | None = None,
    ) -> str:
        """Create a new approval request. Returns the request ID."""
        self._counter += 1
        req_id = f"apr_{self._counter}"
        self._pending[req_id] = ApprovalRequest(
            request_id=req_id,
            tool_name=tool_name,
            tool_id=tool_id,
            chat_id=chat_id,
            parameters=parameters,
        )
        logger.info("Approval requested: %s tool=%s chat=%d", req_id, tool_name, chat_id)
        return req_id

    def resolve(self, request_id: str, *, approved: bool) -> bool:
        """Resolve a pending request. Returns True if found and resolved."""
        req = self._pending.pop(request_id, None)
        if req is None:
            return False
        req.approved = approved
        action = "approved" if approved else "denied"
        logger.info("Approval %s: %s tool=%s", action, request_id, req.tool_name)
        return True

    def has_pending(self, request_id: str) -> bool:
        return request_id in self._pending

    def list_pending(self) -> list[ApprovalRequest]:
        """Return all pending requests."""
        return list(self._pending.values())

    def prune_expired(self) -> list[ApprovalRequest]:
        """Remove and return requests older than timeout_seconds.

        Only prunes when ``auto_deny_on_timeout`` is enabled.
        """
        if not self._cfg.auto_deny_on_timeout:
            return []
        now = time.time()
        expired = []
        for req_id, req in list(self._pending.items()):
            if now - req.created_at > self._cfg.timeout_seconds:
                del self._pending[req_id]
                req.approved = False
                expired.append(req)
        return expired
