"""Guest quota enforcement (plan Sections 5.1, 12).

Guests are limited to a configurable number of messages per UTC day. The check
runs before any provider call; the durable counter is incremented after a
message is accepted. Authenticated users are not governed by this policy.

``QuotaExceededError`` subclasses ``ChatServiceError`` so it flows through the
existing error envelope as a first-class ``quota_exceeded`` (429) response
(plan Section 8.5).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Protocol

from app.core.config import Settings
from app.services.chat_service import ChatServiceError


class QuotaExceededError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="quota_exceeded",
            message="Guest message quota exceeded for the current window.",
            status_code=429,
        )


class GuestQuotaStore(Protocol):
    async def get_message_count(
        self, guest_id: uuid.UUID, window_start: datetime.date
    ) -> int: ...

    async def increment(
        self,
        guest_id: uuid.UUID,
        window_start: datetime.date,
        *,
        tokens: int = 0,
    ) -> None: ...


def _utc_window() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


class QuotaService:
    """Check and record guest usage against the daily message quota."""

    def __init__(self, *, store: GuestQuotaStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings

    async def check(self, guest_id: uuid.UUID) -> None:
        """Raise ``QuotaExceededError`` if the guest has hit the daily limit."""
        count = await self._store.get_message_count(guest_id, _utc_window())
        if count >= self._settings.guest_daily_message_quota:
            raise QuotaExceededError()

    async def record(self, guest_id: uuid.UUID, *, tokens: int = 0) -> None:
        """Durably increment the guest's counter for the current window."""
        await self._store.increment(guest_id, _utc_window(), tokens=tokens)
