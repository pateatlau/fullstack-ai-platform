"""Tests for PostgresWorkflowStore helpers."""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowNodeExecution,
)
from app.ai.workflow.providers.postgres import (
    PostgresWorkflowStore,
    _definition_to_domain,
)
from app.core.config import Settings
from app.db.identity import SqlUserStore
from app.db.models import WorkflowDefinitionRecord

_NOW = datetime.datetime.now(datetime.UTC)


async def _workflow_tables_available(session) -> bool:
    result = await session.scalar(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'workflow_runs'"
        )
    )
    return result == 1


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"workflow-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    return user.id


def _record(*, graph: dict[str, object]) -> WorkflowDefinitionRecord:
    return WorkflowDefinitionRecord(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        name="Test Workflow",
        description=None,
        version=1,
        status="draft",
        entry_node_id="start",
        graph=graph,
        metadata_json={},
        created_at=_NOW,
        updated_at=_NOW,
    )


def _active_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Active Workflow",
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


def _run_payload(*, owner_id: uuid.UUID, definition_id: uuid.UUID) -> WorkflowRun:
    return WorkflowRun(
        id=uuid.uuid4(),
        workflow_definition_id=definition_id,
        owner_id=owner_id,
        idempotency_key="concurrent-key",
        status=RunStatus.RUNNING,
        context=WorkflowContext(),
        checkpoint_version=0,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )


def test_definition_to_domain_rejects_malformed_node_entry() -> None:
    row = _record(
        graph={
            "nodes": [{"id": "start", "type": "task"}, "not-a-node"],
            "edges": [],
        }
    )

    with pytest.raises(WorkflowValidationError):
        _definition_to_domain(row)


def test_definition_to_domain_rejects_non_list_nodes() -> None:
    row = _record(graph={"nodes": "invalid", "edges": []})

    with pytest.raises(WorkflowValidationError, match="nodes must be a list"):
        _definition_to_domain(row)


@pytest.mark.anyio
async def test_create_run_concurrent_idempotent_starts_return_same_run(
    db_session,
) -> None:
    if not await _workflow_tables_available(db_session):
        pytest.skip("workflow tables not available — run alembic upgrade head")

    settings = Settings(openai_api_key="test-key")
    owner_id = await _make_user(db_session)
    definition = _active_definition(owner_id)
    setup_store = PostgresWorkflowStore(db_session, settings)
    await setup_store.create_definition(definition)
    await db_session.commit()

    database_url = Settings().database_url
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_once() -> WorkflowRun:
        async with factory() as session:
            store = PostgresWorkflowStore(session, settings)
            return await store.create_run(
                _run_payload(owner_id=owner_id, definition_id=definition.id)
            )

    try:
        first, second = await asyncio.gather(create_once(), create_once())
    finally:
        await engine.dispose()

    assert first.id == second.id
    assert first.idempotency_key == "concurrent-key"


@pytest.mark.anyio
async def test_append_node_execution_replay_updates_committed_running_attempt(
    db_session,
) -> None:
    if not await _workflow_tables_available(db_session):
        pytest.skip("workflow tables not available — run alembic upgrade head")

    settings = Settings(openai_api_key="test-key")
    owner_id = await _make_user(db_session)
    definition = _active_definition(owner_id)
    store = PostgresWorkflowStore(db_session, settings)
    await store.create_definition(definition)
    run = await store.create_run(
        _run_payload(owner_id=owner_id, definition_id=definition.id).model_copy(
            update={"idempotency_key": "replay-key"}
        )
    )
    original_id = uuid.uuid4()
    running = WorkflowNodeExecution(
        id=original_id,
        run_id=run.id,
        node_id="start",
        node_type=NodeType.TASK,
        attempt=1,
        status=NodeStatus.RUNNING,
        input={"execution_receipt_id": f"{run.id}:start:1"},
        started_at=_NOW,
    )
    await store.append_node_execution(running)
    await db_session.commit()

    replay = running.model_copy(
        update={
            "id": uuid.uuid4(),
            "status": NodeStatus.SUCCEEDED,
            "output": {"ok": True},
            "completed_at": _NOW,
        }
    )
    updated = await store.append_node_execution(replay)

    assert updated.id == original_id
    assert updated.status is NodeStatus.SUCCEEDED
    assert updated.output == {"ok": True}

    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    _, executions = with_executions
    assert len(executions) == 1
    assert executions[0].id == original_id
