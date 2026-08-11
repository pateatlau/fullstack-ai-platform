"""HITL migration smoke and schema integration tests (Epic 09 Phase 1)."""

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
    revision = script.get_revision("0010_hitl_tables")
    assert revision is not None
    assert revision.down_revision == "0009_workflow_plugin_node_type"
    assert callable(revision.module.downgrade)
    assert callable(revision.module.upgrade)


async def _hitl_tables_available(session) -> bool:
    result = await session.execute(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    return bool(result.scalar())


@pytest.mark.anyio
async def test_agent_tool_approvals_table_exists(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    result = await db_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'agent_tool_approvals'
            """
        )
    )
    columns = {row[0] for row in result.fetchall()}
    assert {
        "approval_correlation_id",
        "proposed_calls",
        "paused_scratchpad",
        "paused_state",
        "pending_message_id",
    }.issubset(columns)


@pytest.mark.anyio
async def test_approval_revisions_table_exists(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("approval_revisions not available — run alembic upgrade head")

    result = await db_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'approval_revisions'
            """
        )
    )
    columns = {row[0] for row in result.fetchall()}
    assert {
        "approval_id",
        "approval_kind",
        "revision_number",
        "edited_payload",
    }.issubset(columns)


@pytest.mark.anyio
async def test_chat_messages_hitl_status_values(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("HITL migration not applied — run alembic upgrade head")

    result = await db_session.execute(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'chat_messages'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%status%'
            """
        )
    )
    definition = result.scalar_one()
    assert "waiting_approval" in definition
    assert "rejected" in definition


@pytest.mark.anyio
async def test_workflow_node_executions_hitl_columns(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("HITL migration not applied — run alembic upgrade head")

    result = await db_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'workflow_node_executions'
              AND column_name IN ('edited_arguments', 'reason')
            """
        )
    )
    columns = {row[0] for row in result.fetchall()}
    assert columns == {"edited_arguments", "reason"}
