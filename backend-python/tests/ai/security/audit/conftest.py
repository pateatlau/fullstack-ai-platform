"""Shared fixtures for audit log tests (Epic 11 Phase 3)."""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _truncate_audit_events_for_audit_tests(db_session) -> AsyncIterator[None]:
    """Isolate audit log tests from shared rows — audit writes commit outside db_session."""
    result = await db_session.scalar(
        text("SELECT to_regclass('public.audit_events') IS NOT NULL")
    )
    if not result:
        return
    await db_session.execute(text("TRUNCATE audit_events"))
    await db_session.commit()
    yield
    await db_session.execute(text("TRUNCATE audit_events"))
    await db_session.commit()
