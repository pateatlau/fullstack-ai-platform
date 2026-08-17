from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.ai.security.quotas.store import SqlUsageQuotaStore


@pytest.mark.anyio
async def test_usage_quota_counter_enforces_daily_limit(db_session) -> None:
    store = SqlUsageQuotaStore(db_session)
    subject_id = str(uuid.uuid4())
    day = date(2026, 8, 15)

    assert await store.check_and_increment(subject_id, "tool", 2, day) is True
    assert await store.check_and_increment(subject_id, "tool", 2, day) is True
    assert await store.check_and_increment(subject_id, "tool", 2, day) is False

    next_day = day + timedelta(days=1)
    assert await store.check_and_increment(subject_id, "tool", 2, next_day) is True
