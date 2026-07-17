"""Local/dev seed script for the persistence layer.

Inserts a small, self-consistent dataset (one authenticated user, one guest
identity, one chat session, a few messages, one summary, and minimal usage
data) for manual testing and fixtures (plan Section 4.4).

Seeds never run in production: the script refuses to run when ``APP_ENV`` is
``production``. Run with ``uv run python -m app.db.seed`` (or ``make db-seed``)
after applying migrations.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import (
    ChatMessage,
    ChatSession,
    GuestIdentity,
    SessionSummary,
    UsageEvent,
    User,
)


async def _seed(session: AsyncSession) -> None:
    existing = await session.scalar(
        select(User).where(
            User.auth_provider == "google",
            User.external_auth_id == "demo-google-sub",
        )
    )
    if existing is not None:
        print("Seed data already present; skipping.")
        return

    user = User(
        email="demo.user@example.com",
        display_name="Demo User",
        picture_url=None,
        auth_provider="google",
        external_auth_id="demo-google-sub",
    )
    session.add(user)
    await session.flush()

    # A guest identity stores only the SHA-256 hash of the opaque token.
    guest_token = secrets.token_urlsafe(32)
    guest = GuestIdentity(
        token_hash=hashlib.sha256(guest_token.encode("utf-8")).hexdigest(),
    )
    session.add(guest)
    await session.flush()

    chat = ChatSession(
        user_id=user.id,
        title="Demo conversation",
        next_seq=4,
    )
    session.add(chat)
    await session.flush()

    system_msg = ChatMessage(
        session_id=chat.id,
        seq=1,
        role="system",
        content="You are a helpful assistant.",
    )
    user_msg = ChatMessage(
        session_id=chat.id,
        seq=2,
        role="user",
        content="Hello, who are you?",
    )
    assistant_msg = ChatMessage(
        session_id=chat.id,
        seq=3,
        role="assistant",
        content="I'm a demo assistant seeded for local testing.",
        provider="openai",
        model="gpt-4o-mini",
        status="complete",
        finish_reason="stop",
    )
    session.add_all([system_msg, user_msg, assistant_msg])
    await session.flush()

    summary = SessionSummary(
        session_id=chat.id,
        version=1,
        covers_through_seq=3,
        content="User greeted the assistant; assistant introduced itself.",
        provider="openai",
        model="gpt-4o-mini",
    )
    session.add(summary)

    usage = UsageEvent(
        session_id=chat.id,
        user_id=user.id,
        message_id=assistant_msg.id,
        kind="chat",
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=12,
        completion_tokens=10,
        total_tokens=22,
        token_source="estimated",
    )
    session.add(usage)

    print("Seed data inserted.")


async def main() -> None:
    settings = Settings()
    if settings.app_env == "production":
        raise SystemExit("Refusing to seed: APP_ENV=production.")

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                await _seed(session)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
