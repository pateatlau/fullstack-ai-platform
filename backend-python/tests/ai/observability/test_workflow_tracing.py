"""Workflow run/node span instrumentation tests."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.observability.tracing.spans import capture_current_span_context
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from app.ai.workflow.nodes.parallel_node import ForkNodeExecutor, JoinNodeExecutor
from app.core.config import Settings
from tests.ai.workflow.test_approval_node import FakeTaskExecutor, _await_scheduled
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

pytestmark = pytest.mark.anyio

_NOW = datetime.datetime.now(datetime.UTC)


@pytest.fixture(autouse=True)
def _reset_tracer_registry() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    settings = Settings(openai_api_key="test-key", observability_enabled=True)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    return exporter


def _linear_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Linear Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _fork_join_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Parallel Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="fork",
                type=NodeType.FORK,
                config={"join_node_id": "join"},
            ),
            WorkflowNode(id="left", type=NodeType.TASK, config={}),
            WorkflowNode(id="right", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="join",
                type=NodeType.JOIN,
                config={"fork_node_id": "fork", "join_policy": "all"},
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="start", to_node_id="fork"),
            WorkflowEdge(id="e2", from_node_id="fork", to_node_id="left"),
            WorkflowEdge(id="e3", from_node_id="fork", to_node_id="right"),
            WorkflowEdge(id="e4", from_node_id="left", to_node_id="join"),
            WorkflowEdge(id="e5", from_node_id="right", to_node_id="join"),
            WorkflowEdge(id="e6", from_node_id="join", to_node_id="end"),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _manager(
    store: FakeWorkflowStore, task_executor: FakeTaskExecutor
) -> WorkflowManager:
    return WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.FORK: ForkNodeExecutor(max_parallel_branches=8),
            NodeType.JOIN: JoinNodeExecutor(),
        },
    )


@contextmanager
def _request_span():
    from app.ai.observability.tracing.spans import get_tracer

    tracer = get_tracer("test")
    span = tracer.start_span("http.server")
    token = otel_context.attach(trace.set_span_in_context(span))
    try:
        yield span
    finally:
        otel_context.detach(token)
        span.end()


async def test_workflow_run_and_node_spans_emitted(
    memory_exporter: InMemorySpanExporter,
) -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_linear_definition(owner_id))
    task_executor = FakeTaskExecutor()
    manager = _manager(store, task_executor)

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="trace-1"
    )
    await _await_scheduled(manager)

    run_spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "workflow.run"
    ]
    node_spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "workflow.node"
    ]
    assert len(run_spans) == 1
    task_node_spans = [
        span
        for span in node_spans
        if dict(span.attributes or {}).get("node_type") == NodeType.TASK.value
    ]
    assert len(task_node_spans) == 1
    run_attrs = dict(run_spans[0].attributes or {})
    node_attrs = dict(task_node_spans[0].attributes or {})
    assert run_attrs["run_id"] == str(run.id)
    assert run_attrs["status"] == RunStatus.COMPLETED.value
    assert node_attrs["node_type"] == NodeType.TASK.value
    assert node_attrs["attempt"] == 1
    assert node_attrs["status"] == "succeeded"


async def test_parallel_fork_join_emits_concurrent_node_spans(
    memory_exporter: InMemorySpanExporter,
) -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_fork_join_definition(owner_id))
    task_executor = FakeTaskExecutor()
    manager = _manager(store, task_executor)

    await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="parallel-trace"
    )
    await _await_scheduled(manager)

    node_spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "workflow.node"
    ]
    task_node_spans = [
        span
        for span in node_spans
        if dict(span.attributes or {}).get("node_type") == NodeType.TASK.value
    ]
    assert len(task_node_spans) >= 3
    node_types = {dict(span.attributes or {}).get("node_type") for span in node_spans}
    assert NodeType.JOIN.value in node_types


async def test_background_run_links_to_originating_request_span(
    memory_exporter: InMemorySpanExporter,
) -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_linear_definition(owner_id))
    manager = _manager(store, FakeTaskExecutor())

    with _request_span() as request_span:
        request_context = request_span.get_span_context()
        await manager.start_run(
            definition.id, owner_id=owner_id, idempotency_key="linked-run"
        )
        await _await_scheduled(manager)

    run_span = next(
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "workflow.run"
    )
    assert len(run_span.links) == 1
    link_context = run_span.links[0].context
    assert link_context.trace_id == request_context.trace_id
    assert link_context.span_id == request_context.span_id
    assert link_context.trace_flags == request_context.trace_flags


async def test_background_run_without_valid_context_has_no_link(
    memory_exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_linear_definition(owner_id))
    manager = _manager(store, FakeTaskExecutor())

    monkeypatch.setattr(
        "app.ai.workflow.manager.capture_current_span_context",
        lambda: None,
    )

    await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="no-link-run"
    )
    await _await_scheduled(manager)

    final = await manager.get_run(
        (await store.list_runs(owner_id=owner_id))[0].id,
        owner_id=owner_id,
    )
    assert final is not None
    assert final.status is RunStatus.COMPLETED

    run_span = next(
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "workflow.run"
    )
    assert len(run_span.links) == 0


async def test_resume_opens_fresh_root_span_without_link(
    memory_exporter: InMemorySpanExporter,
) -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_fork_join_definition(owner_id))
    manager = _manager(store, FakeTaskExecutor())

    with _request_span():
        run = await manager.start_run(
            definition.id, owner_id=owner_id, idempotency_key="resume-trace"
        )
        await _await_scheduled(manager)

    memory_exporter.clear()
    completed = await manager.get_run(run.id, owner_id=owner_id)
    assert completed is not None
    await store.checkpoint_run(
        completed.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "completed_at": None,
                "checkpoint_version": completed.checkpoint_version + 1,
            }
        ),
        expected_checkpoint_version=completed.checkpoint_version,
    )
    manager._last_scheduled_run_task = None

    await manager.resume(run.id, owner_id=owner_id)
    await _await_scheduled(manager)

    run_spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "workflow.run"
    ]
    assert len(run_spans) == 1
    assert len(run_spans[0].links) == 0
    assert dict(run_spans[0].attributes or {}).get("resume_reason") == "resume"


async def test_reconcile_orphaned_runs_open_fresh_root_span_without_link(
    memory_exporter: InMemorySpanExporter,
) -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_linear_definition(owner_id))

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_definition_id=definition.id,
        owner_id=owner_id,
        idempotency_key="orphan",
        status=RunStatus.RUNNING,
        context=WorkflowContext(),
        current_node_ids=["start"],
        checkpoint_version=0,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )
    await store.get_or_create_run(run)
    manager = _manager(store, FakeTaskExecutor())

    reconciled = await manager.reconcile_orphaned_runs()
    await _await_scheduled(manager)

    assert reconciled == 1
    run_spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "workflow.run"
    ]
    assert len(run_spans) == 1
    assert len(run_spans[0].links) == 0
    assert dict(run_spans[0].attributes or {}).get("resume_reason") == "reconcile"


async def test_capture_current_span_context_returns_none_when_invalid() -> None:
    token = otel_context.attach(otel_context.Context())
    try:
        assert capture_current_span_context() is None
    finally:
        otel_context.detach(token)


async def test_capture_current_span_context_returns_snapshot_when_valid(
    memory_exporter: InMemorySpanExporter,
) -> None:
    del memory_exporter
    from app.ai.observability.tracing.spans import get_tracer

    tracer = get_tracer("test")
    span = tracer.start_span("parent")
    token = otel_context.attach(trace.set_span_in_context(span))
    try:
        snapshot = capture_current_span_context()
        assert snapshot is not None
        assert snapshot.is_valid
        parent = span.get_span_context()
        assert snapshot.trace_id == parent.trace_id
        assert snapshot.span_id == parent.span_id
    finally:
        otel_context.detach(token)
        span.end()


def test_span_context_snapshot_roundtrips_w3c_trace_state() -> None:
    from opentelemetry.trace.span import TraceState

    from app.ai.observability.tracing.spans import SpanContextSnapshot

    header = TraceState([("vendor", "opaque")]).to_header()
    snapshot = SpanContextSnapshot(
        trace_id=0xABC,
        span_id=0xDEF,
        trace_flags=1,
        trace_state=header,
    )

    assert snapshot.to_span_context().trace_state.to_header() == header
