"""Chat endpoints with optional, flag-gated persistence.

When ``chat_persistence_enabled`` is off (or unset per environment), the
persistence dependencies resolve to ``None`` and the endpoints behave exactly
as the original stateless chat API. When on, each request resolves a caller,
opens a request-scoped session, and persists the chat lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deps import (
    get_agent_runtime,
    get_context_builder,
    get_prompt_manager,
    get_retriever,
    get_tool_executor,
    get_tool_registry,
)
from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.prompts.manager import PromptManager
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.retriever import Retriever
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.core.caller import CallerContext, get_current_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import bind_context, get_logger
from app.db.chat import SqlChatStore
from app.db.engine import get_sessionmaker
from app.db.identity import SqlGuestQuotaStore
from app.db.usage import SqlUsageStore
from app.schemas.chat import (
    ChatActivityFrame,
    ChatActivityPhase,
    ChatCompleteFrame,
    ChatRequestSchema,
    ChatResponseSchema,
    ChatSessionListItem,
    ChatSessionOut,
)
from app.services.chat_service import (
    ChatService,
    ChatServiceError,
    SessionNotFoundError,
)
from app.services.quota_service import QuotaService
from app.services.tool_chat_service import ToolChatService
from app.services.unified_chat_service import UnifiedChatService

router = APIRouter()
logger = get_logger(__name__)

CHAT_NDJSON_MEDIA = "application/x-ndjson"


def _wants_chat_progress(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return CHAT_NDJSON_MEDIA in accept


def _needs_unified_path(request: ChatRequestSchema) -> bool:
    return request.use_web_search or request.use_documents


def _effective_web_search(request: ChatRequestSchema, settings: Settings) -> bool:
    return request.use_web_search and settings.tools_enabled


def _effective_documents(request: ChatRequestSchema, settings: Settings) -> bool:
    return request.use_documents and settings.rag_enabled


def _unified_path_reports_progress(
    request: ChatRequestSchema, settings: Settings, http_request: Request
) -> bool:
    if not _wants_chat_progress(http_request):
        return False
    return _effective_web_search(request, settings) or _effective_documents(
        request, settings
    )


async def _stream_unified_chat_ndjson(
    *,
    request: ChatRequestSchema,
    caller: CallerContext | None,
    unified_service: UnifiedChatService,
) -> AsyncIterator[str]:
    """Yield activity frames during unified tool chat, then a terminal complete frame."""
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    async def on_activity(phase: str) -> None:
        await queue.put(("activity", phase))

    async def run_chat() -> None:
        try:
            result = await unified_service.execute(
                request, caller, on_activity=on_activity
            )
            await queue.put(("complete", result))
        except Exception as exc:
            await queue.put(("error", exc))

    task = asyncio.create_task(run_chat())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "activity":
                frame = ChatActivityFrame(phase=cast(ChatActivityPhase, payload))
                yield json.dumps(frame.model_dump()) + "\n"
            elif kind == "complete":
                frame = ChatCompleteFrame(response=cast(ChatResponseSchema, payload))
                yield json.dumps(frame.model_dump(mode="json")) + "\n"
                break
            elif kind == "error":
                raise payload  # type: ignore[misc]
    finally:
        await task


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
    prompt_manager: PromptManager = Depends(get_prompt_manager),
) -> ChatService:
    if session is None:
        return ChatService(settings, prompt_manager=prompt_manager)
    return ChatService(
        settings,
        chat_store=SqlChatStore(session),
        usage_store=SqlUsageStore(session),
        quota_service=QuotaService(
            store=SqlGuestQuotaStore(session), settings=settings
        ),
        session=session,
        prompt_manager=prompt_manager,
    )


def get_tool_chat_service(
    settings: Settings = Depends(get_settings),
    chat_service: ChatService = Depends(get_chat_service),
    registry: ToolRegistry = Depends(get_tool_registry),
    executor: ToolExecutor = Depends(get_tool_executor),
    prompt_manager: PromptManager = Depends(get_prompt_manager),
) -> ToolChatService:
    return ToolChatService(
        chat_service=chat_service,
        tool_executor=executor,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        settings=settings,
    )


def get_unified_chat_service(
    settings: Settings = Depends(get_settings),
    chat_service: ChatService = Depends(get_chat_service),
    tool_chat_service: ToolChatService = Depends(get_tool_chat_service),
    retriever: Retriever = Depends(get_retriever),
    context_builder: ContextBuilder = Depends(get_context_builder),
    prompt_manager: PromptManager = Depends(get_prompt_manager),
    agent: DefaultAgent = Depends(get_agent_runtime),
) -> UnifiedChatService:
    return UnifiedChatService(
        chat_service=chat_service,
        tool_chat_service=tool_chat_service,
        retriever=retriever,
        context_builder=context_builder,
        prompt_manager=prompt_manager,
        settings=settings,
        agent=agent,
    )


def _set_guest_token(response: Response, caller: CallerContext | None) -> None:
    if caller is not None and caller.issued_guest_token:
        response.headers["X-Guest-Token"] = caller.issued_guest_token


async def _set_guest_headers(
    response: Response, caller: CallerContext | None, service: ChatService
) -> None:
    """Set continuity + quota-visibility headers for guest callers (plan §3.1)."""
    _set_guest_token(response, caller)
    remaining = await service.guest_quota_remaining(caller)
    if remaining is not None:
        response.headers["X-Guest-Quota-Remaining"] = str(remaining)


@router.post("/api/chat", response_model=ChatResponseSchema)
async def create_chat(
    request: ChatRequestSchema,
    http_request: Request,
    response: Response,
    caller: CallerContext | None = Depends(get_optional_caller),
    settings: Settings = Depends(get_settings),
    service: ChatService = Depends(get_chat_service),
    unified_service: UnifiedChatService = Depends(get_unified_chat_service),
) -> ChatResponseSchema | StreamingResponse:
    if caller is not None and caller.user_id is not None:
        bind_context(user_id=str(caller.user_id))
    logger.info("Chat request accepted", route="/api/chat", method="POST")

    if _needs_unified_path(request):
        if _unified_path_reports_progress(request, settings, http_request):
            stream_response = StreamingResponse(
                _stream_unified_chat_ndjson(
                    request=request,
                    caller=caller,
                    unified_service=unified_service,
                ),
                media_type=CHAT_NDJSON_MEDIA,
            )
            await _set_guest_headers(stream_response, caller, service)
            return stream_response

        result = await unified_service.execute(request, caller)
        await _set_guest_headers(response, caller, service)
        return result

    result = await service.complete_chat(request, caller)
    await _set_guest_headers(response, caller, service)
    return result


@router.post("/api/chat/stream")
async def create_chat_stream(
    request: ChatRequestSchema,
    http_request: Request,
    caller: CallerContext | None = Depends(get_optional_caller),
    settings: Settings = Depends(get_settings),
    service: ChatService = Depends(get_chat_service),
    unified_service: UnifiedChatService = Depends(get_unified_chat_service),
) -> StreamingResponse:
    if not settings.chat_streaming_enabled:
        raise AppError(
            code="feature_disabled",
            message="Chat streaming is not enabled on this server.",
            status_code=503,
        )

    if caller is not None and caller.user_id is not None:
        bind_context(user_id=str(caller.user_id))
    logger.info("Chat stream accepted", route="/api/chat/stream", method="POST")

    try:
        if _effective_web_search(request, settings):
            unified_service.validate_stream_web_search(request, caller)
    except ChatServiceError as exc:
        raise AppError(
            code=exc.code, message=exc.message, status_code=exc.status_code
        ) from exc

    # Pre-flight (quota/session/user-append) runs before streaming so quota or
    # ownership failures surface as normal HTTP errors.
    prep = await service.prepare_stream(request, caller)

    if _effective_web_search(request, settings) or _effective_documents(
        request, settings
    ):
        stream = unified_service.stream_execute(request, http_request, caller, prep)
    else:
        stream = service.stream_chat(request, http_request, caller, prep)

    response = StreamingResponse(stream, media_type="text/event-stream")
    await _set_guest_headers(response, caller, service)
    return response


@router.get("/api/chat/sessions", response_model=list[ChatSessionListItem])
async def list_chat_sessions(
    response: Response,
    caller: CallerContext | None = Depends(get_optional_caller),
    service: ChatService = Depends(get_chat_service),
) -> list[ChatSessionListItem]:
    # No caller (persistence disabled) means no session concept at all: an
    # empty list, not an error (plan Section 2.7).
    if caller is None:
        return []
    result = await service.list_sessions(caller)
    _set_guest_token(response, caller)
    return result


@router.post("/api/chat/sessions", response_model=ChatSessionOut, status_code=201)
async def create_chat_session(
    response: Response,
    caller: CallerContext | None = Depends(get_optional_caller),
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionOut:
    if caller is None:
        raise SessionNotFoundError()
    result = await service.create_session(caller)
    _set_guest_token(response, caller)
    return result


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


@router.delete("/api/chat/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: uuid.UUID,
    response: Response,
    caller: CallerContext | None = Depends(get_optional_caller),
    service: ChatService = Depends(get_chat_service),
) -> Response:
    if caller is None:
        raise SessionNotFoundError()
    await service.delete_session(session_id, caller)
    _set_guest_token(response, caller)
    return Response(status_code=204)
