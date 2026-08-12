"""Fan-out dispatcher for approval notification providers."""

from __future__ import annotations

import asyncio

from app.ai.hitl.notifications.base import (
    ApprovalNotificationEvent,
    NotificationProvider,
)
from app.core.logging import get_logger

_logger = get_logger(__name__)


def _log_background_task_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _logger.warning(
            "HITL background notification dispatch failed",
            exc_info=exc,
        )


class NotificationDispatcher:
    """Best-effort fan-out to every configured provider.

    Dispatch never raises and never blocks the approval lifecycle: provider
    delivery runs in a background task so slow or failing providers cannot
    delay pause/decide/cancel paths. Each provider's own exceptions are caught
    in :meth:`_dispatch_one` in addition to defensive handling inside the
    providers themselves.
    """

    def __init__(self, providers: list[NotificationProvider] | None = None) -> None:
        self._providers = list(providers or [])

    @property
    def providers(self) -> list[NotificationProvider]:
        return list(self._providers)

    async def dispatch(self, event: ApprovalNotificationEvent) -> None:
        """Schedule provider fan-out and return without awaiting delivery."""
        if not self._providers:
            return

        task = asyncio.create_task(
            self._dispatch_all(event),
            name=(f"hitl-notification-{event.event_type.value}-{event.approval_id}"),
        )
        task.add_done_callback(_log_background_task_failure)

    async def _dispatch_all(self, event: ApprovalNotificationEvent) -> None:
        await asyncio.gather(
            *(self._dispatch_one(provider, event) for provider in self._providers)
        )

    async def _dispatch_one(
        self,
        provider: NotificationProvider,
        event: ApprovalNotificationEvent,
    ) -> None:
        try:
            await provider.notify(event)
        except Exception:
            _logger.warning(
                "HITL notification provider raised unexpectedly",
                provider=type(provider).__name__,
                approval_id=str(event.approval_id),
                event_type=event.event_type.value,
                exc_info=True,
            )
