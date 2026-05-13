"""Central message bus: intake, lock, inject, deliver."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from klir.bus.envelope import DeliveryMode, Envelope, LockMode
from klir.bus.lock_pool import LockPool

logger = logging.getLogger(__name__)


@runtime_checkable
class TransportAdapter(Protocol):
    """Protocol for transport-specific delivery."""

    async def deliver(self, envelope: Envelope) -> None:
        """Send the envelope's result to the targeted chat."""
        ...

    async def deliver_broadcast(self, envelope: Envelope) -> None:
        """Send the envelope's result to all allowed users."""
        ...


@runtime_checkable
class SessionInjector(Protocol):
    """Protocol for injecting a prompt into the active CLI session.

    Implemented by the Orchestrator.  The bus calls this when
    ``envelope.needs_injection`` is True.
    """

    async def inject_prompt(
        self,
        prompt: str,
        chat_id: int,
        label: str,
        *,
        topic_id: int | None = None,
    ) -> str:
        """Execute *prompt* in the active session. Returns response text."""
        ...


class MessageBus:
    """Central coordinator for all background message routing.

    Usage::

        bus = MessageBus()
        bus.register_transport(TelegramTransport(bot))
        bus.set_injector(orchestrator)

        # Any observer callback:
        await bus.submit(from_cron_result(title, text, status))
    """

    def __init__(self, lock_pool: LockPool | None = None) -> None:
        self._locks = lock_pool if lock_pool is not None else LockPool()
        self._transports: list[TransportAdapter] = []
        self._injector: SessionInjector | None = None
        self._pre_deliver: Callable[[Envelope], Awaitable[None]] | None = None
        self._audit: Callable[[Envelope], Awaitable[None]] | None = None

    @property
    def lock_pool(self) -> LockPool:
        """The shared lock pool."""
        return self._locks

    def register_transport(self, transport: TransportAdapter) -> None:
        """Add a transport adapter for delivery."""
        self._transports.append(transport)

    def set_injector(self, injector: SessionInjector) -> None:
        """Set the session injector (typically the Orchestrator)."""
        self._injector = injector

    def set_pre_deliver_hook(self, hook: Callable[[Envelope], Awaitable[None]]) -> None:
        """Optional hook called after injection but before delivery.

        Useful for transport-specific actions like typing indicators
        or pre-delivery notifications.
        """
        self._pre_deliver = hook

    def set_audit_hook(self, hook: Callable[[Envelope], Awaitable[None]]) -> None:
        """Optional audit hook called for every submitted envelope."""
        self._audit = hook

    async def submit(self, envelope: Envelope) -> None:
        """Route an envelope: assign ID, acquire lock, inject, deliver."""
        if not envelope.envelope_id:
            envelope.envelope_id = secrets.token_hex(6)

        if self._audit:
            try:
                await self._audit(envelope)
            except Exception:
                logger.exception("Audit hook failed for envelope %s", envelope.envelope_id)

        logger.debug(
            "Bus submit: origin=%s chat=%d delivery=%s lock=%s inject=%s",
            envelope.origin.value,
            envelope.chat_id,
            envelope.delivery.value,
            envelope.lock_mode.value,
            envelope.needs_injection,
        )

        if envelope.lock_mode == LockMode.REQUIRED:
            lock = self._locks.get(envelope.lock_key)
            async with lock:
                await self._process(envelope)
        else:
            await self._process(envelope)

    async def _process(self, envelope: Envelope) -> None:
        """Inject if needed, then deliver."""
        if envelope.needs_injection and self._injector and envelope.prompt:
            label = f"{envelope.origin.value}:{envelope.envelope_id}"
            try:
                response = await self._injector.inject_prompt(
                    envelope.prompt,
                    envelope.chat_id,
                    label,
                    topic_id=envelope.topic_id,
                )
                envelope.result_text = response
            except Exception:
                logger.exception(
                    "Injection failed: origin=%s chat=%d",
                    envelope.origin.value,
                    envelope.chat_id,
                )
                envelope.is_error = True
                if not envelope.result_text:
                    envelope.result_text = f"Error processing {envelope.origin.value} result"

        if self._pre_deliver:
            await self._pre_deliver(envelope)

        await self._deliver(envelope)

    async def _deliver(self, envelope: Envelope) -> None:
        """Fan out to all registered transports."""
        if not self._transports:
            logger.warning(
                "No transports registered — envelope lost: origin=%s chat=%d",
                envelope.origin.value,
                envelope.chat_id,
            )
            return
        for transport in self._transports:
            try:
                if envelope.delivery == DeliveryMode.BROADCAST:
                    await transport.deliver_broadcast(envelope)
                else:
                    await transport.deliver(envelope)
            except Exception:
                logger.exception(
                    "Transport delivery failed: origin=%s transport=%s",
                    envelope.origin.value,
                    type(transport).__name__,
                )
