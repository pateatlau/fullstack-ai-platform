"""Fan-out dispatcher for approval notification providers."""

from __future__ import annotations

from app.ai.hitl.notifications.base import (
    ApprovalNotificationEvent,
    NotificationProvider,
)
from app.core.logging import get_logger

_logger = get_logger(__name__)


class NotificationDispatcher:
    """Best-effort fan-out to every configured provider.

    Dispatch never raises: a slow or failing provider must never affect an
    approval decision. Each provider's own exceptions are caught here in
    addition to the defensive handling inside the providers themselves.
    """

    def __init__(self, providers: list[NotificationProvider] | None = None) -> None:
        self._providers = list(providers or [])

    @property
    def providers(self) -> list[NotificationProvider]:
        return list(self._providers)

    async def dispatch(self, event: ApprovalNotificationEvent) -> None:
        for provider in self._providers:
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
