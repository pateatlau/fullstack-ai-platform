"""Agent tool-call approval REST API (Epic 09 Phase 3)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.ai.agent.adapters.chat_adapter import _chat_agent_system_prompt
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEventType
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.agent.streaming.adapter import sse_frame_from_agent_event
from app.ai.agent.streaming.publisher import QueueStreamPublisher
from app.ai.deps import (
    get_agent_approval_service,
    get_agent_runtime,
    get_approvals_store,
)
from app.ai.hitl.exceptions import (
    ApprovalDecisionConflictError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalValidationError,
    HitlError,
)
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalKind,
    ApprovalStatus,
    RequestMetadata,
)
from app.ai.hitl.request_metadata import build_request_metadata
from app.ai.hitl.service import AgentApprovalService, normalize_hitl_reason
from app.ai.hitl.store import ApprovalsStore
from app.ai.tools.schemas import ToolExecutionContext
from app.core.caller import CallerContext, require_authenticated_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import bind_context
from app.db.models import ChatMessage
from app.middleware.correlation_id import get_request_id
from app.schemas.approvals import (
    ApprovalAuditEntryResponse,
    ApprovalAuditListResponse,
    ApprovalCancelRequest,
    ApprovalDecideRequest,
    ApprovalResultResponse,
    ApprovalRevisionResponse,
    ApprovalReviseRequest,
    DEFAULT_APPROVALS_LIST_LIMIT,
    MAX_APPROVALS_LIST_LIMIT,
)
from app.schemas.chat import ErrorFrame
from app.services.chat_service import format_sse

router = APIRouter()


def _require_hitl_enabled(settings: Settings) -> None:
    if not settings.hitl_enabled:
        raise AppError(
            code="feature_disabled",
            message="Human-in-the-loop approvals are not enabled on this server.",
            status_code=503,
        )


def _request_metadata_from_request(
    request: Request, settings: Settings
) -> RequestMetadata:
    return build_request_metadata(request, settings)


def _raise_hitl_error(exc: HitlError) -> NoReturn:
    if isinstance(exc, ApprovalNotFoundError):
        raise AppError(
            code="approval_not_found",
            message=str(exc),
            status_code=404,
        ) from exc
    if isinstance(exc, ApprovalExpiredError):
        raise AppError(
            code="approval_expired",
            message=str(exc),
            status_code=409,
        ) from exc
    if isinstance(exc, ApprovalDecisionConflictError):
        raise AppError(
            code="approval_decision_conflict",
            message=str(exc),
            status_code=409,
        ) from exc
    if isinstance(exc, ApprovalValidationError):
        raise AppError(
            code="validation_error",
            message=str(exc),
            status_code=422,
        ) from exc
    raise AppError(
        code="hitl_error",
        message=str(exc),
        status_code=500,
    ) from exc


def _sse_error_from_hitl(exc: HitlError, *, response_id: str) -> str:
    if isinstance(exc, ApprovalDecisionConflictError):
        code = "approval_decision_conflict"
    elif isinstance(exc, ApprovalExpiredError):
        code = "approval_expired"
    elif isinstance(exc, ApprovalNotFoundError):
        code = "approval_not_found"
    elif isinstance(exc, ApprovalValidationError):
        code = "validation_error"
    else:
        code = "hitl_error"
    return format_sse(
        "error",
        ErrorFrame(id=response_id, code=code, message=str(exc)),
    )


def _build_resume_request(
    *,
    model: str | None,
    provider: str | None,
    settings: Settings,
) -> AgentRequest:
    return AgentRequest(
        messages=[AgentMessage(role="user", content=".")],
        model=model or "gpt-4o-mini",
        provider=provider,
        system_prompt=_chat_agent_system_prompt(),
        config=AgentConfig(
            max_iterations=3,
            timeout_seconds=settings.request_timeout_seconds,
        ),
    )


@router.get("/api/approvals", response_model=ApprovalAuditListResponse)
async def list_approvals(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    approvals_store: ApprovalsStore = Depends(get_approvals_store),
    status: Literal["pending", "approved", "rejected", "expired", "cancelled"]
    | None = Query(default=None),
    kind: Literal["agent_tool", "workflow_node"] | None = Query(default=None),
    limit: int = Query(
        default=DEFAULT_APPROVALS_LIST_LIMIT,
        ge=1,
        le=MAX_APPROVALS_LIST_LIMIT,
    ),
    offset: int = Query(default=0, ge=0),
) -> ApprovalAuditListResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_hitl_enabled(settings)

    parsed_status = ApprovalStatus(status) if status is not None else None
    parsed_kind = ApprovalKind(kind) if kind is not None else None
    entries, total = await approvals_store.list_for_owner(
        caller.user_id,
        status=parsed_status,
        kind=parsed_kind,
        limit=limit,
        offset=offset,
    )
    return ApprovalAuditListResponse(
        approvals=[ApprovalAuditEntryResponse.from_domain(item) for item in entries],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/api/approvals/{approval_id}", response_model=ApprovalAuditEntryResponse)
async def get_approval(
    approval_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    approvals_store: ApprovalsStore = Depends(get_approvals_store),
) -> ApprovalAuditEntryResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_hitl_enabled(settings)

    entry = await approvals_store.get_for_owner(approval_id, owner_id=caller.user_id)
    if entry is None:
        raise AppError(
            code="approval_not_found",
            message=f"Approval {approval_id} not found or not owned by caller.",
            status_code=404,
        )
    return ApprovalAuditEntryResponse.from_domain(entry)


@router.post(
    "/api/approvals/{approval_id}/revise",
    response_model=ApprovalRevisionResponse,
)
async def revise_agent_approval(
    approval_id: uuid.UUID,
    body: ApprovalReviseRequest,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    approval_service: AgentApprovalService = Depends(get_agent_approval_service),
) -> ApprovalRevisionResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_hitl_enabled(settings)

    try:
        _, revision = await approval_service.revise(
            approval_id,
            edited_calls=body.edited_calls,
            owner_id=caller.user_id,
            note=body.note,
        )
    except HitlError as exc:
        _raise_hitl_error(exc)

    return ApprovalRevisionResponse.from_domain(revision)


@router.get(
    "/api/approvals/{approval_id}/revisions",
    response_model=list[ApprovalRevisionResponse],
)
async def list_approval_revisions(
    approval_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    approvals_store: ApprovalsStore = Depends(get_approvals_store),
) -> list[ApprovalRevisionResponse]:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_hitl_enabled(settings)

    try:
        revisions = await approvals_store.list_revisions_for_owner(
            approval_id,
            owner_id=caller.user_id,
        )
    except HitlError as exc:
        _raise_hitl_error(exc)

    return [ApprovalRevisionResponse.from_domain(item) for item in revisions]


@router.post("/api/approvals/{approval_id}/decide")
async def decide_agent_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecideRequest,
    http_request: Request,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    approval_service: AgentApprovalService = Depends(get_agent_approval_service),
    agent: DefaultAgent = Depends(get_agent_runtime),
):
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_hitl_enabled(settings)

    reason = normalize_hitl_reason(
        body.reason, max_length=settings.hitl_max_reason_length
    )
    comments = normalize_hitl_reason(
        body.comments, max_length=settings.hitl_max_comment_length
    )
    request_metadata = _request_metadata_from_request(http_request, settings)

    if body.decision == "rejected":
        try:
            result = await approval_service.decide(
                approval_id,
                owner_id=caller.user_id,
                decision="rejected",
                reason=reason,
                comments=comments,
                request_metadata=request_metadata,
            )
        except HitlError as exc:
            _raise_hitl_error(exc)
        return ApprovalResultResponse.from_domain(result)

    try:
        approval = await approval_service.get_owned_approval(
            approval_id,
            owner_id=caller.user_id,
        )
    except HitlError as exc:
        _raise_hitl_error(exc)

    if approval.status != ApprovalStatus.PENDING:
        raise AppError(
            code="approval_decision_conflict",
            message=(
                f"Approval {approval_id} is no longer pending "
                f"(status={approval.status.value})."
            ),
            status_code=409,
        )

    # Multi-stage checklist (recommendation #5): only the *final* outstanding
    # stage executes tools via the SSE resume flow below. Earlier stages just
    # record a checklist entry and stay pending.
    if _remaining_stage_count(approval) > 1:
        try:
            result = await approval_service.record_stage_approval(
                approval_id,
                owner_id=caller.user_id,
                reason=reason,
                comments=comments,
            )
        except HitlError as exc:
            _raise_hitl_error(exc)
        return ApprovalResultResponse.from_domain(result)

    placeholder = await approval_service.get_placeholder_message(approval)

    return StreamingResponse(
        _stream_approved_decision(
            approval_id=approval_id,
            body=body,
            reason=reason,
            comments=comments,
            request_metadata=request_metadata,
            caller=caller,
            settings=settings,
            approval_service=approval_service,
            agent=agent,
            approval=approval,
            placeholder=placeholder,
        ),
        media_type="text/event-stream",
    )


def _remaining_stage_count(approval: AgentToolApproval) -> int:
    return len(approval.required_stages) - len(approval.stage_decisions)


@router.post(
    "/api/approvals/{approval_id}/cancel",
    response_model=ApprovalResultResponse,
)
async def cancel_agent_approval(
    approval_id: uuid.UUID,
    body: ApprovalCancelRequest,
    http_request: Request,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    approval_service: AgentApprovalService = Depends(get_agent_approval_service),
) -> ApprovalResultResponse:
    """Requester-initiated withdrawal of a still-pending approval (recommendation #2)."""
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_hitl_enabled(settings)

    reason = normalize_hitl_reason(
        body.reason, max_length=settings.hitl_max_reason_length
    )
    try:
        result = await approval_service.cancel(
            approval_id,
            owner_id=caller.user_id,
            reason=reason,
            request_metadata=_request_metadata_from_request(http_request, settings),
        )
    except HitlError as exc:
        _raise_hitl_error(exc)
    return ApprovalResultResponse.from_domain(result)


async def _stream_approved_decision(
    *,
    approval_id: uuid.UUID,
    body: ApprovalDecideRequest,
    reason: str | None,
    comments: str | None,
    request_metadata: RequestMetadata,
    caller: CallerContext,
    settings: Settings,
    approval_service: AgentApprovalService,
    agent: DefaultAgent,
    approval: AgentToolApproval,
    placeholder: ChatMessage | None,
) -> AsyncIterator[str]:
    assert caller.user_id is not None
    owner_id = caller.user_id

    request = _build_resume_request(
        model=placeholder.model if placeholder is not None else None,
        provider=placeholder.provider if placeholder is not None else None,
        settings=settings,
    )
    context = AgentContext(
        execution_id=approval.execution_id,
        request_id=get_request_id(),
        caller=caller,
        session_id=approval.session_id,
    )
    tool_context = ToolExecutionContext(
        caller=caller,
        request_id=context.request_id,
        session_id=approval.session_id,
    )

    publisher = QueueStreamPublisher()
    executor = agent.create_streaming_executor(request, publisher)
    response_id = str(approval_id)

    async def _run_approval_resume():
        try:
            return await approval_service.approve_and_resume(
                approval_id,
                owner_id=owner_id,
                executor=executor,
                request=request,
                context=context,
                tool_context=tool_context,
                stream_publisher=publisher,
                edited_calls=body.edited_calls,
                reason=reason,
                comments=comments,
                request_metadata=request_metadata,
            )
        finally:
            await publisher.close()

    task = asyncio.create_task(_run_approval_resume())

    try:
        while True:
            event = await publisher.queue.get()
            if event is None:
                break
            if event.type in {
                AgentStreamEventType.START,
                AgentStreamEventType.PLANNING,
                AgentStreamEventType.REFLECTION,
            }:
                continue
            mapped = sse_frame_from_agent_event(event, response_id=response_id)
            if mapped is not None:
                event_name, frame = mapped
                yield format_sse(event_name, frame)
        try:
            await task
        except HitlError as exc:
            yield _sse_error_from_hitl(exc, response_id=response_id)
    finally:
        await publisher.close()
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
