"""PostgreSQL-backed ``WorkflowStore`` (Epic 06).

Phase 2 implements definition CRUD. Phase 3+ implements run checkpointing,
node executions, and approval decision persistence.
"""

from __future__ import annotations

import uuid

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.workflow.exceptions import WorkflowNotFoundError, WorkflowValidationError
from app.ai.workflow.models import (
    ApprovalDecision,
    DefinitionStatus,
    NodeStatus,
    RunStatus,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.core.config import Settings
from app.db.models import WorkflowDefinitionRecord, WorkflowRunRecord

_RUN_METHODS_MSG = (
    "PostgresWorkflowStore run persistence is not implemented until Phase 3+."
)
_CONCURRENT_UPDATE_MSG = (
    "Workflow definition was modified concurrently; retry the update."
)


class PostgresWorkflowStore:
    """Concrete ``WorkflowStore`` backed by PostgreSQL (Part I)."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def create_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition:
        row = _definition_to_orm(definition)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _definition_to_domain(row)

    async def update_definition(
        self,
        definition: WorkflowDefinition,
        *,
        expected_version: int | None = None,
        require_no_runs: bool = False,
    ) -> WorkflowDefinition:
        if expected_version is not None:
            if require_no_runs:
                run_count = await self._session.scalar(
                    select(func.count())
                    .select_from(WorkflowRunRecord)
                    .where(
                        WorkflowRunRecord.workflow_definition_id == definition.id,
                        WorkflowRunRecord.owner_id == definition.owner_id,
                    )
                )
                if run_count:
                    raise WorkflowValidationError(
                        "Workflow definition has runs and cannot be updated in place; "
                        "create a new version instead."
                    )

            stmt = (
                update(WorkflowDefinitionRecord)
                .where(
                    WorkflowDefinitionRecord.id == definition.id,
                    WorkflowDefinitionRecord.owner_id == definition.owner_id,
                    WorkflowDefinitionRecord.version == expected_version,
                )
                .values(
                    name=definition.name,
                    description=definition.description,
                    status=definition.status.value,
                    entry_node_id=definition.entry_node_id,
                    graph=_graph_to_json(definition),
                    metadata_json=definition.metadata,
                    updated_at=definition.updated_at,
                )
                .returning(WorkflowDefinitionRecord)
            )
            row = (await self._session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise WorkflowValidationError(_CONCURRENT_UPDATE_MSG)
            await self._session.flush()
            await self._session.refresh(row)
            return _definition_to_domain(row)

        existing = await self._session.scalar(
            select(WorkflowDefinitionRecord).where(
                WorkflowDefinitionRecord.id == definition.id,
                WorkflowDefinitionRecord.owner_id == definition.owner_id,
            )
        )
        if existing is None:
            raise WorkflowNotFoundError(
                f"Workflow definition {definition.id} not found."
            )

        existing.name = definition.name
        existing.description = definition.description
        existing.status = definition.status.value
        existing.entry_node_id = definition.entry_node_id
        existing.graph = _graph_to_json(definition)
        existing.metadata_json = definition.metadata
        existing.updated_at = definition.updated_at
        await self._session.flush()
        await self._session.refresh(existing)
        return _definition_to_domain(existing)

    async def get_definition(
        self, definition_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowDefinition | None:
        row = await self._session.scalar(
            select(WorkflowDefinitionRecord).where(
                WorkflowDefinitionRecord.id == definition_id,
                WorkflowDefinitionRecord.owner_id == owner_id,
            )
        )
        if row is None:
            return None
        return _definition_to_domain(row)

    async def list_definitions(
        self,
        *,
        owner_id: uuid.UUID,
        status: DefinitionStatus | None = None,
    ) -> list[WorkflowDefinition]:
        stmt = select(WorkflowDefinitionRecord).where(
            WorkflowDefinitionRecord.owner_id == owner_id
        )
        if status is not None:
            stmt = stmt.where(WorkflowDefinitionRecord.status == status.value)
        stmt = stmt.order_by(
            WorkflowDefinitionRecord.name,
            WorkflowDefinitionRecord.version.desc(),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_definition_to_domain(row) for row in rows]

    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        raise NotImplementedError(_RUN_METHODS_MSG)

    async def get_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun | None:
        raise NotImplementedError(_RUN_METHODS_MSG)

    async def get_run_by_idempotency_key(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID,
        idempotency_key: str,
    ) -> WorkflowRun | None:
        raise NotImplementedError(_RUN_METHODS_MSG)

    async def get_run_with_executions(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> tuple[WorkflowRun, list[WorkflowNodeExecution]] | None:
        raise NotImplementedError(_RUN_METHODS_MSG)

    async def checkpoint_run(
        self, run: WorkflowRun, *, expected_checkpoint_version: int
    ) -> WorkflowRun:
        raise NotImplementedError(_RUN_METHODS_MSG)

    async def list_runs(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
    ) -> list[WorkflowRun]:
        raise NotImplementedError(_RUN_METHODS_MSG)

    async def append_node_execution(
        self, execution: WorkflowNodeExecution
    ) -> WorkflowNodeExecution:
        raise NotImplementedError(_RUN_METHODS_MSG)

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
        raise NotImplementedError(_RUN_METHODS_MSG)


def _graph_to_json(definition: WorkflowDefinition) -> dict[str, object]:
    return {
        "nodes": [node.model_dump(mode="json") for node in definition.nodes],
        "edges": [edge.model_dump(mode="json") for edge in definition.edges],
    }


def _definition_to_orm(definition: WorkflowDefinition) -> WorkflowDefinitionRecord:
    return WorkflowDefinitionRecord(
        id=definition.id,
        owner_id=definition.owner_id,
        name=definition.name,
        description=definition.description,
        version=definition.version,
        status=definition.status.value,
        entry_node_id=definition.entry_node_id,
        graph=_graph_to_json(definition),
        metadata_json=definition.metadata,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


def _definition_to_domain(row: WorkflowDefinitionRecord) -> WorkflowDefinition:
    graph = row.graph
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])
    try:
        if not isinstance(raw_nodes, list):
            raise WorkflowValidationError(
                f"Persisted graph nodes must be a list for definition {row.id}."
            )
        if not isinstance(raw_edges, list):
            raise WorkflowValidationError(
                f"Persisted graph edges must be a list for definition {row.id}."
            )
        nodes = [WorkflowNode.model_validate(node) for node in raw_nodes]
        edges = [WorkflowEdge.model_validate(edge) for edge in raw_edges]
    except ValidationError as exc:
        raise WorkflowValidationError(str(exc)) from exc
    return WorkflowDefinition(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        description=row.description,
        version=row.version,
        status=DefinitionStatus(row.status),
        entry_node_id=row.entry_node_id,
        nodes=nodes,
        edges=edges,
        metadata=row.metadata_json,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
