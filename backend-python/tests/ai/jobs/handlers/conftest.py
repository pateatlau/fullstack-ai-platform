"""Shared fixtures for handler integration tests."""

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _truncate_hitl_approvals_for_handler_tests(db_session) -> None:
    """Isolate handler DB tests from shared approval rows."""
    result = await db_session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result:
        return
    await db_session.execute(
        text("TRUNCATE agent_tool_approvals RESTART IDENTITY CASCADE")
    )
    await db_session.commit()
