"""Authenticated Workflow management REST API (Epic 06 Phase 9)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Awaitable
from typing import NoReturn, TypeVar

from fastapi import APIRouter, Depends

from app.ai.deps import get_workflow_manager
from app.ai.workflow.exceptions import (
    WorkflowAccessDeniedError,
    WorkflowConcurrentUpdateError,
    WorkflowDecisionConflictError,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import ApprovalDecision
from app.core.caller import CallerContext, require_authenticated_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import bind_context
from app.schemas.workflow import (
    StartWorkflowRunRequest,
    WorkflowDefinitionCreateRequest,
    WorkflowDefinitionListResponse,
    WorkflowDefinitionResponse,
    WorkflowDefinitionUpdateRequest,
    WorkflowRunDetailResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
    definition_from_create_request,
    to_definition_response,
    to_run_detail_response,
    to_run_response,
)

router = APIRouter()

_T = TypeVar("_T")


def _require_workflow_enabled(settings: Settings) -> None:
    if not settings.workflow_engine_enabled:
        raise AppError(
            code="feature_disabled",
            message="Workflow Engine is not enabled on this server.",
            status_code=503,
        )


def _raise_workflow_error(exc: WorkflowError) -> NoReturn:
    if isinstance(exc, WorkflowNotFoundError):
        raise AppError(
            code="workflow_not_found",
            message=str(exc),
            status_code=404,
        ) from exc
    if isinstance(exc, WorkflowAccessDeniedError):
        raise AppError(
            code="workflow_access_denied",
            message=str(exc),
            status_code=403,
        ) from exc
    if isinstance(exc, WorkflowValidationError):
        raise AppError(
            code="workflow_validation_error",
            message=str(exc),
            status_code=422,
        ) from exc
    if isinstance(exc, WorkflowDecisionConflictError):
        raise AppError(
            code="workflow_decision_conflict",
            message=str(exc),
            status_code=409,
        ) from exc
    if isinstance(exc, WorkflowConcurrentUpdateError):
        raise AppError(
            code="workflow_concurrent_update",
            message=str(exc),
            status_code=409,
        ) from exc
    raise AppError(
        code=exc.code or "workflow_error",
        message=str(exc),
        status_code=500,
    ) from exc


async def _run_workflow_operation(coro: Awaitable[_T]) -> _T:
    try:
        return await coro
    except WorkflowError as exc:
        _raise_workflow_error(exc)


@router.post("/api/workflows", response_model=WorkflowDefinitionResponse)
async def create_workflow_definition(
    body: WorkflowDefinitionCreateRequest,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowDefinitionResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    now = datetime.datetime.now(datetime.UTC)
    definition = definition_from_create_request(
        body,
        definition_id=uuid.uuid4(),
        owner_id=caller.user_id,
        version=1,
        created_at=now,
        updated_at=now,
    )
    created = await _run_workflow_operation(
        workflow_manager.create_definition(definition)
    )
    return to_definition_response(created)


@router.get("/api/workflows", response_model=WorkflowDefinitionListResponse)
async def list_workflow_definitions(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowDefinitionListResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    definitions = await workflow_manager.list_definitions(owner_id=caller.user_id)
    return WorkflowDefinitionListResponse(
        definitions=[to_definition_response(item) for item in definitions]
    )


@router.get("/api/workflows/{definition_id}", response_model=WorkflowDefinitionResponse)
async def get_workflow_definition(
    definition_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowDefinitionResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    definition = await workflow_manager.get_definition(
        definition_id, owner_id=caller.user_id
    )
    if definition is None:
        raise AppError(
            code="workflow_not_found",
            message=f"Workflow definition {definition_id} not found.",
            status_code=404,
        )
    return to_definition_response(definition)


@router.put("/api/workflows/{definition_id}", response_model=WorkflowDefinitionResponse)
async def update_workflow_definition(
    definition_id: uuid.UUID,
    body: WorkflowDefinitionUpdateRequest,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowDefinitionResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    existing = await workflow_manager.get_definition(
        definition_id, owner_id=caller.user_id
    )
    if existing is None:
        raise AppError(
            code="workflow_not_found",
            message=f"Workflow definition {definition_id} not found.",
            status_code=404,
        )

    now = datetime.datetime.now(datetime.UTC)
    definition = definition_from_create_request(
        body,
        definition_id=definition_id,
        owner_id=caller.user_id,
        version=existing.version,
        created_at=existing.created_at,
        updated_at=now,
    )
    updated = await _run_workflow_operation(
        workflow_manager.update_definition(definition, owner_id=caller.user_id)
    )
    return to_definition_response(updated)


@router.delete(
    "/api/workflows/{definition_id}", response_model=WorkflowDefinitionResponse
)
async def archive_workflow_definition(
    definition_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowDefinitionResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    archived = await _run_workflow_operation(
        workflow_manager.archive_definition(definition_id, owner_id=caller.user_id)
    )
    return to_definition_response(archived)


@router.post(
    "/api/workflows/{definition_id}/runs",
    response_model=WorkflowRunResponse,
)
async def start_workflow_run(
    definition_id: uuid.UUID,
    body: StartWorkflowRunRequest,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowRunResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    run = await _run_workflow_operation(
        workflow_manager.start_run(
            definition_id,
            owner_id=caller.user_id,
            idempotency_key=body.idempotency_key,
            trigger_input=body.trigger_input,
        )
    )
    return to_run_response(run)


@router.get(
    "/api/workflows/{definition_id}/runs",
    response_model=WorkflowRunListResponse,
)
async def list_workflow_runs_for_definition(
    definition_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowRunListResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    definition = await workflow_manager.get_definition(
        definition_id, owner_id=caller.user_id
    )
    if definition is None:
        raise AppError(
            code="workflow_not_found",
            message=f"Workflow definition {definition_id} not found.",
            status_code=404,
        )

    runs = await workflow_manager.list_runs(
        owner_id=caller.user_id,
        workflow_definition_id=definition_id,
    )
    return WorkflowRunListResponse(runs=[to_run_response(item) for item in runs])


@router.get("/api/workflow-runs", response_model=WorkflowRunListResponse)
async def list_workflow_runs(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowRunListResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    runs = await workflow_manager.list_runs(owner_id=caller.user_id)
    return WorkflowRunListResponse(runs=[to_run_response(item) for item in runs])


@router.get(
    "/api/workflow-runs/{run_id}",
    response_model=WorkflowRunDetailResponse,
)
async def get_workflow_run(
    run_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowRunDetailResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    with_executions = await workflow_manager.get_run_with_executions(
        run_id, owner_id=caller.user_id
    )
    if with_executions is None:
        raise AppError(
            code="workflow_not_found",
            message=f"Workflow run {run_id} not found.",
            status_code=404,
        )
    run, executions = with_executions
    return to_run_detail_response(run, executions)


@router.post(
    "/api/workflow-runs/{run_id}/cancel",
    response_model=WorkflowRunResponse,
)
async def cancel_workflow_run(
    run_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowRunResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    run = await _run_workflow_operation(
        workflow_manager.cancel_run(run_id, owner_id=caller.user_id)
    )
    return to_run_response(run)


@router.post(
    "/api/workflow-runs/{run_id}/resume",
    response_model=WorkflowRunResponse,
)
async def resume_workflow_run(
    run_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowRunResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    run = await _run_workflow_operation(
        workflow_manager.resume(run_id, owner_id=caller.user_id)
    )
    return to_run_response(run)


@router.post(
    "/api/workflow-runs/{run_id}/nodes/{node_execution_id}/approve",
    response_model=WorkflowRunResponse,
)
async def approve_workflow_node(
    run_id: uuid.UUID,
    node_execution_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowRunResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    run = await _run_workflow_operation(
        workflow_manager.apply_decision(
            run_id,
            node_execution_id,
            owner_id=caller.user_id,
            decision=ApprovalDecision.APPROVED,
        )
    )
    return to_run_response(run)


@router.post(
    "/api/workflow-runs/{run_id}/nodes/{node_execution_id}/reject",
    response_model=WorkflowRunResponse,
)
async def reject_workflow_node(
    run_id: uuid.UUID,
    node_execution_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    workflow_manager: WorkflowManager = Depends(get_workflow_manager),
) -> WorkflowRunResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_workflow_enabled(settings)

    run = await _run_workflow_operation(
        workflow_manager.apply_decision(
            run_id,
            node_execution_id,
            owner_id=caller.user_id,
            decision=ApprovalDecision.REJECTED,
        )
    )
    return to_run_response(run)
