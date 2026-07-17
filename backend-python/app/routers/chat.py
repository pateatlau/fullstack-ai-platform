"""Chat endpoints with optional, flag-gated persistence.

When ``chat_persistence_enabled`` is off (or unset per environment), the
persistence dependencies resolve to ``None`` and the endpoints behave exactly
as the original stateless chat API. When on, each request resolves a caller,
opens a request-scoped session, and persists the chat lifecycle.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.caller import CallerContext, get_current_caller
from app.core.config import Settings, get_settings
from app.db.chat import SqlChatStore
from app.db.engine import get_sessionmaker
from app.db.identity import SqlGuestQuotaStore
from app.db.usage import SqlUsageStore
from app.schemas.chat import ChatRequestSchema, ChatResponseSchema, ChatSessionOut
from app.services.chat_service import ChatService, SessionNotFoundError
from app.services.quota_service import QuotaService

router = APIRouter()


async def get_optional_session(
    settings: Settings = Depends(get_settings),
) -> AsyncIterator[AsyncSession | None]:
    """Yield a request-scoped session, or ``None`` when persistence is disabled."""
    if not settings.chat_persistence_enabled:
        yield None
        return
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_optional_caller(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession | None = Depends(get_optional_session),
) -> CallerContext | None:
    if session is None:
        return None
    return await get_current_caller(request, settings, session)


def get_chat_service(
    settings: Settings = Depends(get_settings),
    session: AsyncSession | None = Depends(get_optional_session),
) -> ChatService:
    if session is None:
        return ChatService(settings)
    return ChatService(
        settings,
        chat_store=SqlChatStore(session),
        usage_store=SqlUsageStore(session),
        quota_service=QuotaService(
            store=SqlGuestQuotaStore(session), settings=settings
        ),
        session=session,
    )


def _set_guest_token(response: Response, caller: CallerContext | None) -> None:
    if caller is not None and caller.issued_guest_token:
        response.headers["X-Guest-Token"] = caller.issued_guest_token


@router.post("/api/chat", response_model=ChatResponseSchema)
async def create_chat(
    request: ChatRequestSchema,
    response: Response,
    caller: CallerContext | None = Depends(get_optional_caller),
    service: ChatService = Depends(get_chat_service),
) -> ChatResponseSchema:
    result = await service.complete_chat(request, caller)
    _set_guest_token(response, caller)
    return result


@router.post("/api/chat/stream")
async def create_chat_stream(
    request: ChatRequestSchema,
    http_request: Request,
    caller: CallerContext | None = Depends(get_optional_caller),
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    # Pre-flight (quota/session/user-append) runs before streaming so quota or
    # ownership failures surface as normal HTTP errors.
    prep = await service.prepare_stream(request, caller)
    response = StreamingResponse(
        service.stream_chat(request, http_request, caller, prep),
        media_type="text/event-stream",
    )
    _set_guest_token(response, caller)
    return response


@router.get("/api/chat/sessions/{session_id}", response_model=ChatSessionOut)
async def get_chat_session(
    session_id: uuid.UUID,
    response: Response,
    caller: CallerContext | None = Depends(get_optional_caller),
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionOut:
    if caller is None:
        raise SessionNotFoundError()
    result = await service.get_session_transcript(session_id, caller)
    _set_guest_token(response, caller)
    return result
