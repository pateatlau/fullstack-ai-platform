"""Tests for ``WorkflowManager`` skeleton (Phase 1)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import WorkflowEdge
from tests.ai.workflow.test_interfaces import FakeWorkflowStore


@pytest.mark.anyio
async def test_get_definition_delegates_to_store() -> None:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store)
    owner_id = uuid.uuid4()

    assert await manager.get_definition(uuid.uuid4(), owner_id=owner_id) is None


@pytest.mark.anyio
async def test_get_run_delegates_to_store() -> None:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store)
    owner_id = uuid.uuid4()

    assert await manager.get_run(uuid.uuid4(), owner_id=owner_id) is None


@pytest.mark.anyio
async def test_create_definition_validates_graph() -> None:
    from tests.ai.workflow.test_interfaces import _definition

    manager = WorkflowManager(FakeWorkflowStore())

    with pytest.raises(WorkflowValidationError):
        await manager.create_definition(
            _definition(uuid.uuid4()).model_copy(
                update={
                    "edges": [
                        WorkflowEdge(
                            id="cycle",
                            from_node_id="start",
                            to_node_id="start",
                        )
                    ],
                }
            )
        )


@pytest.mark.anyio
async def test_start_run_not_implemented_in_phase_1() -> None:
    manager = WorkflowManager(FakeWorkflowStore())

    with pytest.raises(NotImplementedError, match="Phase 3"):
        await manager.start_run(
            uuid.uuid4(),
            owner_id=uuid.uuid4(),
            idempotency_key="key-1",
        )
