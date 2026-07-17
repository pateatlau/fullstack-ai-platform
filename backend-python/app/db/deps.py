"""FastAPI dependency providers for request-scoped persistence components.

Each store is constructed from the request-scoped ``AsyncSession`` dependency,
consistent with the existing ``Depends(...)`` pattern. Endpoints wire these in
when chat lifecycle persistence lands (Phase 5).
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.chat import SqlChatStore
from app.db.session import get_db_session
from app.db.usage import SqlUsageStore


def get_chat_store(
    session: AsyncSession = Depends(get_db_session),
) -> SqlChatStore:
    return SqlChatStore(session)


def get_usage_store(
    session: AsyncSession = Depends(get_db_session),
) -> SqlUsageStore:
    return SqlUsageStore(session)
