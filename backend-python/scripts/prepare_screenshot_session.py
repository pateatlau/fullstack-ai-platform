"""Create a screenshot demo user and JWT for Phase 6 asset capture."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.identity import SqlUserStore

SCREENSHOT_USER = {
    "email": "demo.user@example.com",
    "display_name": "Demo User",
    "picture_url": None,
}


async def main() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        store = SqlUserStore(session)
        user = await store.create(
            sub=f"screenshot-demo-{uuid.uuid4()}",
            email=SCREENSHOT_USER["email"],
            name=SCREENSHOT_USER["display_name"],
            picture=None,
        )
        await session.commit()

        token = create_access_token(user_id=user.id, settings=settings)
        user_payload = {
            "id": str(user.id),
            **SCREENSHOT_USER,
        }

    print(
        json.dumps(
            {
                "access_token": token,
                "user": user_payload,
            }
        )
    )
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
