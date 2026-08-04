"""Tests for the WorkflowStore protocol (structural conformance)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.workflow.exceptions import WorkflowNotFoundError, WorkflowValidationError
from app.ai.workflow.interfaces import WorkflowStore
from app.ai.workflow.models import (
    ApprovalDecision,
    DefinitionStatus,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Test Workflow",
        entry_node_id="start",
        nodes=[WorkflowNode(id="start", type=NodeType.TASK, config={})],
        edges=[],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _run(owner_id: uuid.UUID, definition_id: uuid.UUID) -> WorkflowRun:
    return WorkflowRun(
        id=uuid.uuid4(),
        workflow_definition_id=definition_id,
        owner_id=owner_id,
        idempotency_key="key-1",
        status=RunStatus.RUNNING,
        context=WorkflowContext(trigger_input={"x": 1}),
        created_at=_NOW,
        updated_at=_NOW,
    )


class FakeWorkflowStore:
    """In-memory fake used to verify Protocol conformance."""

    def __init__(self) -> None:
        self._definitions: dict[uuid.UUID, WorkflowDefinition] = {}
        self._runs: dict[uuid.UUID, WorkflowRun] = {}
        self._executions: dict[uuid.UUID, WorkflowNodeExecution] = {}

    async def create_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition:
        self._definitions[definition.id] = definition
        return definition

    async def update_definition(
        self,
        definition: WorkflowDefinition,
        *,
        expected_version: int | None = None,
        require_no_runs: bool = False,
    ) -> WorkflowDefinition:
        existing = self._definitions.get(definition.id)
        if existing is None or existing.owner_id != definition.owner_id:
            raise WorkflowNotFoundError(
                f"Workflow definition {definition.id} not found."
            )
        if expected_version is not None:
            if existing.version != expected_version:
                raise WorkflowValidationError(
                    "Workflow definition was modified concurrently; retry the update."
                )
            if require_no_runs:
                has_runs = any(
                    run.workflow_definition_id == definition.id
                    and run.owner_id == definition.owner_id
                    for run in self._runs.values()
                )
                if has_runs:
                    raise WorkflowValidationError(
                        "Workflow definition has runs and cannot be updated in place; "
                        "create a new version instead."
                    )
        self._definitions[definition.id] = definition
        return definition

    async def get_definition(
        self, definition_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowDefinition | None:
        definition = self._definitions.get(definition_id)
        if definition is None or definition.owner_id != owner_id:
            return None
        return definition

    async def list_definitions(
        self,
        *,
        owner_id: uuid.UUID,
        status: DefinitionStatus | None = None,
    ) -> list[WorkflowDefinition]:
        results = [
            definition
            for definition in self._definitions.values()
            if definition.owner_id == owner_id
        ]
        if status is not None:
            results = [
                definition for definition in results if definition.status is status
            ]
        return results

    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        self._runs[run.id] = run
        return run

    async def get_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun | None:
        run = self._runs.get(run_id)
        if run is None or run.owner_id != owner_id:
            return None
        return run

    async def get_run_by_idempotency_key(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID,
        idempotency_key: str,
    ) -> WorkflowRun | None:
        for run in self._runs.values():
            if (
                run.owner_id == owner_id
                and run.workflow_definition_id == workflow_definition_id
                and run.idempotency_key == idempotency_key
            ):
                return run
        return None

    async def get_run_with_executions(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> tuple[WorkflowRun, list[WorkflowNodeExecution]] | None:
        run = await self.get_run(run_id, owner_id=owner_id)
        if run is None:
            return None
        executions = [
            execution
            for execution in self._executions.values()
            if execution.run_id == run_id
        ]
        return run, executions

    async def checkpoint_run(
        self, run: WorkflowRun, *, expected_checkpoint_version: int
    ) -> WorkflowRun:
        existing = self._runs.get(run.id)
        if existing is None:
            raise KeyError(run.id)
        if existing.checkpoint_version != expected_checkpoint_version:
            raise ValueError("checkpoint version conflict")
        self._runs[run.id] = run
        return run

    async def list_runs(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
    ) -> list[WorkflowRun]:
        results = [run for run in self._runs.values() if run.owner_id == owner_id]
        if workflow_definition_id is not None:
            results = [
                run
                for run in results
                if run.workflow_definition_id == workflow_definition_id
            ]
        if status is not None:
            results = [run for run in results if run.status is status]
        return results

    async def append_node_execution(
        self, execution: WorkflowNodeExecution
    ) -> WorkflowNodeExecution:
        self._executions[execution.id] = execution
        return execution

    async def record_approval_decision(
        self,
        execution_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        decision: ApprovalDecision,
        decided_by: uuid.UUID,
        node_status: NodeStatus,
        run: WorkflowRun,
    ) -> WorkflowNodeExecution:
        del owner_id
        execution = self._executions[execution_id]
        updated = execution.model_copy(
            update={
                "decision": decision,
                "decided_by": decided_by,
                "decided_at": _NOW,
                "status": node_status,
            }
        )
        self._executions[execution_id] = updated
        self._runs[run.id] = run
        return updated


class TestWorkflowStoreProtocol:
    @pytest.mark.anyio
    async def test_fake_store_conforms_to_protocol(self) -> None:
        store: WorkflowStore = FakeWorkflowStore()
        owner_id = uuid.uuid4()
        definition = _definition(owner_id)

        created = await store.create_definition(definition)
        assert created == definition

        fetched = await store.get_definition(definition.id, owner_id=owner_id)
        assert fetched == definition

        other_owner_fetch = await store.get_definition(
            definition.id, owner_id=uuid.uuid4()
        )
        assert other_owner_fetch is None

        listed = await store.list_definitions(owner_id=owner_id)
        assert listed == [definition]

        run = _run(owner_id, definition.id)
        created_run = await store.create_run(run)
        assert created_run == run

        by_key = await store.get_run_by_idempotency_key(
            owner_id=owner_id,
            workflow_definition_id=definition.id,
            idempotency_key="key-1",
        )
        assert by_key == run

        execution = WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run.id,
            node_id="start",
            node_type=NodeType.TASK,
            status=NodeStatus.SUCCEEDED,
        )
        await store.append_node_execution(execution)

        with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
        assert with_executions is not None
        assert with_executions[0] == run
        assert with_executions[1] == [execution]

        checkpointed = await store.checkpoint_run(
            run.model_copy(update={"checkpoint_version": 1}),
            expected_checkpoint_version=0,
        )
        assert checkpointed.checkpoint_version == 1

        runs = await store.list_runs(owner_id=owner_id, status=RunStatus.RUNNING)
        assert runs == [checkpointed]

        decided = await store.record_approval_decision(
            execution.id,
            owner_id=owner_id,
            decision=ApprovalDecision.APPROVED,
            decided_by=owner_id,
            node_status=NodeStatus.SUCCEEDED,
            run=checkpointed.model_copy(update={"status": RunStatus.COMPLETED}),
        )
        assert decided.decision is ApprovalDecision.APPROVED
