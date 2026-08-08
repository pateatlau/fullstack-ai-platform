"""Usage cost persistence and migration tests."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from alembic.config import Config
from app.ai.observability.cost.calculator import CostCalculator, CostRegistry
from app.ai.observability.cost.pricing import ModelPricingTable
from app.core.config import Settings
from app.db.chat import SqlChatStore
from app.db.identity import SqlUserStore
from app.db.models import UsageEvent
from app.db.usage import SqlUsageStore

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PRICING = _BACKEND_ROOT / "config" / "model_pricing.yaml"


async def _usage_cost_columns_available(session) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'usage_events'
              AND column_name IN ('cost_usd', 'pricing_version')
            """
        )
    )
    return int(result.scalar_one()) == 2


async def _skip_without_usage_cost_columns(session) -> None:
    if not await _usage_cost_columns_available(session):
        pytest.skip(
            "usage_events cost columns not available — run alembic upgrade head"
        )


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"usage-cost-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _init_cost_registry() -> None:
    CostRegistry.reset_for_tests()
    settings = Settings(
        openai_api_key="test-key",
        observability_enabled=True,
        observability_cost_pricing_version="2026-08",
    )
    table = ModelPricingTable.load(settings, pricing_file=_CANONICAL_PRICING)
    CostRegistry._initialized = True
    CostRegistry._calculator = CostCalculator(table)


@pytest.mark.anyio
async def test_usage_store_persists_cost_for_known_model(db_session) -> None:
    await _skip_without_usage_cost_columns(db_session)
    _init_cost_registry()
    chat_store = SqlChatStore(db_session)
    usage_store = SqlUsageStore(db_session)
    user_id = await _make_user(db_session)

    chat = await chat_store.create_session(user_id=user_id)
    seq = await chat_store.allocate_seq(chat.id)
    message = await chat_store.add_message(
        session_id=chat.id,
        seq=seq,
        role="assistant",
        content="hello",
        provider="openai",
        model="gpt-4o-mini",
    )

    event = await usage_store.record(
        session_id=chat.id,
        user_id=user_id,
        message_id=message.id,
        provider="openai",
        model="gpt-4o-mini",
        token_source="provider_reported",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
    )

    assert event.cost_usd is not None
    assert float(event.cost_usd) > 0
    assert event.pricing_version == "2026-08"

    CostRegistry.reset_for_tests()


@pytest.mark.anyio
async def test_usage_store_leaves_cost_null_for_unknown_model(db_session) -> None:
    await _skip_without_usage_cost_columns(db_session)
    _init_cost_registry()
    chat_store = SqlChatStore(db_session)
    usage_store = SqlUsageStore(db_session)
    user_id = await _make_user(db_session)

    chat = await chat_store.create_session(user_id=user_id)

    event = await usage_store.record(
        session_id=chat.id,
        user_id=user_id,
        provider="openai",
        model="not-in-pricing-table",
        token_source="provider_reported",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )

    assert event.cost_usd is None
    assert event.pricing_version is None

    CostRegistry.reset_for_tests()


@pytest.mark.anyio
async def test_usage_store_leaves_cost_null_when_observability_disabled(
    db_session,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    CostRegistry.reset_for_tests()
    CostRegistry.initialize(
        Settings(openai_api_key="test-key", observability_enabled=False)
    )

    chat_store = SqlChatStore(db_session)
    usage_store = SqlUsageStore(db_session)
    user_id = await _make_user(db_session)
    chat = await chat_store.create_session(user_id=user_id)

    event = await usage_store.record(
        session_id=chat.id,
        user_id=user_id,
        provider="openai",
        model="gpt-4o-mini",
        token_source="provider_reported",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )

    assert event.cost_usd is None
    assert event.pricing_version is None

    CostRegistry.reset_for_tests()


@pytest.mark.anyio
async def test_pricing_version_change_does_not_alter_existing_rows(db_session) -> None:
    await _skip_without_usage_cost_columns(db_session)
    _init_cost_registry()
    chat_store = SqlChatStore(db_session)
    usage_store = SqlUsageStore(db_session)
    user_id = await _make_user(db_session)
    chat = await chat_store.create_session(user_id=user_id)

    first = await usage_store.record(
        session_id=chat.id,
        user_id=user_id,
        provider="openai",
        model="gpt-4o-mini",
        token_source="provider_reported",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
    )
    assert first.cost_usd is not None
    original_cost = float(first.cost_usd)
    original_version = first.pricing_version

    settings = Settings(
        openai_api_key="test-key",
        observability_enabled=True,
        observability_cost_pricing_version="2026-08",
    )
    table_v2 = ModelPricingTable.load(settings, pricing_file=_CANONICAL_PRICING)
    CostRegistry._calculator = CostCalculator(table_v2)

    second = await usage_store.record(
        session_id=chat.id,
        user_id=user_id,
        provider="openai",
        model="gpt-4o-mini",
        token_source="provider_reported",
        prompt_tokens=2000,
        completion_tokens=1000,
        total_tokens=3000,
    )

    refreshed = await db_session.scalar(
        select(UsageEvent).where(UsageEvent.id == first.id)
    )
    assert refreshed is not None
    assert float(refreshed.cost_usd) == original_cost
    assert refreshed.pricing_version == original_version
    assert second.cost_usd is not None

    CostRegistry.reset_for_tests()


@pytest.mark.anyio
async def test_migration_adds_cost_columns(db_session) -> None:
    if not await _usage_cost_columns_available(db_session):
        pytest.skip(
            "usage_events cost columns not available — run alembic upgrade head"
        )

    result = await db_session.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'usage_events'
            """
        )
    )
    index_names = {row[0] for row in result.fetchall()}
    assert "ix_usage_events_provider_model_created" in index_names


def test_migration_upgrade_downgrade_smoke() -> None:
    alembic_cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))

    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    revision = script.get_revision("0008_observability_usage_cost")
    assert revision is not None
    assert revision.down_revision == "0007_workflow_tables"
    assert callable(revision.module.downgrade)
    assert callable(revision.module.upgrade)
