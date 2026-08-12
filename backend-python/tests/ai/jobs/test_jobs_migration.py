"""Background Jobs migration smoke tests (Epic 10 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_migration_upgrade_downgrade_smoke() -> None:
    alembic_cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))

    script = ScriptDirectory.from_config(alembic_cfg)
    revision = script.get_revision("0012_background_jobs")
    assert revision is not None
    assert revision.down_revision == "0011_hitl_lifecycle_audit"
    assert callable(revision.module.downgrade)
    assert callable(revision.module.upgrade)


@pytest.mark.anyio
async def test_background_jobs_table_exists(db_session) -> None:
    result = await db_session.execute(
        text("SELECT to_regclass('public.background_jobs') IS NOT NULL")
    )
    if not result.scalar():
        pytest.skip("background_jobs not available — run alembic upgrade head")

    columns = await db_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'background_jobs'
            """
        )
    )
    names = {row[0] for row in columns.fetchall()}
    assert {
        "id",
        "job_type",
        "status",
        "payload",
        "result",
        "attempt_count",
        "max_attempts",
        "version",
        "run_at",
        "locked_by",
        "locked_at",
        "last_error",
        "idempotency_key",
        "schedule_id",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    }.issubset(names)

    status_check = await db_session.execute(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'background_jobs'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%status%'
            """
        )
    )
    definition = status_check.scalar_one()
    assert "dead_letter" in definition
    assert "cancelled" in definition


@pytest.mark.anyio
async def test_background_job_schedules_table_exists(db_session) -> None:
    result = await db_session.execute(
        text("SELECT to_regclass('public.background_job_schedules') IS NOT NULL")
    )
    if not result.scalar():
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    columns = await db_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'background_job_schedules'
            """
        )
    )
    names = {row[0] for row in columns.fetchall()}
    assert {
        "id",
        "name",
        "job_type",
        "payload",
        "interval_seconds",
        "next_run_at",
        "version",
        "status",
        "created_at",
        "updated_at",
    }.issubset(names)


def test_migration_0013_upgrade_downgrade_smoke() -> None:
    alembic_cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))

    script = ScriptDirectory.from_config(alembic_cfg)
    revision = script.get_revision("0013_background_job_schedules")
    assert revision is not None
    assert revision.down_revision == "0012_background_jobs"
    assert callable(revision.module.downgrade)
    assert callable(revision.module.upgrade)
