"""Tests for ``WorkflowManager`` run lifecycle (Epic 06 Phase 3)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.workflow.exceptions import WorkflowNotFoundError, WorkflowValidationError
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeType,
    RunStatus,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


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


async def _await_scheduled(manager: WorkflowManager) -> None:
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task


@pytest.mark.anyio
async def test_start_run_creates_a_running_run() -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(_active_definition(owner_id))
    manager = WorkflowManager(store)

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )

    assert run.workflow_definition_id == definition.id
    assert run.owner_id == owner_id
    assert run.status is RunStatus.RUNNING
    await _await_scheduled(manager)


@pytest.mark.anyio
async def test_start_run_is_idempotent_for_same_key() -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(_active_definition(owner_id))
    manager = WorkflowManager(store)

    first = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    second = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )

    assert first.id == second.id
    all_runs = await store.list_runs(
        owner_id=owner_id, workflow_definition_id=definition.id
    )
    assert len(all_runs) == 1


@pytest.mark.anyio
async def test_start_run_with_different_keys_creates_separate_runs() -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(_active_definition(owner_id))
    manager = WorkflowManager(store)

    first = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    second = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-2"
    )
    await _await_scheduled(manager)

    assert first.id != second.id


@pytest.mark.anyio
async def test_start_run_rejects_missing_definition() -> None:
    manager = WorkflowManager(FakeWorkflowStore())

    with pytest.raises(WorkflowNotFoundError):
        await manager.start_run(
            uuid.uuid4(), owner_id=uuid.uuid4(), idempotency_key="key-1"
        )


@pytest.mark.anyio
async def test_start_run_rejects_draft_definition() -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    draft = await store.create_definition(
        _active_definition(owner_id).model_copy(
            update={"status": DefinitionStatus.DRAFT}
        )
    )
    manager = WorkflowManager(store)

    with pytest.raises(WorkflowValidationError, match="active"):
        await manager.start_run(draft.id, owner_id=owner_id, idempotency_key="key-1")


@pytest.mark.anyio
async def test_get_run_hides_other_owners_run() -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(_active_definition(owner_id))
    manager = WorkflowManager(store)
    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)

    assert await manager.get_run(run.id, owner_id=uuid.uuid4()) is None
    assert (await manager.get_run(run.id, owner_id=owner_id)) is not None


@pytest.mark.anyio
async def test_list_runs_is_owner_scoped() -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    definition = await store.create_definition(_active_definition(owner_id))
    other_definition = await store.create_definition(_active_definition(other_owner_id))
    manager = WorkflowManager(store)

    await manager.start_run(definition.id, owner_id=owner_id, idempotency_key="key-1")
    await _await_scheduled(manager)
    await manager.start_run(
        other_definition.id, owner_id=other_owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)

    owned_runs = await manager.list_runs(owner_id=owner_id)
    assert len(owned_runs) == 1
    assert owned_runs[0].owner_id == owner_id
