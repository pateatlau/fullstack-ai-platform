import os

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
# Existing chat tests exercise the stateless contract; persistence is opt-in and
# enabled explicitly by the Phase 5 persistence tests.
os.environ.setdefault("CHAT_PERSISTENCE_ENABLED", "false")
# Streaming tests assume the default enabled path; local .env may set false for dev.
os.environ.setdefault("CHAT_STREAMING_ENABLED", "true")

import pytest
from collections.abc import Iterator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.middleware.rate_limit import reset_rate_limiter


@pytest.fixture(autouse=True)
def _isolate_rate_limit_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Prevent the shared test client IP bucket from tripping default limits.
    monkeypatch.setenv("RATE_LIMIT_ANONYMOUS_PER_MINUTE", "100000")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_PER_MINUTE", "100000")
    reset_rate_limiter()
    get_settings.cache_clear()
    yield
    reset_rate_limiter()
    get_settings.cache_clear()


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
