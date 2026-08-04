"""PostgreSQL-backed ``WorkflowStore`` (Epic 06 Phase 1 scaffold).

Phase 2 implements definition CRUD. Phase 3+ implements run checkpointing,
node executions, and approval decision persistence.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.workflow.models import (
    ApprovalDecision,
    DefinitionStatus,
    NodeStatus,
    RunStatus,
    WorkflowDefinition,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.core.config import Settings

_PHASE1_MSG = "PostgresWorkflowStore persistence is not implemented until Phase 2+."


class PostgresWorkflowStore:
    """Concrete ``WorkflowStore`` backed by PostgreSQL (Part I)."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def create_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition:
        raise NotImplementedError(_PHASE1_MSG)

    async def update_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition:
        raise NotImplementedError(_PHASE1_MSG)

    async def get_definition(
        self, definition_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowDefinition | None:
        raise NotImplementedError(_PHASE1_MSG)

    async def list_definitions(
        self,
        *,
        owner_id: uuid.UUID,
        status: DefinitionStatus | None = None,
    ) -> list[WorkflowDefinition]:
        raise NotImplementedError(_PHASE1_MSG)

    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        raise NotImplementedError(_PHASE1_MSG)

    async def get_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun | None:
        raise NotImplementedError(_PHASE1_MSG)

    async def get_run_by_idempotency_key(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID,
        idempotency_key: str,
    ) -> WorkflowRun | None:
        raise NotImplementedError(_PHASE1_MSG)

    async def get_run_with_executions(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> tuple[WorkflowRun, list[WorkflowNodeExecution]] | None:
        raise NotImplementedError(_PHASE1_MSG)

    async def checkpoint_run(
        self, run: WorkflowRun, *, expected_checkpoint_version: int
    ) -> WorkflowRun:
        raise NotImplementedError(_PHASE1_MSG)

    async def list_runs(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
    ) -> list[WorkflowRun]:
        raise NotImplementedError(_PHASE1_MSG)

    async def append_node_execution(
        self, execution: WorkflowNodeExecution
    ) -> WorkflowNodeExecution:
        raise NotImplementedError(_PHASE1_MSG)

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
        raise NotImplementedError(_PHASE1_MSG)
