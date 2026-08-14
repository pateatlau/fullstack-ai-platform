"""New business-level HITL metrics tests (Epic 09 recommendation #7)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import text

from app.ai.agent.models.plan import PlannedStep, StepAction
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.scratchpad import Scratchpad
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.hitl.models import ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.observability.metrics.instruments import MetricInstruments
from app.ai.observability.metrics.meter import MeterRegistry
from app.ai.tools.schemas import ToolCall
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _reset_observability() -> Iterator[None]:
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()
    yield
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    MeterRegistry._initialized = True
    MeterRegistry._enabled = True
    MetricInstruments.initialize()
    return reader


def _metric_sum(reader: InMemoryMetricReader, name: str) -> float:
    data = reader.get_metrics_data()
    assert data is not None
    total = 0.0
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    counter_value = getattr(point, "value", None)
                    if counter_value is not None:
                        total += float(counter_value)
                        continue
                    histogram_sum = getattr(point, "sum", None)
                    if histogram_sum is not None:
                        total += float(histogram_sum)
    return total


def _metric_present(reader: InMemoryMetricReader, name: str) -> bool:
    data = reader.get_metrics_data()
    assert data is not None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name and metric.data.data_points:
                    return True
    return False


async def test_pause_increments_approval_requests_total(
    metric_reader: InMemoryMetricReader,
) -> None:
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=uuid.uuid4())
    service = AgentApprovalService(
        approval_store=InMemoryApprovalStore(), chat_store=chat_store
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[
            ToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
    )

    await service.pause(
        step,
        scratchpad=Scratchpad("exec-metrics"),
        state=AgentExecutionState(
            execution_id="exec-metrics", status=AgentExecutionStatus.EXECUTING
        ),
        session_id=session.id,
        owner_id=session.user_id,  # type: ignore[arg-type]
        execution_id="exec-metrics",
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert _metric_sum(metric_reader, "approval_requests_total") == 1.0


async def test_decide_increments_duration_seconds_histogram(
    metric_reader: InMemoryMetricReader,
) -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-metrics-decide",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-metrics-decide",
            "status": "waiting_approval",
        },
    )
    service = AgentApprovalService(approval_store=store, chat_store=chat_store)

    await service.decide(approval.id, decider_id=owner_id, decision="rejected")

    assert _metric_present(metric_reader, "approval_duration_seconds")
    assert _metric_sum(metric_reader, "approval_decisions_total") == 1.0


async def test_cancel_increments_cancelled_total(
    metric_reader: InMemoryMetricReader,
) -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-metrics-cancel",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-metrics-cancel",
            "status": "waiting_approval",
        },
    )
    service = AgentApprovalService(approval_store=store, chat_store=chat_store)

    await service.cancel(approval.id, owner_id=owner_id)

    assert _metric_sum(metric_reader, "approval_cancelled_total") == 1.0


async def test_touching_expired_approval_emits_expired_metric(
    metric_reader: InMemoryMetricReader,
    db_session,
) -> None:
    """Lazy expiration (the real Postgres-backed store) records the metric

    the first time an expired-but-still-``pending`` row is touched — not at
    creation time, since expiry is on-read/on-write, not a background sweep
    (that automatic sweep is deferred to Epic 10).
    """
    from app.ai.hitl.exceptions import ApprovalExpiredError
    from app.ai.hitl.store import AgentToolApprovalStore
    from app.db.models import ChatSession, User

    result = await db_session.execute(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result.scalar():
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-metrics-{uuid.uuid4().hex}",
        email=f"hitl-metrics-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    chat_session = ChatSession(user_id=user.id, next_seq=1)
    db_session.add(chat_session)
    await db_session.flush()

    store = AgentToolApprovalStore(db_session)
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    approval = await store.create(
        session_id=chat_session.id,
        owner_id=user.id,
        execution_id="exec-metrics-expired",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-metrics-expired",
            "status": "waiting_approval",
        },
        expires_at=past,
    )

    with pytest.raises(ApprovalExpiredError):
        await store.require_for_owner(approval.id, owner_id=user.id)

    assert _metric_sum(metric_reader, "approval_expired_total") == 1.0
