"""``PostgresAuditStore`` insert/query round-trip (Epic 11 Phase 3)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.security.audit.models import AuditEvent, AuditOutcome
from app.ai.security.audit.store import PostgresAuditStore
from tests.ai.jobs.conftest import make_queue_session_factory


async def _audit_events_table_available(db_session) -> bool:
    from sqlalchemy import text

    result = await db_session.scalar(
        text("SELECT to_regclass('public.audit_events') IS NOT NULL")
    )
    return bool(result)


def _event(**overrides: object) -> AuditEvent:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "occurred_at": datetime.datetime.now(datetime.UTC),
        "actor_user_id": uuid.uuid4(),
        "actor_kind": "user",
        "action": "tool.execution.denied",
        "resource_type": "tool",
        "resource_id": "web_search",
        "outcome": AuditOutcome.DENIED,
        "metadata": {"reason": "denied"},
    }
    base.update(overrides)
    return AuditEvent(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_insert_and_query_round_trip(db_session) -> None:
    if not await _audit_events_table_available(db_session):
        pytest.skip("audit_events table not available — run alembic upgrade head")

    store = PostgresAuditStore(make_queue_session_factory(db_session.bind))
    event = _event()
    await store.insert(event)

    rows = await store.query(actor_user_id=event.actor_user_id)
    assert await store.count(actor_user_id=event.actor_user_id) == 1

    assert len(rows) == 1
    fetched = rows[0]
    assert fetched.id == event.id
    assert fetched.action == "tool.execution.denied"
    assert fetched.outcome is AuditOutcome.DENIED
    assert fetched.resource_type == "tool"
    assert fetched.resource_id == "web_search"
    assert fetched.metadata == {"reason": "denied"}


@pytest.mark.anyio
async def test_query_filters_by_action_and_outcome(db_session) -> None:
    if not await _audit_events_table_available(db_session):
        pytest.skip("audit_events table not available — run alembic upgrade head")

    store = PostgresAuditStore(make_queue_session_factory(db_session.bind))
    await store.insert(
        _event(action="tool.execution.denied", outcome=AuditOutcome.DENIED)
    )
    await store.insert(_event(action="login.succeeded", outcome=AuditOutcome.SUCCESS))

    denied_only = await store.query(outcome=AuditOutcome.DENIED)
    login_only = await store.query(action="login.succeeded")

    assert all(e.outcome is AuditOutcome.DENIED for e in denied_only)
    assert all(e.action == "login.succeeded" for e in login_only)
