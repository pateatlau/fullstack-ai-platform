from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageQuotaCounter


class SqlUsageQuotaStore:
    """Atomic daily counters for security usage quota types."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_count(
        self, subject_id: str, quota_type: str, day: datetime.date
    ) -> int:
        value = await self._session.scalar(
            select(UsageQuotaCounter.count).where(
                UsageQuotaCounter.subject_id == subject_id,
                UsageQuotaCounter.quota_type == quota_type,
                UsageQuotaCounter.day == day,
            )
        )
        return value or 0

    async def check_and_increment(
        self,
        subject_id: str,
        quota_type: str,
        limit: int,
        day: datetime.date,
    ) -> bool:
        if limit < 1:
            return False
        stmt = pg_insert(UsageQuotaCounter).values(
            subject_id=subject_id,
            quota_type=quota_type,
            day=day,
            count=1,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["subject_id", "quota_type", "day"],
            set_={"count": UsageQuotaCounter.count + 1, "updated_at": func.now()},
            where=UsageQuotaCounter.count < limit,
        ).returning(UsageQuotaCounter.count)
        return await self._session.scalar(stmt) is not None
