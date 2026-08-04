"""WorkflowStore protocol (public API — stable after Phase 1).

All Workflow persistence implementations implement this interface. The rest of
the platform depends only on this protocol, never on a concrete provider
(Part I § Workflow Store Contract).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.ai.workflow.models import (
    ApprovalDecision,
    DefinitionStatus,
    NodeStatus,
    RunStatus,
    WorkflowDefinition,
    WorkflowNodeExecution,
    WorkflowRun,
)


class WorkflowStore(Protocol):
    """Persist and retrieve workflow definitions, runs, and node executions."""

    async def create_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition: ...

    async def update_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition: ...

    async def get_definition(
        self, definition_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowDefinition | None: ...

    async def list_definitions(
        self,
        *,
        owner_id: uuid.UUID,
        status: DefinitionStatus | None = None,
    ) -> list[WorkflowDefinition]: ...

    async def create_run(self, run: WorkflowRun) -> WorkflowRun: ...

    async def get_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun | None: ...

    async def get_run_by_idempotency_key(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID,
        idempotency_key: str,
    ) -> WorkflowRun | None: ...

    async def get_run_with_executions(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> tuple[WorkflowRun, list[WorkflowNodeExecution]] | None: ...

    async def checkpoint_run(
        self, run: WorkflowRun, *, expected_checkpoint_version: int
    ) -> WorkflowRun: ...

    async def list_runs(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
    ) -> list[WorkflowRun]: ...

    async def append_node_execution(
        self, execution: WorkflowNodeExecution
    ) -> WorkflowNodeExecution: ...

    async def record_approval_decision(
        self,
        execution_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        decision: ApprovalDecision,
        decided_by: uuid.UUID,
        node_status: NodeStatus,
        run: WorkflowRun,
    ) -> WorkflowNodeExecution: ...
