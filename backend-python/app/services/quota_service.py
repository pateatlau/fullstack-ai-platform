"""Guest and authenticated quota enforcement (plan Sections 5.1, 12; V1.1.1 upload quota)."""

from __future__ import annotations

import datetime
import uuid
from typing import Protocol

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.chat_service import ChatServiceError

logger = get_logger(__name__)


class QuotaExceededError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="quota_exceeded",
            message="Guest message quota exceeded for the current window.",
            status_code=429,
        )


class UploadQuotaExceededError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="quota_exceeded",
            message="Daily document upload quota exceeded for the current window.",
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


class UploadQuotaStore(Protocol):
    async def get_upload_count(
        self, user_id: uuid.UUID, window_start: datetime.date
    ) -> int: ...

    async def try_reserve(
        self,
        user_id: uuid.UUID,
        window_start: datetime.date,
        *,
        quota: int,
    ) -> bool: ...

    async def release(
        self, user_id: uuid.UUID, window_start: datetime.date
    ) -> None: ...


def _utc_window() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


class QuotaService:
    """Check and record guest message usage and authenticated upload volume."""

    def __init__(
        self,
        *,
        store: GuestQuotaStore,
        settings: Settings,
        upload_store: UploadQuotaStore | None = None,
    ) -> None:
        self._store = store
        self._upload_store = upload_store
        self._settings = settings

    async def check(self, guest_id: uuid.UUID) -> None:
        """Raise ``QuotaExceededError`` if the guest has hit the daily limit."""
        count = await self._store.get_message_count(guest_id, _utc_window())
        if count >= self._settings.guest_daily_message_quota:
            logger.info(
                "Guest quota denied",
                guest_id=str(guest_id),
                count=count,
                quota=self._settings.guest_daily_message_quota,
            )
            raise QuotaExceededError()

    async def record(self, guest_id: uuid.UUID, *, tokens: int = 0) -> None:
        """Durably increment the guest's counter for the current window."""
        await self._store.increment(guest_id, _utc_window(), tokens=tokens)

    async def remaining(self, guest_id: uuid.UUID) -> int:
        """Messages left in the current UTC window (never negative)."""
        count = await self._store.get_message_count(guest_id, _utc_window())
        return max(0, self._settings.guest_daily_message_quota - count)

    async def reserve_upload(self, user_id: uuid.UUID) -> None:
        """Atomically reserve one upload slot for the current UTC window."""
        quota = self._settings.effective_authenticated_daily_upload_quota
        if quota is None or self._upload_store is None:
            return

        reserved = await self._upload_store.try_reserve(
            user_id,
            _utc_window(),
            quota=quota,
        )
        if not reserved:
            count = await self._upload_store.get_upload_count(user_id, _utc_window())
            logger.info(
                "Upload quota denied",
                upload_quota_denied_total=True,
                user_id=str(user_id),
                count=count,
                quota=quota,
            )
            raise UploadQuotaExceededError()

    async def release_upload(self, user_id: uuid.UUID) -> None:
        """Return a reserved upload slot after a failed or cancelled ingest."""
        if (
            self._settings.effective_authenticated_daily_upload_quota is None
            or self._upload_store is None
        ):
            return
        await self._upload_store.release(user_id, _utc_window())
