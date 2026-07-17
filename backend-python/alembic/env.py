"""Alembic environment configured for the async SQLAlchemy engine.

The database URL is read from application ``Settings`` (which loads it from the
environment / ``.env``) rather than from ``alembic.ini`` so there is a single
source of truth. ``Settings()`` is instantiated directly to avoid the provider
API-key validation performed by ``get_settings()`` — migrations must not depend
on LLM provider configuration.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.base import Base

# Import models so their metadata is registered on Base.metadata.
from app.db import models  # noqa: F401  (import for side effects)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _normalize_async_url(url: str) -> str:
    """Ensure the URL uses the asyncpg driver.

    Managed Postgres / CI secrets often provide ``postgres://`` or
    ``postgresql://`` URLs; Alembic here runs on the async engine, which needs
    the ``postgresql+asyncpg://`` driver prefix.
    """
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    return url


# Resolve the async database URL from application settings.
config.set_main_option("sqlalchemy.url", _normalize_async_url(Settings().database_url))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using the async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
