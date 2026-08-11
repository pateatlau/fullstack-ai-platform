"""HITL observability span and metric tests (Epic 09 Phase 7)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.agent.executor import AgentExecutor, ToolRunner
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.plan import PlannedStep, StepAction
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.planner import ReActPlanner
from app.ai.agent.scratchpad import Scratchpad, ScratchpadStore
from app.ai.agent.streaming import InMemoryStreamPublisher, NoOpStreamPublisher
from app.ai.hitl.models import ApprovalKind, ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.observability.metrics.instruments import (
    MetricInstruments,
    record_hitl_decision_metrics,
    record_hitl_resume_latency_ms,
    record_hitl_tool_execution_latency_ms,
)
from app.ai.observability.metrics.meter import MeterRegistry
from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
)
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, echo_handler
from app.providers.base import ProviderToolCall, ProviderToolCompletion
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import ApprovalDecision, NodeType, RunStatus
from app.ai.workflow.nodes.approval_node import ApprovalNodeExecutor
from app.ai.prompts.manager import create_prompt_manager
from app.core.caller import CallerContext
from app.core.config import Settings
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.ai.workflow.test_approval_node import (
    FakeTaskExecutor,
    _approval_linear_definition,
    _await_scheduled,
)
from tests.ai.workflow.test_interfaces import FakeWorkflowStore
from tests.fakes import FakeChatStore, FakeProvider


pytestmark = pytest.mark.anyio


class _SensitiveHandler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True, data={"ok": True})


@pytest.fixture(autouse=True)
def _reset_observability() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()


@pytest.fixture
def observability_stack() -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    settings = Settings(openai_api_key="test-key", observability_enabled=True)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    MeterRegistry._initialized = True
    MeterRegistry._enabled = True
    MetricInstruments.initialize()
    return exporter, reader


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_file",
            description="delete",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            requires_approval=True,
        ),
        _SensitiveHandler(),
    )
    return registry


def _registry_with_echo() -> ToolRegistry:
    registry = _registry()
    registry.register(ECHO_TOOL_DEFINITION, echo_handler())
    return registry


def _service(
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    registry: ToolRegistry,
) -> AgentApprovalService:
    return AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
    )


def _approval_spans(exporter: InMemorySpanExporter) -> list:
    return [
        span for span in exporter.get_finished_spans() if span.name == "approval.decide"
    ]


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


def _resume_executor(
    *,
    registry: ToolRegistry,
    scratchpad_store: ScratchpadStore,
) -> AgentExecutor:
    provider = FakeProvider(response="Done.")
    tool_executor = ToolExecutor(registry=registry, settings=Settings())
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=registry,
        stream_publisher=NoOpStreamPublisher(),
        hitl_enabled=False,
    )
    return AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=registry,
            prompt_manager=create_prompt_manager(),
            scratchpad_store=scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=NoOpStreamPublisher(),
        scratchpad_store=scratchpad_store,
        prompt_manager=create_prompt_manager(),
    )


async def test_pause_emits_approval_span_and_pending_metric(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, reader = observability_stack
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=uuid.uuid4())
    service = _service(InMemoryApprovalStore(), chat_store, _registry())
    scratchpad = Scratchpad("exec-obs")
    state = AgentExecutionState(
        execution_id="exec-obs",
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="step-1",
        action=StepAction.TOOL_CALL,
        tool_calls=[
            ToolCall(name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1")
        ],
    )

    approval = await service.pause(
        step,
        scratchpad=scratchpad,
        state=state,
        session_id=session.id,
        owner_id=session.user_id,  # type: ignore[arg-type]
        execution_id="exec-obs",
        stream_publisher=InMemoryStreamPublisher(),
    )

    spans = _approval_spans(exporter)
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["approval_id"] == str(approval.id)
    assert attrs["approval_kind"] == ApprovalKind.AGENT_TOOL.value
    assert attrs["approval_status"] == ApprovalStatus.PENDING.value
    assert attrs["approval_correlation_id"] == str(approval.approval_correlation_id)
    assert _metric_sum(reader, "agent_tool_approval_pending_count") == 1.0


async def test_revise_emits_approval_span_with_edited(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, _reader = observability_stack
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    service = _service(store, FakeChatStore(), _registry())
    approval = await store.create(
        session_id=uuid.uuid4(),
        owner_id=owner_id,
        execution_id="exec-revise",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
            )
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-revise", "status": "waiting_approval"},
    )

    await service.revise(
        approval.id,
        edited_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/edited"}, call_id="c1"
            )
        ],
        owner_id=owner_id,
    )

    spans = _approval_spans(exporter)
    assert len(spans) == 1
    attrs = dict(spans[-1].attributes or {})
    assert attrs["approval_status"] == ApprovalStatus.PENDING.value
    assert attrs["edited"] is True


async def test_decide_reject_emits_span_and_decision_metrics(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, reader = observability_stack
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    service = _service(store, chat_store, _registry())
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-reject",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
            )
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-reject", "status": "waiting_approval"},
    )
    await store.link_pending_message(
        approval.id,
        pending_message_id=uuid.uuid4(),
    )

    await service.decide(
        approval.id,
        owner_id=owner_id,
        decision="rejected",
        reason="no",
    )

    spans = _approval_spans(exporter)
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["approval_decision"] == "rejected"
    assert attrs["approval_status"] == ApprovalStatus.REJECTED.value
    assert "decision_latency_ms" in attrs
    assert _metric_sum(reader, "agent_tool_approval_pending_count") == -1.0
    assert _metric_sum(reader, "approval_decisions_total") == 1.0
    assert _metric_present(reader, "hitl_approval_decision_latency_ms")


async def test_decide_reject_records_metrics_before_placeholder_update_fails(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    _, reader = observability_stack
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()

    class _FailingUpdateChatStore(FakeChatStore):
        async def update_message(self, message_id: uuid.UUID, **kwargs: object):
            del message_id, kwargs
            raise RuntimeError("update failed")

    failing_chat_store = _FailingUpdateChatStore()
    service = _service(store, failing_chat_store, _registry())
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-reject-metrics",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
            )
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-reject-metrics",
            "status": "waiting_approval",
        },
    )
    await store.link_pending_message(
        approval.id,
        pending_message_id=uuid.uuid4(),
    )

    with pytest.raises(RuntimeError, match="update failed"):
        await service.decide(
            approval.id,
            owner_id=owner_id,
            decision="rejected",
        )

    assert _metric_sum(reader, "agent_tool_approval_pending_count") == -1.0
    assert _metric_sum(reader, "approval_decisions_total") == 1.0


async def test_approve_and_resume_links_tool_span_correlation_id(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, reader = observability_stack
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry()
    service = _service(store, chat_store, registry)
    scratchpad_store = ScratchpadStore()
    session = await chat_store.create_session(user_id=owner_id)
    correlation_id = uuid.uuid4()
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-resume",
        approval_correlation_id=correlation_id,
        proposed_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
            )
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-resume", "status": "waiting_approval"},
    )
    caller = CallerContext.for_user(owner_id)

    await service.approve_and_resume(
        approval.id,
        owner_id=owner_id,
        executor=_resume_executor(registry=registry, scratchpad_store=scratchpad_store),
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="delete")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=2),
        ),
        context=AgentContext(
            execution_id="exec-resume",
            caller=caller,
            session_id=session.id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    approval_spans = _approval_spans(exporter)
    assert len(approval_spans) == 1
    approval_attrs = dict(approval_spans[0].attributes or {})
    assert approval_attrs["approval_decision"] == "approved"
    assert approval_attrs["approval_correlation_id"] == str(correlation_id)

    tool_spans = [
        span for span in exporter.get_finished_spans() if span.name == "tool.execute"
    ]
    assert len(tool_spans) == 1
    tool_attrs = dict(tool_spans[0].attributes or {})
    assert tool_attrs["approval_correlation_id"] == str(correlation_id)
    assert _metric_present(reader, "hitl_resume_latency_ms")
    assert _metric_present(reader, "hitl_tool_execution_latency_ms")


async def test_resume_does_not_leak_approval_correlation_id_to_later_tools(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, reader = observability_stack
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry_with_echo()
    service = _service(store, chat_store, registry)
    scratchpad_store = ScratchpadStore()
    session = await chat_store.create_session(user_id=owner_id)
    correlation_id = uuid.uuid4()
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-resume-leak",
        approval_correlation_id=correlation_id,
        proposed_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
            )
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-resume-leak", "status": "waiting_approval"},
    )
    caller = CallerContext.for_user(owner_id)
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Echo next.",
                tool_calls=[
                    ProviderToolCall(
                        id="tc-echo",
                        name="echo",
                        arguments={"message": "hi"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Done.", finish_reason="stop", tool_calls=[]
            ),
        ]
    )
    tool_executor = ToolExecutor(registry=registry, settings=Settings())
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=registry,
        stream_publisher=NoOpStreamPublisher(),
        hitl_enabled=False,
    )
    executor = AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=registry,
            prompt_manager=create_prompt_manager(),
            scratchpad_store=scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=NoOpStreamPublisher(),
        scratchpad_store=scratchpad_store,
        prompt_manager=create_prompt_manager(),
    )

    await service.approve_and_resume(
        approval.id,
        owner_id=owner_id,
        executor=executor,
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="delete")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=3),
        ),
        context=AgentContext(
            execution_id="exec-resume-leak",
            caller=caller,
            session_id=session.id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    tool_spans = [
        span for span in exporter.get_finished_spans() if span.name == "tool.execute"
    ]
    assert len(tool_spans) == 2
    correlated = [
        span
        for span in tool_spans
        if dict(span.attributes or {}).get("approval_correlation_id")
        == str(correlation_id)
    ]
    assert len(correlated) == 1
    assert dict(correlated[0].attributes or {})["tool_name"] == "delete_file"
    assert _metric_sum(reader, "hitl_tool_execution_latency_ms") >= 0.0


async def test_workflow_apply_decision_emits_span_and_metrics(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, reader = observability_stack
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )
    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-obs"
    )
    await _await_scheduled(manager)
    paused = await manager.get_run(run.id, owner_id=owner_id)
    assert paused is not None
    assert paused.status is RunStatus.WAITING_APPROVAL

    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )
    await _await_scheduled(manager)

    spans = _approval_spans(exporter)
    assert any(
        dict(span.attributes or {}).get("approval_kind")
        == ApprovalKind.WORKFLOW_NODE.value
        for span in spans
    )
    assert _metric_sum(reader, "approval_decisions_total") >= 1.0
    assert _metric_present(reader, "hitl_approval_decision_latency_ms")
    assert _metric_present(reader, "hitl_resume_latency_ms")


async def test_workflow_apply_decision_records_metrics_before_continue_fails(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    _, reader = observability_stack
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )
    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-obs-fail"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    with patch(
        "app.ai.workflow.manager.WorkflowExecutor.continue_from_approval",
        new=AsyncMock(side_effect=RuntimeError("continue failed")),
    ):
        with pytest.raises(RuntimeError, match="continue failed"):
            await manager.apply_decision(
                run.id,
                approval_execution.id,
                owner_id=owner_id,
                decision=ApprovalDecision.APPROVED,
            )

    assert _metric_sum(reader, "approval_decisions_total") >= 1.0
    assert not _metric_present(reader, "hitl_resume_latency_ms")


def test_hitl_metric_helpers_record_both_kinds(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    _, reader = observability_stack
    for kind in ("agent_tool", "workflow_node"):
        record_hitl_decision_metrics(
            kind=kind,
            decision="approved",
            decision_latency_ms=10,
        )
        record_hitl_resume_latency_ms(kind=kind, latency_ms=20)
        record_hitl_tool_execution_latency_ms(kind=kind, latency_ms=30)

    assert _metric_sum(reader, "approval_decisions_total") == 2.0
    assert _metric_present(reader, "hitl_approval_decision_latency_ms")
    assert _metric_present(reader, "hitl_resume_latency_ms")
    assert _metric_present(reader, "hitl_tool_execution_latency_ms")


async def test_hitl_observability_disabled_emits_nothing() -> None:
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=uuid.uuid4())
    service = _service(InMemoryApprovalStore(), chat_store, _registry())
    scratchpad = Scratchpad("exec-off")
    state = AgentExecutionState(
        execution_id="exec-off",
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="step-1",
        action=StepAction.TOOL_CALL,
        tool_calls=[
            ToolCall(name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1")
        ],
    )

    await service.pause(
        step,
        scratchpad=scratchpad,
        state=state,
        session_id=session.id,
        owner_id=session.user_id,  # type: ignore[arg-type]
        execution_id="exec-off",
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert TracerRegistry.is_enabled() is False
    assert MeterRegistry.is_enabled() is False
