"""Request-scoped async database session dependency.

Yields an ``AsyncSession`` bound to the application engine, commits on success,
rolls back on error, and always closes. Consistent with the existing
``Depends(...)`` pattern used elsewhere in the app. Broader transaction
boundaries and use-case persistence wiring are refined in a later phase.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_sessionmaker


async def get_db_session() -> AsyncIterator[AsyncSession]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
