"""Tests for ``WorkflowManager`` definition CRUD (Epic 06 Phase 2)."""

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
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


def _valid_definition(owner_id: uuid.UUID | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id or uuid.uuid4(),
        name="Sample Workflow",
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.anyio
async def test_create_definition_persists_valid_graph() -> None:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store)
    definition = _valid_definition()

    created = await manager.create_definition(definition)

    assert created == definition
    assert await store.get_definition(definition.id, owner_id=definition.owner_id)


@pytest.mark.anyio
async def test_create_definition_rejects_invalid_graph() -> None:
    manager = WorkflowManager(FakeWorkflowStore())
    definition = _valid_definition()
    definition = definition.model_copy(
        update={
            "edges": [
                WorkflowEdge(id="e1", from_node_id="start", to_node_id="end"),
                WorkflowEdge(id="e2", from_node_id="end", to_node_id="start"),
            ],
        }
    )

    with pytest.raises(WorkflowValidationError):
        await manager.create_definition(definition)


@pytest.mark.anyio
async def test_create_definition_rejects_active_invalid_graph() -> None:
    manager = WorkflowManager(FakeWorkflowStore())
    definition = _valid_definition().model_copy(
        update={"status": DefinitionStatus.ACTIVE}
    )
    definition = definition.model_copy(
        update={
            "edges": [
                WorkflowEdge(id="e1", from_node_id="start", to_node_id="end"),
                WorkflowEdge(id="e2", from_node_id="end", to_node_id="start"),
            ],
        }
    )

    with pytest.raises(WorkflowValidationError):
        await manager.create_definition(definition)


@pytest.mark.anyio
async def test_list_definitions_is_owner_scoped() -> None:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store)
    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    owned = _valid_definition(owner_id=owner_id)
    other = _valid_definition(owner_id=other_owner_id)
    await store.create_definition(owned)
    await store.create_definition(other)

    listed = await manager.list_definitions(owner_id=owner_id)

    assert listed == [owned]


@pytest.mark.anyio
async def test_get_definition_hides_other_owners() -> None:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store)
    definition = _valid_definition()
    await store.create_definition(definition)

    assert await manager.get_definition(definition.id, owner_id=uuid.uuid4()) is None


@pytest.mark.anyio
async def test_update_definition_in_place_when_no_runs() -> None:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store)
    owner_id = uuid.uuid4()
    definition = _valid_definition(owner_id=owner_id)
    await store.create_definition(definition)

    updated = definition.model_copy(
        update={
            "name": "Renamed Workflow",
            "updated_at": datetime.datetime.now(datetime.UTC),
        }
    )
    result = await manager.update_definition(updated, owner_id=owner_id)

    assert result.id == definition.id
    assert result.version == 1
    assert result.name == "Renamed Workflow"


@pytest.mark.anyio
async def test_update_definition_creates_new_version_when_runs_exist() -> None:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store)
    owner_id = uuid.uuid4()
    definition = _valid_definition(owner_id=owner_id)
    await store.create_definition(definition)
    await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="run-1",
            status=RunStatus.COMPLETED,
            context=WorkflowContext(),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )

    updated = definition.model_copy(
        update={
            "name": "Version 2",
            "updated_at": datetime.datetime.now(datetime.UTC),
        }
    )
    result = await manager.update_definition(updated, owner_id=owner_id)

    assert result.id != definition.id
    assert result.version == 2
    assert result.name == "Version 2"
    original = await store.get_definition(definition.id, owner_id=owner_id)
    assert original is not None
    assert original.version == 1


@pytest.mark.anyio
async def test_update_definition_not_found() -> None:
    manager = WorkflowManager(FakeWorkflowStore())
    definition = _valid_definition()

    with pytest.raises(WorkflowNotFoundError):
        await manager.update_definition(definition, owner_id=definition.owner_id)


@pytest.mark.anyio
async def test_archive_definition_sets_status() -> None:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store)
    owner_id = uuid.uuid4()
    definition = _valid_definition(owner_id=owner_id)
    await store.create_definition(definition)

    archived = await manager.archive_definition(definition.id, owner_id=owner_id)

    assert archived.status is DefinitionStatus.ARCHIVED


@pytest.mark.anyio
async def test_archive_definition_not_found() -> None:
    manager = WorkflowManager(FakeWorkflowStore())

    with pytest.raises(WorkflowNotFoundError):
        await manager.archive_definition(uuid.uuid4(), owner_id=uuid.uuid4())
