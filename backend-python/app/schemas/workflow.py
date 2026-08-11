"""Workflow REST API schemas (Epic 06 Phase 9).

Public responses never expose checkpoint versions, internal retry counters, or
other owners' data.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field, ValidationError

from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.models import (
    ApprovalDecision,
    DefinitionStatus,
    NodeRetryPolicy,
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

DEFAULT_WORKFLOW_LIST_LIMIT = 50
MAX_WORKFLOW_LIST_LIMIT = 100

__all__ = [
    "ApprovalDecisionRequest",
    "DEFAULT_WORKFLOW_LIST_LIMIT",
    "MAX_WORKFLOW_LIST_LIMIT",
    "StartWorkflowRunRequest",
    "WorkflowContextResponse",
    "WorkflowDefinitionCreateRequest",
    "WorkflowDefinitionListResponse",
    "WorkflowDefinitionResponse",
    "WorkflowDefinitionUpdateRequest",
    "WorkflowEdgeSchema",
    "WorkflowNodeExecutionResponse",
    "WorkflowNodeSchema",
    "WorkflowRunDetailResponse",
    "WorkflowRunListResponse",
    "WorkflowRunResponse",
    "to_definition_response",
    "to_node_execution_response",
    "to_run_detail_response",
    "to_run_response",
]


class WorkflowNodeSchema(BaseModel):
    """Public workflow node shape for definition APIs."""

    id: str
    type: NodeType
    config: dict[str, object] = Field(default_factory=dict)
    retry_policy: NodeRetryPolicy | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)


class WorkflowEdgeSchema(BaseModel):
    """Public workflow edge shape for definition APIs."""

    id: str
    from_node_id: str
    to_node_id: str
    condition: dict[str, object] | None = None


class WorkflowDefinitionCreateRequest(BaseModel):
    """Create a workflow definition from a validated graph payload."""

    name: str = Field(min_length=1)
    description: str | None = None
    status: DefinitionStatus = DefinitionStatus.DRAFT
    entry_node_id: str = Field(min_length=1)
    nodes: list[WorkflowNodeSchema] = Field(min_length=1)
    edges: list[WorkflowEdgeSchema] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class WorkflowDefinitionUpdateRequest(WorkflowDefinitionCreateRequest):
    """Update a workflow definition (in place or new version when runs exist)."""


class WorkflowDefinitionResponse(BaseModel):
    """Public workflow definition shape."""

    id: uuid.UUID
    name: str
    description: str | None
    version: int
    status: DefinitionStatus
    entry_node_id: str
    nodes: list[WorkflowNodeSchema]
    edges: list[WorkflowEdgeSchema]
    metadata: dict[str, object]
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WorkflowDefinitionListResponse(BaseModel):
    """Paginated list of caller-owned workflow definitions."""

    definitions: list[WorkflowDefinitionResponse] = Field(default_factory=list)
    limit: int
    offset: int
    total: int


class WorkflowContextResponse(BaseModel):
    """Public run context snapshot."""

    trigger_input: dict[str, object] = Field(default_factory=dict)
    variables: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class StartWorkflowRunRequest(BaseModel):
    """Start (or idempotently re-fetch) a workflow run."""

    idempotency_key: str = Field(min_length=1)
    trigger_input: dict[str, object] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    """Optional body for workflow approve/reject endpoints (Epic 09)."""

    edited_arguments: dict[str, object] | None = None
    reason: str | None = None


class WorkflowRunResponse(BaseModel):
    """Public workflow run snapshot."""

    id: uuid.UUID
    workflow_definition_id: uuid.UUID
    idempotency_key: str
    session_id: uuid.UUID | None
    status: RunStatus
    context: WorkflowContextResponse
    current_node_ids: list[str]
    error: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None


class WorkflowNodeExecutionResponse(BaseModel):
    """Public node execution history entry."""

    id: uuid.UUID
    run_id: uuid.UUID
    node_id: str
    node_type: NodeType
    status: NodeStatus
    input: dict[str, object]
    output: dict[str, object] | None
    error: str | None
    decided_by: uuid.UUID | None
    decided_at: datetime.datetime | None
    decision: ApprovalDecision | None
    edited_arguments: dict[str, object] | None = None
    reason: str | None = None
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None


class WorkflowRunDetailResponse(WorkflowRunResponse):
    """Run snapshot with node execution history."""

    node_executions: list[WorkflowNodeExecutionResponse] = Field(default_factory=list)


class WorkflowRunListResponse(BaseModel):
    """Paginated list of caller-owned workflow runs."""

    runs: list[WorkflowRunResponse] = Field(default_factory=list)
    limit: int
    offset: int
    total: int


def _node_to_schema(node: WorkflowNode) -> WorkflowNodeSchema:
    return WorkflowNodeSchema(
        id=node.id,
        type=node.type,
        config=node.config,
        retry_policy=node.retry_policy,
        timeout_seconds=node.timeout_seconds,
    )


def _edge_to_schema(edge: WorkflowEdge) -> WorkflowEdgeSchema:
    return WorkflowEdgeSchema(
        id=edge.id,
        from_node_id=edge.from_node_id,
        to_node_id=edge.to_node_id,
        condition=edge.condition,
    )


def _context_to_response(context: WorkflowContext) -> WorkflowContextResponse:
    return WorkflowContextResponse(
        trigger_input=context.trigger_input,
        variables=context.variables,
        metadata=context.metadata,
    )


def to_definition_response(
    definition: WorkflowDefinition,
) -> WorkflowDefinitionResponse:
    return WorkflowDefinitionResponse(
        id=definition.id,
        name=definition.name,
        description=definition.description,
        version=definition.version,
        status=definition.status,
        entry_node_id=definition.entry_node_id,
        nodes=[_node_to_schema(node) for node in definition.nodes],
        edges=[_edge_to_schema(edge) for edge in definition.edges],
        metadata=definition.metadata,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


def to_run_response(run: WorkflowRun) -> WorkflowRunResponse:
    return WorkflowRunResponse(
        id=run.id,
        workflow_definition_id=run.workflow_definition_id,
        idempotency_key=run.idempotency_key,
        session_id=run.session_id,
        status=run.status,
        context=_context_to_response(run.context),
        current_node_ids=list(run.current_node_ids),
        error=run.error,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def to_node_execution_response(
    execution: WorkflowNodeExecution,
) -> WorkflowNodeExecutionResponse:
    return WorkflowNodeExecutionResponse(
        id=execution.id,
        run_id=execution.run_id,
        node_id=execution.node_id,
        node_type=execution.node_type,
        status=execution.status,
        input=execution.input,
        output=execution.output,
        error=execution.error,
        decided_by=execution.decided_by,
        decided_at=execution.decided_at,
        decision=execution.decision,
        edited_arguments=execution.edited_arguments,
        reason=execution.reason,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
    )


def to_run_detail_response(
    run: WorkflowRun,
    executions: list[WorkflowNodeExecution],
) -> WorkflowRunDetailResponse:
    return WorkflowRunDetailResponse(
        **to_run_response(run).model_dump(),
        node_executions=[to_node_execution_response(item) for item in executions],
    )


def definition_from_create_request(
    request: WorkflowDefinitionCreateRequest,
    *,
    definition_id: uuid.UUID,
    owner_id: uuid.UUID,
    version: int,
    created_at: datetime.datetime,
    updated_at: datetime.datetime,
) -> WorkflowDefinition:
    try:
        return WorkflowDefinition(
            id=definition_id,
            owner_id=owner_id,
            name=request.name,
            description=request.description,
            version=version,
            status=request.status,
            entry_node_id=request.entry_node_id,
            nodes=[
                WorkflowNode(
                    id=node.id,
                    type=node.type,
                    config=node.config,
                    retry_policy=node.retry_policy,
                    timeout_seconds=node.timeout_seconds,
                )
                for node in request.nodes
            ],
            edges=[
                WorkflowEdge(
                    id=edge.id,
                    from_node_id=edge.from_node_id,
                    to_node_id=edge.to_node_id,
                    condition=edge.condition,
                )
                for edge in request.edges
            ],
            metadata=request.metadata,
            created_at=created_at,
            updated_at=updated_at,
        )
    except ValidationError as exc:
        raise WorkflowValidationError(str(exc)) from exc
    except ValueError as exc:
        raise WorkflowValidationError(str(exc)) from exc
