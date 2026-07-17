import os

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
# Existing chat tests exercise the stateless contract; persistence is opt-in and
# enabled explicitly by the Phase 5 persistence tests.
os.environ.setdefault("CHAT_PERSISTENCE_ENABLED", "false")

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings


@pytest.fixture
async def db_session():
    """Yield a real ``AsyncSession`` for integration tests, or skip if the DB is down.

    A fresh ``NullPool`` engine per test avoids reusing pooled connections across
    anyio's per-test event loops. The session is rolled back after each test so
    integration tests leave no residue.
    """
    engine = create_async_engine(Settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"Postgres not available: {exc}")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
