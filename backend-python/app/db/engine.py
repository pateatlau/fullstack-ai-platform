"""Async SQLAlchemy engine and session factory.

Creates a single application-wide async engine plus an ``async_sessionmaker``
built from ``Settings.database_url``. Request-scoped session lifecycle and
FastAPI DI wiring are introduced in a later phase; this module only owns the
engine/session-factory construction.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine (created once)."""
    settings: Settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory (created once)."""
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
    )
