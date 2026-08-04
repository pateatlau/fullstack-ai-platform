"""Authenticated Memory management REST API (Epic 05 Phase 7)."""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Depends, Query

from app.ai.deps import get_conversation_summary_service, get_memory_manager
from app.ai.memory.exceptions import MemoryAccessDeniedError, MemoryNotFoundError
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.manager import MemoryManager
from app.ai.memory.models import MemoryType, UserPreferenceUpsert
from app.ai.memory.project import validate_project_id
from app.ai.memory.summarizer import ConversationSummaryService
from app.core.caller import CallerContext, require_authenticated_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import bind_context, get_logger
from app.schemas.memory import (
    MemoryRecordListResponse,
    MemoryRecordResponse,
    UserPreferenceItem,
    UserPreferenceListResponse,
)

router = APIRouter()
logger = get_logger(__name__)


def _require_memory_enabled(settings: Settings) -> None:
    if not settings.memory_enabled:
        raise AppError(
            code="feature_disabled",
            message="Memory is not enabled on this server.",
            status_code=503,
        )


def _to_record_response(record) -> MemoryRecordResponse:  # noqa: ANN001
    return MemoryRecordResponse(
        id=record.id,
        title=record.title,
        content=record.content,
        memory_type=record.memory_type,
        session_id=record.project_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _require_project_session_id(session_id: uuid.UUID | None) -> uuid.UUID:
    """Reject missing or nil project session identifiers with 422."""
    if session_id is None:
        raise AppError(
            code="validation_error",
            message="session_id is required when memory_type is 'project'.",
            status_code=422,
        )
    try:
        return validate_project_id(session_id)
    except ValueError as exc:
        raise AppError(
            code="validation_error",
            message=str(exc),
            status_code=422,
        ) from exc


@router.get("/api/memory/records", response_model=MemoryRecordListResponse)
async def list_memory_records(
    memory_type: MemoryType = Query(...),
    session_id: uuid.UUID | None = Query(default=None),
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    memory_manager: MemoryManager = Depends(get_memory_manager),
) -> MemoryRecordListResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_memory_enabled(settings)

    validated_session_id = session_id
    if memory_type is MemoryType.PROJECT:
        validated_session_id = _require_project_session_id(session_id)

    records = await memory_manager.list_records(
        owner_id=caller.user_id,
        memory_type=memory_type,
        session_id=validated_session_id,
    )
    return MemoryRecordListResponse(
        records=[_to_record_response(record) for record in records]
    )


@router.get("/api/memory/records/{record_id}", response_model=MemoryRecordResponse)
async def get_memory_record(
    record_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    memory_manager: MemoryManager = Depends(get_memory_manager),
) -> MemoryRecordResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_memory_enabled(settings)

    record = await memory_manager.get_record(record_id, owner_id=caller.user_id)
    if record is None or record.lifecycle_state is LifecycleState.DELETED:
        raise AppError(
            code="memory_not_found",
            message=f"Memory record {record_id} not found.",
            status_code=404,
        )
    return _to_record_response(record)


@router.delete("/api/memory/records/{record_id}", status_code=204)
async def delete_memory_record(
    record_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    memory_manager: MemoryManager = Depends(get_memory_manager),
) -> None:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_memory_enabled(settings)

    try:
        await memory_manager.delete_record(record_id, owner_id=caller.user_id)
    except MemoryNotFoundError as exc:
        raise AppError(
            code="memory_not_found",
            message=str(exc),
            status_code=404,
        ) from exc


@router.get("/api/memory/preferences", response_model=UserPreferenceListResponse)
async def list_preferences(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    memory_manager: MemoryManager = Depends(get_memory_manager),
) -> UserPreferenceListResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_memory_enabled(settings)

    preferences = await memory_manager.list_preferences(user_id=caller.user_id)
    return UserPreferenceListResponse(
        preferences=[
            UserPreferenceItem(key=key, value=cast(dict[str, object], value))
            for key, value in sorted(preferences.items())
        ]
    )


@router.put("/api/memory/preferences/{key}", response_model=UserPreferenceItem)
async def upsert_preference(
    key: str,
    body: UserPreferenceUpsert,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    memory_manager: MemoryManager = Depends(get_memory_manager),
) -> UserPreferenceItem:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_memory_enabled(settings)

    await memory_manager.set_preference(
        user_id=caller.user_id,
        key=key,
        value=body.value,
    )
    return UserPreferenceItem(key=key, value=body.value)


@router.delete("/api/memory/preferences/{key}", status_code=204)
async def delete_preference(
    key: str,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    memory_manager: MemoryManager = Depends(get_memory_manager),
) -> None:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_memory_enabled(settings)

    await memory_manager.delete_preference(user_id=caller.user_id, key=key)


@router.delete("/api/memory/sessions/{session_id}/summary", status_code=204)
async def clear_session_summary(
    session_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    memory_manager: MemoryManager = Depends(get_memory_manager),
    summary_service: ConversationSummaryService = Depends(
        get_conversation_summary_service
    ),
) -> None:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))
    _require_memory_enabled(settings)

    try:
        await memory_manager.clear_session_summary(
            session_id=session_id,
            owner_id=caller.user_id,
            summary_service=summary_service,
        )
    except MemoryAccessDeniedError as exc:
        raise AppError(
            code="memory_access_denied",
            message=str(exc),
            status_code=403,
        ) from exc
