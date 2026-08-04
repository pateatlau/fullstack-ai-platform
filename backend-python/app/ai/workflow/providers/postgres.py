"""PostgreSQL-backed ``WorkflowStore`` (Epic 06).

Phase 2 implements definition CRUD. Phase 3 implements run checkpointing,
node executions. Approval decision persistence lands in Phase 7.

Run/node-execution writes (``create_run``, ``checkpoint_run``) commit
immediately rather than only flushing, unlike the definition CRUD methods
above: a run must be durably visible to the *separate* DB session used by its
background ``WorkflowExecutor`` task as soon as it is created, and every node
transition must survive a crash between it and the next one (Part I §
Checkpoint-per-transition). ``append_node_execution`` only flushes — it is
always immediately followed by a ``checkpoint_run`` call in the same
transaction, which commits both together atomically.
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
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.core.config import Settings
from app.db.models import (
    WorkflowDefinitionRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
)

_APPROVAL_METHODS_MSG = (
    "PostgresWorkflowStore approval decision persistence is not implemented "
    "until Phase 7."
)
_CONCURRENT_UPDATE_MSG = (
    "Workflow definition was modified concurrently; retry the update."
)
_CONCURRENT_RUN_UPDATE_MSG = (
    "Workflow run checkpoint was modified concurrently; retry the update."
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
        row = _run_to_orm(run)
        self._session.add(row)
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(row)
        return _run_to_domain(row)

    async def get_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun | None:
        row = await self._session.scalar(
            select(WorkflowRunRecord).where(
                WorkflowRunRecord.id == run_id,
                WorkflowRunRecord.owner_id == owner_id,
            )
        )
        return _run_to_domain(row) if row is not None else None

    async def get_run_by_idempotency_key(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID,
        idempotency_key: str,
    ) -> WorkflowRun | None:
        row = await self._session.scalar(
            select(WorkflowRunRecord).where(
                WorkflowRunRecord.owner_id == owner_id,
                WorkflowRunRecord.workflow_definition_id == workflow_definition_id,
                WorkflowRunRecord.idempotency_key == idempotency_key,
            )
        )
        return _run_to_domain(row) if row is not None else None

    async def get_run_with_executions(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> tuple[WorkflowRun, list[WorkflowNodeExecution]] | None:
        run = await self.get_run(run_id, owner_id=owner_id)
        if run is None:
            return None
        stmt = (
            select(WorkflowNodeExecutionRecord)
            .where(WorkflowNodeExecutionRecord.run_id == run_id)
            .order_by(
                WorkflowNodeExecutionRecord.started_at,
                WorkflowNodeExecutionRecord.attempt,
            )
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return run, [_node_execution_to_domain(row) for row in rows]

    async def checkpoint_run(
        self, run: WorkflowRun, *, expected_checkpoint_version: int
    ) -> WorkflowRun:
        stmt = (
            update(WorkflowRunRecord)
            .where(
                WorkflowRunRecord.id == run.id,
                WorkflowRunRecord.owner_id == run.owner_id,
                WorkflowRunRecord.checkpoint_version == expected_checkpoint_version,
            )
            .values(
                status=run.status.value,
                context=run.context.model_dump(mode="json"),
                current_node_ids=list(run.current_node_ids),
                checkpoint_version=run.checkpoint_version,
                error=run.error,
                updated_at=run.updated_at,
                started_at=run.started_at,
                completed_at=run.completed_at,
            )
            .returning(WorkflowRunRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise WorkflowValidationError(_CONCURRENT_RUN_UPDATE_MSG)
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(row)
        return _run_to_domain(row)

    async def list_runs(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
    ) -> list[WorkflowRun]:
        stmt = select(WorkflowRunRecord).where(WorkflowRunRecord.owner_id == owner_id)
        if workflow_definition_id is not None:
            stmt = stmt.where(
                WorkflowRunRecord.workflow_definition_id == workflow_definition_id
            )
        if status is not None:
            stmt = stmt.where(WorkflowRunRecord.status == status.value)
        stmt = stmt.order_by(WorkflowRunRecord.created_at.desc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_run_to_domain(row) for row in rows]

    async def append_node_execution(
        self, execution: WorkflowNodeExecution
    ) -> WorkflowNodeExecution:
        existing = await self._session.get(WorkflowNodeExecutionRecord, execution.id)
        if existing is None:
            row = _node_execution_to_orm(execution)
            self._session.add(row)
        else:
            _apply_node_execution_updates(existing, execution)
            row = existing
        await self._session.flush()
        await self._session.refresh(row)
        return _node_execution_to_domain(row)

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
        raise NotImplementedError(_APPROVAL_METHODS_MSG)


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


def _run_to_orm(run: WorkflowRun) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        id=run.id,
        workflow_definition_id=run.workflow_definition_id,
        owner_id=run.owner_id,
        idempotency_key=run.idempotency_key,
        session_id=run.session_id,
        status=run.status.value,
        context=run.context.model_dump(mode="json"),
        current_node_ids=list(run.current_node_ids),
        checkpoint_version=run.checkpoint_version,
        error=run.error,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _run_to_domain(row: WorkflowRunRecord) -> WorkflowRun:
    return WorkflowRun(
        id=row.id,
        workflow_definition_id=row.workflow_definition_id,
        owner_id=row.owner_id,
        idempotency_key=row.idempotency_key,
        session_id=row.session_id,
        status=RunStatus(row.status),
        context=WorkflowContext.model_validate(row.context),
        current_node_ids=list(row.current_node_ids),
        checkpoint_version=row.checkpoint_version,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _node_execution_to_orm(
    execution: WorkflowNodeExecution,
) -> WorkflowNodeExecutionRecord:
    return WorkflowNodeExecutionRecord(
        id=execution.id,
        run_id=execution.run_id,
        node_id=execution.node_id,
        node_type=execution.node_type.value,
        attempt=execution.attempt,
        status=execution.status.value,
        input=execution.input,
        output=execution.output,
        error=execution.error,
        decided_by=execution.decided_by,
        decided_at=execution.decided_at,
        decision=execution.decision.value if execution.decision is not None else None,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
    )


def _apply_node_execution_updates(
    row: WorkflowNodeExecutionRecord, execution: WorkflowNodeExecution
) -> None:
    row.status = execution.status.value
    row.input = execution.input
    row.output = execution.output
    row.error = execution.error
    row.decided_by = execution.decided_by
    row.decided_at = execution.decided_at
    row.decision = execution.decision.value if execution.decision is not None else None
    row.started_at = execution.started_at
    row.completed_at = execution.completed_at


def _node_execution_to_domain(
    row: WorkflowNodeExecutionRecord,
) -> WorkflowNodeExecution:
    return WorkflowNodeExecution(
        id=row.id,
        run_id=row.run_id,
        node_id=row.node_id,
        node_type=NodeType(row.node_type),
        attempt=row.attempt,
        status=NodeStatus(row.status),
        input=row.input,
        output=row.output,
        error=row.error,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision=ApprovalDecision(row.decision) if row.decision is not None else None,
        started_at=row.started_at,
        completed_at=row.completed_at,
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
