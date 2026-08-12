"""Shared fixtures for Background Jobs tests."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def background_jobs_table_available(session: AsyncSession) -> bool:
    result = await session.scalar(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'background_jobs'"
        )
    )
    return result == 1


def make_queue_session_factory(
    engine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _truncate_background_jobs(db_session) -> None:
    """Isolate queue integration tests — queue ops commit outside db_session."""
    if not await background_jobs_table_available(db_session):
        return
    await db_session.execute(text("TRUNCATE background_jobs"))
    await db_session.commit()
