"""SQLAlchemy-backed identity/auth persistence (plan Section 8.2).

Focused stores for the identity/auth use cases — Google users, guest
identities, and guest quota counters — with no generic repository framework or
per-table symmetry.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GuestIdentity, GuestQuotaCounter, UploadQuotaCounter, User


class SqlUserStore:
    """Resolve/create/update Google users against an ``AsyncSession``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_google_sub(self, sub: str) -> User | None:
        return await self._session.scalar(
            select(User).where(
                User.auth_provider == "google",
                User.external_auth_id == sub,
            )
        )

    async def create(
        self,
        *,
        sub: str,
        email: str | None,
        name: str | None,
        picture: str | None,
    ) -> User:
        user = User(
            auth_provider="google",
            external_auth_id=sub,
            email=email,
            display_name=name,
            picture_url=picture,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def update_profile(
        self,
        user: User,
        *,
        email: str | None,
        name: str | None,
        picture: str | None,
    ) -> User:
        changed = False
        if name is not None and user.display_name != name:
            user.display_name = name
            changed = True
        if picture is not None and user.picture_url != picture:
            user.picture_url = picture
            changed = True
        if email is not None and user.email != email:
            user.email = email
            changed = True
        if changed:
            await self._session.flush()
        return user


class SqlGuestStore:
    """Resolve/create guest identities and support guest→user linking."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token_hash(self, token_hash: str) -> GuestIdentity | None:
        return await self._session.scalar(
            select(GuestIdentity).where(GuestIdentity.token_hash == token_hash)
        )

    async def create(
        self,
        *,
        token_hash: str,
        created_ip_hash: str | None = None,
    ) -> GuestIdentity:
        guest = GuestIdentity(
            token_hash=token_hash,
            created_ip_hash=created_ip_hash,
        )
        self._session.add(guest)
        await self._session.flush()
        return guest

    async def touch(self, guest_id: uuid.UUID) -> None:
        """Advance ``last_seen_at`` for guest continuity."""
        await self._session.execute(
            update(GuestIdentity)
            .where(GuestIdentity.id == guest_id)
            .values(last_seen_at=func.now())
        )

    async def link_to_user(self, guest_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Link this guest identity to an authenticated user (link only, Section 7)."""
        await self._session.execute(
            update(GuestIdentity)
            .where(GuestIdentity.id == guest_id)
            .values(linked_user_id=user_id)
        )


class SqlGuestQuotaStore:
    """Durable, windowed guest quota counters (plan Section 2.8)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_message_count(
        self, guest_id: uuid.UUID, window_start: datetime.date
    ) -> int:
        value = await self._session.scalar(
            select(GuestQuotaCounter.message_count).where(
                GuestQuotaCounter.guest_id == guest_id,
                GuestQuotaCounter.window_start == window_start,
            )
        )
        return value or 0

    async def increment(
        self,
        guest_id: uuid.UUID,
        window_start: datetime.date,
        *,
        tokens: int = 0,
    ) -> None:
        """Atomically upsert-and-increment the windowed counter (Section 2.8).

        The ``INSERT ... ON CONFLICT DO UPDATE`` makes the check-and-increment
        safe under Postgres row locking, so concurrent guest requests cannot
        corrupt the count.
        """
        stmt = pg_insert(GuestQuotaCounter).values(
            guest_id=guest_id,
            window_start=window_start,
            message_count=1,
            total_tokens=tokens,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["guest_id", "window_start"],
            set_={
                "message_count": GuestQuotaCounter.message_count + 1,
                "total_tokens": GuestQuotaCounter.total_tokens + tokens,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(stmt)


class SqlUploadQuotaStore:
    """Durable, windowed authenticated upload counters (V1.1.1 demo protection)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_upload_count(
        self, user_id: uuid.UUID, window_start: datetime.date
    ) -> int:
        value = await self._session.scalar(
            select(UploadQuotaCounter.upload_count).where(
                UploadQuotaCounter.user_id == user_id,
                UploadQuotaCounter.window_start == window_start,
            )
        )
        return value or 0

    async def try_reserve(
        self,
        user_id: uuid.UUID,
        window_start: datetime.date,
        *,
        quota: int,
    ) -> bool:
        stmt = pg_insert(UploadQuotaCounter).values(
            user_id=user_id,
            window_start=window_start,
            upload_count=1,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "window_start"],
            set_={
                "upload_count": UploadQuotaCounter.upload_count + 1,
                "updated_at": func.now(),
            },
            where=(UploadQuotaCounter.upload_count < quota),
        ).returning(UploadQuotaCounter.upload_count)
        result = await self._session.scalar(stmt)
        return result is not None

    async def release(self, user_id: uuid.UUID, window_start: datetime.date) -> None:
        stmt = (
            update(UploadQuotaCounter)
            .where(
                UploadQuotaCounter.user_id == user_id,
                UploadQuotaCounter.window_start == window_start,
                UploadQuotaCounter.upload_count > 0,
            )
            .values(
                upload_count=UploadQuotaCounter.upload_count - 1,
                updated_at=func.now(),
            )
        )
        await self._session.execute(stmt)
