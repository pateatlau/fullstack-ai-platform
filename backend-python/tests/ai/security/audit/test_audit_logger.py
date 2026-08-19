"""AuditLogger unit tests (Epic 11 Phase 3) — fake store, no DB required."""

from __future__ import annotations

import uuid

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.tracing.provider import TracerRegistry, get_tracer
from app.ai.security.audit.logger import AuditLogger
from app.ai.security.audit.models import AuditEvent, AuditOutcome
from app.core.caller import CallerContext
from app.core.config import Settings


class FakeAuditStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[AuditEvent] = []
        self._fail = fail

    async def insert(self, event: AuditEvent) -> None:
        if self._fail:
            raise RuntimeError("simulated DB failure")
        self.events.append(event)

    async def get_by_id(self, event_id: uuid.UUID) -> AuditEvent | None:
        for event in self.events:
            if event.id == event_id:
                return event
        return None

    async def query(self, **_: object) -> list[AuditEvent]:
        return list(self.events)


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "security_governance_enabled": True,
        "security_audit_log_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_tracer_registry():
    TracerRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()


@pytest.mark.anyio
async def test_record_inserts_one_event_with_actor_and_metadata() -> None:
    store = FakeAuditStore()
    logger = AuditLogger(store, settings=_settings())
    user_id = uuid.uuid4()

    await logger.record(
        actor=CallerContext.for_user(user_id),
        action="tool.execution.denied",
        outcome=AuditOutcome.DENIED,
        resource_type="tool",
        resource_id="web_search",
        metadata={"reason": "denied"},
    )

    assert len(store.events) == 1
    event = store.events[0]
    assert event.actor_user_id == user_id
    assert event.actor_kind == "user"
    assert event.action == "tool.execution.denied"
    assert event.outcome is AuditOutcome.DENIED
    assert event.resource_type == "tool"
    assert event.resource_id == "web_search"
    assert event.metadata == {"reason": "denied"}


@pytest.mark.anyio
async def test_record_is_noop_when_master_flag_off() -> None:
    store = FakeAuditStore()
    logger = AuditLogger(store, settings=_settings(security_governance_enabled=False))

    await logger.record(
        actor=None, action="login.succeeded", outcome=AuditOutcome.SUCCESS
    )

    assert store.events == []


@pytest.mark.anyio
async def test_record_is_noop_when_audit_log_sub_flag_off() -> None:
    store = FakeAuditStore()
    logger = AuditLogger(store, settings=_settings(security_audit_log_enabled=False))

    await logger.record(
        actor=None, action="login.succeeded", outcome=AuditOutcome.SUCCESS
    )

    assert store.events == []


@pytest.mark.anyio
async def test_record_never_raises_on_db_failure() -> None:
    store = FakeAuditStore(fail=True)
    logger = AuditLogger(store, settings=_settings())

    # No exception propagates to the caller.
    await logger.record(
        actor=None, action="login.succeeded", outcome=AuditOutcome.SUCCESS
    )


@pytest.mark.anyio
async def test_record_rejects_unknown_action_without_raising() -> None:
    store = FakeAuditStore()
    logger = AuditLogger(store, settings=_settings())

    await logger.record(
        actor=None, action="not.a.real.action", outcome=AuditOutcome.SUCCESS
    )

    assert store.events == []


@pytest.mark.anyio
async def test_trace_id_null_when_observability_disabled() -> None:
    store = FakeAuditStore()
    logger = AuditLogger(store, settings=_settings(observability_enabled=False))

    await logger.record(
        actor=None, action="login.succeeded", outcome=AuditOutcome.SUCCESS
    )

    assert store.events[0].trace_id is None


@pytest.mark.anyio
async def test_trace_id_populated_when_observability_enabled() -> None:
    memory_exporter = InMemorySpanExporter()
    settings = _settings(observability_enabled=True)
    TracerRegistry.initialize(
        settings, extra_span_processors=[SimpleSpanProcessor(memory_exporter)]
    )
    store = FakeAuditStore()
    logger = AuditLogger(store, settings=settings)

    tracer = get_tracer("test")
    with tracer.start_as_current_span("probe"):
        await logger.record(
            actor=None, action="login.succeeded", outcome=AuditOutcome.SUCCESS
        )

    assert store.events[0].trace_id is not None
    assert len(store.events[0].trace_id) == 32
