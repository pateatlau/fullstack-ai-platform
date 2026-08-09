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


async def dispose_engine_cache() -> None:
    """Dispose the cached engine (if any) and clear factory caches.

    Required when the app lifespan or an HTTP test client shuts down on one
    anyio event loop and a later test runs on a fresh loop — a disposed engine
    left in ``lru_cache`` raises "attached to a different loop".
    """
    cache_clear = getattr(get_engine, "cache_clear", None)
    if callable(cache_clear):
        if get_engine.cache_info().currsize:
            await get_engine().dispose()
        cache_clear()

    sessionmaker_cache_clear = getattr(get_sessionmaker, "cache_clear", None)
    if callable(sessionmaker_cache_clear):
        sessionmaker_cache_clear()


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
