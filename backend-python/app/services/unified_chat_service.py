"""Unified chat orchestration (V1.1b non-streaming, V1.1c streaming tools + RAG).

Composes ``ChatService``, ``ToolChatService``, and RAG retrieval components
without adding domain logic to ``app/ai/rag/`` framework modules.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

from fastapi import Request
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.ai.agent.adapters.chat_adapter import ChatAgentAdapter
from app.ai.agent.adapters.chat_stream_adapter import stream_agent_chat
from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.prompts.manager import PromptManager
from app.ai.rag.citations import to_citation_schemas
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.pipeline import AdvancedRetrievalPipeline
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import RetrievalRequest, RetrievalResult
from app.ai.rag.service import EMPTY_CORPUS_MESSAGE
from app.ai.tools.implementations.web_search import WEB_SEARCH_TOOL_NAME
from app.core.caller import CallerContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import (
    ChatMessageInput,
    LLMProvider,
    ProviderChunk,
    ProviderToolCall,
)
from app.providers.capabilities import get_capabilities
from app.schemas.chat import (
    ChatMessageSchema,
    ChatRequestSchema,
    ChatResponseSchema,
    CitationSchema,
    DeltaFrame,
    EndFrame,
    ErrorFrame,
    ProviderName,
    RetrievedChunkMetaSchema,
    RetrievalCompleteFrame,
    StartFrame,
    ToolEndFrame,
    ToolStartFrame,
)
from app.services.chat_service import (
    ChatService,
    ChatServiceError,
    ClosableAsyncIterator,
    DbUnavailableError,
    EmptyProviderResponseError,
    _StreamPrep,
    format_sse,
    normalize_chat_error,
)
from app.services.max_tokens import resolve_max_tokens
from app.services.tool_chat_service import (
    ChatActivityCallback,
    ToolChatService,
    _GUEST_TOOL_DENIED_MESSAGE,
    _TOOL_ITERATION_LIMIT_MESSAGE,
    _assistant_tool_call_message,
)

logger = get_logger(__name__)


class UnifiedChatService:
    """Canonical chat orchestrator for unified toggles (stream + non-stream)."""

    def __init__(
        self,
        *,
        chat_service: ChatService,
        tool_chat_service: ToolChatService,
        retriever: Retriever,
        context_builder: ContextBuilder,
        prompt_manager: PromptManager,
        settings: Settings,
        agent: DefaultAgent | None = None,
        advanced_pipeline: AdvancedRetrievalPipeline | None = None,
    ) -> None:
        self._chat_service = chat_service
        self._tool_chat_service = tool_chat_service
        self._retriever = retriever
        self._context_builder = context_builder
        self._prompt_manager = prompt_manager
        self._settings = settings
        self._agent = agent
        self._advanced_pipeline = advanced_pipeline
        self._chat_agent_adapter = (
            ChatAgentAdapter(
                agent=agent,
                chat_service=chat_service,
                settings=settings,
            )
            if agent is not None
            else None
        )

    def validate_stream_web_search(
        self,
        request: ChatRequestSchema,
        caller: CallerContext | None,
    ) -> None:
        """Pre-stream validation for unified streaming + web search."""
        del caller
        if not (request.use_web_search and self._settings.tools_enabled):
            return

        provider_name = self._resolve_provider_name(request)
        if not get_capabilities(provider_name).supports_tool_calling:
            raise ChatServiceError(
                code="validation_error",
                message=(
                    f"Tool calling is not supported for provider '{provider_name}'."
                ),
                status_code=422,
            )

    async def execute(
        self,
        request: ChatRequestSchema,
        caller: CallerContext | None = None,
        on_activity: ChatActivityCallback | None = None,
    ) -> ChatResponseSchema:
        effective_web_search = request.use_web_search and self._settings.tools_enabled
        effective_documents = request.use_documents and self._settings.rag_enabled

        if (
            effective_web_search or effective_documents
        ) and self._is_guest_or_anonymous(caller):
            return await self._guest_denial_response(request, caller)

        provider, model, provider_name = self._chat_service._resolve_provider(request)

        if (
            effective_web_search
            and not get_capabilities(provider_name).supports_tool_calling
        ):
            raise ChatServiceError(
                code="validation_error",
                message=(
                    f"Tool calling is not supported for provider '{provider_name}'."
                ),
                status_code=422,
            )

        working_request = request
        retrieved_chunks: list[RetrievedChunkMetaSchema] | None = None
        # Additive citations: populated on advanced path; V1 leaves ``None``.
        citations: list[CitationSchema] | None = None

        if effective_documents:
            assert caller is not None and caller.user_id is not None
            question = self._chat_service._last_user_content(request)
            if on_activity is not None:
                await on_activity("document_retrieval")
            try:
                doc_result = await self._retrieve_document_context(
                    question=question,
                    user_id=caller.user_id,
                )
            finally:
                if on_activity is not None:
                    await on_activity("thinking")
            if doc_result.empty:
                return await self._empty_corpus_response(
                    request=request,
                    caller=caller,
                    model=model,
                    provider_name=provider_name,
                    citations=doc_result.citations,
                )

            retrieved_chunks = doc_result.retrieved_chunks
            citations = doc_result.citations
            working_request = self._merge_document_context(
                request=request,
                question=question,
                context_text=doc_result.context_text,
            )

        if effective_web_search:
            # ToolChatService/ChatAgentAdapter bypass ChatService.complete_chat's
            # own message resolution, so Memory must be applied here explicitly
            # (Part I: orchestrated only from ChatService/UnifiedChatService).
            working_request = await self._apply_memory_context(
                working_request, caller, session_id=working_request.session_id
            )
            if self._use_agent_runtime():
                assert self._chat_agent_adapter is not None
                response = await self._chat_agent_adapter.complete_chat(
                    working_request,
                    caller,
                    on_activity=on_activity,
                    allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
                )
            else:
                response = await self._tool_chat_service.complete_chat(
                    working_request,
                    caller,
                    on_activity=on_activity,
                    allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
                )
            self._chat_service._maybe_extract_memory(
                caller=caller,
                session_id=response.session_id,
                provider=provider,
                provider_name=provider_name,
                model=model,
                user_content=self._chat_service._last_user_content(request),
                assistant_content=response.content,
            )
        else:
            # Memory + conversation-summary substitution both happen inside
            # complete_chat; bypass the latter when documents were merged so
            # the ephemeral RAG context (never persisted) isn't discarded by
            # the DB-reconstructed message history.
            response = await self._chat_service.complete_chat(
                working_request,
                caller,
                bypass_summary_reconstruction=effective_documents,
            )

        response_updates: dict[str, object] = {}
        if retrieved_chunks is not None:
            response_updates["retrieved_chunks"] = retrieved_chunks
        if citations is not None:
            response_updates["citations"] = citations
        if response_updates:
            response = response.model_copy(update=response_updates)
        return response

    async def stream_execute(
        self,
        request: ChatRequestSchema,
        http_request: Request,
        caller: CallerContext | None = None,
        prep: _StreamPrep | None = None,
    ) -> AsyncIterator[str]:
        """SSE generator for streaming chat with document grounding and/or web search."""
        effective_web_search = request.use_web_search and self._settings.tools_enabled
        effective_documents = request.use_documents and self._settings.rag_enabled

        provider, model, provider_name = self._chat_service._resolve_provider(request)
        response_id = f"resp_{uuid.uuid4().hex[:12]}"
        session_id = prep.session_id if prep is not None else None
        request_start_time = time.perf_counter()

        if prep is not None and prep.idempotent_reply is not None:
            yield format_sse("start", StartFrame(id=response_id, session_id=session_id))
            if prep.idempotent_reply:
                yield format_sse(
                    "delta", DeltaFrame(id=response_id, content=prep.idempotent_reply)
                )
            yield format_sse(
                "end",
                EndFrame(
                    id=response_id, finish_reason=prep.idempotent_finish or "stop"
                ),
            )
            return

        if self._is_guest_or_anonymous(caller):
            async for frame in self._stream_guest_denial(
                request=request,
                caller=caller,
                response_id=response_id,
                session_id=session_id,
                prep=prep,
                provider=provider,
                provider_name=provider_name,
                model=model,
            ):
                yield frame
            return

        # stream_execute never delegates to ChatService.stream_chat (unlike the
        # plain SSE route), so Memory must be applied here for every branch
        # below (plain, RAG, tool-loop, and agent streaming all read from
        # ``working_request``).
        working_request = await self._apply_memory_context(
            request, caller, session_id=session_id
        )
        retrieval_latency_ms: int | None = None

        try:
            if effective_documents:
                assert caller is not None and caller.user_id is not None
                question = self._chat_service._last_user_content(request)
                retrieval_start = time.perf_counter()
                try:
                    doc_result = await self._retrieve_document_context(
                        question=question,
                        user_id=caller.user_id,
                    )
                except Exception:
                    logger.exception(
                        "Document retrieval failed during unified stream",
                        response_id=response_id,
                    )
                    try:
                        await self._chat_service._persist_stream_result(
                            caller=caller,
                            prep=prep,
                            provider=provider,
                            provider_name=provider_name,
                            model=model,
                            content="",
                            finish_reason=None,
                            status="error",
                        )
                    except Exception:  # noqa: BLE001 - best-effort error persistence
                        logger.exception(
                            "Failed to persist unified stream retrieval error state",
                            response_id=response_id,
                        )
                    yield format_sse(
                        "start", StartFrame(id=response_id, session_id=session_id)
                    )
                    yield format_sse(
                        "error",
                        ErrorFrame(
                            id=response_id,
                            code="retrieval_error",
                            message=("Could not retrieve documents. Please try again."),
                        ),
                    )
                    return

                retrieval_latency_ms = (
                    doc_result.retrieval_latency_ms
                    if doc_result.retrieval_latency_ms is not None
                    else int((time.perf_counter() - retrieval_start) * 1000)
                )
                citation_count = (
                    len(doc_result.citations) if doc_result.citations is not None else 0
                )
                logger.info(
                    "Unified stream document retrieval completed",
                    response_id=response_id,
                    retrieval_latency_ms=retrieval_latency_ms,
                    chunk_count=len(doc_result.retrieved_chunks),
                    citation_count=citation_count,
                    advanced_rag_enabled=self._use_advanced_pipeline(),
                )

                if doc_result.empty:
                    async for frame in self._stream_static_content(
                        response_id=response_id,
                        session_id=session_id,
                        content=EMPTY_CORPUS_MESSAGE,
                        finish_reason="stop",
                        caller=caller,
                        prep=prep,
                        provider=provider,
                        provider_name=provider_name,
                        model=model,
                    ):
                        yield frame
                    return

                working_request = self._merge_document_context(
                    request=working_request,
                    question=question,
                    context_text=doc_result.context_text,
                )
                yield format_sse(
                    "retrieval_complete",
                    RetrievalCompleteFrame(
                        id=response_id,
                        chunk_count=len(doc_result.retrieved_chunks),
                        citation_count=citation_count,
                    ),
                )

            if effective_web_search and self._use_agent_runtime():
                assert self._agent is not None
                async for frame in stream_agent_chat(
                    agent=self._agent,
                    chat_service=self._chat_service,
                    settings=self._settings,
                    request=working_request,
                    http_request=http_request,
                    caller=caller,
                    prep=prep,
                    response_id=response_id,
                    session_id=session_id,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
                    request_start_time=request_start_time,
                    retrieval_latency_ms=retrieval_latency_ms,
                ):
                    yield frame
            else:
                stream_messages: list[ChatMessageInput] = list(working_request.messages)
                tool_rounds = 0

                if effective_web_search:
                    loop_result = await self._run_stream_tool_loop(
                        provider=provider,
                        request=working_request,
                        model=model,
                        provider_name=provider_name,
                        caller=caller,
                        response_id=response_id,
                        http_request=http_request,
                    )
                    for frame in loop_result.frames:
                        yield frame

                    if loop_result.guest_denied:
                        async for frame in self._stream_static_content(
                            response_id=response_id,
                            session_id=session_id,
                            content=_GUEST_TOOL_DENIED_MESSAGE,
                            finish_reason="stop",
                            caller=caller,
                            prep=prep,
                            provider=provider,
                            provider_name=provider_name,
                            model=model,
                        ):
                            yield frame
                        return

                    if loop_result.iteration_limit_content is not None:
                        async for frame in self._stream_static_content(
                            response_id=response_id,
                            session_id=session_id,
                            content=loop_result.iteration_limit_content,
                            finish_reason="tool_iteration_cap",
                            caller=caller,
                            prep=prep,
                            provider=provider,
                            provider_name=provider_name,
                            model=model,
                        ):
                            yield frame
                        return

                    tool_rounds = loop_result.tool_rounds
                    if tool_rounds > 0:
                        stream_messages = loop_result.loop_messages

                async for frame in self._stream_provider_answer(
                    provider=provider,
                    messages=cast(list[ChatMessageSchema], stream_messages),
                    model=model,
                    provider_name=provider_name,
                    temperature=working_request.temperature,
                    response_id=response_id,
                    session_id=session_id,
                    caller=caller,
                    prep=prep,
                    http_request=http_request,
                    tool_rounds=tool_rounds,
                    request_start_time=request_start_time,
                    retrieval_latency_ms=retrieval_latency_ms,
                ):
                    yield frame
        except ChatServiceError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            app_error = normalize_chat_error(exc)
            logger.exception(
                "Unified stream failed",
                response_id=response_id,
                provider=provider_name,
                model=model,
            )
            yield format_sse("start", StartFrame(id=response_id, session_id=session_id))
            yield format_sse(
                "error",
                ErrorFrame(
                    id=response_id,
                    code=app_error.code,
                    message=app_error.message,
                ),
            )

    async def _run_stream_tool_loop(
        self,
        *,
        provider: LLMProvider,
        request: ChatRequestSchema,
        model: str,
        provider_name: ProviderName,
        caller: CallerContext | None,
        response_id: str,
        http_request: Request,
    ) -> _StreamToolLoopResult:
        tools = self._tool_chat_service._tool_registry.get_schemas_for_llm()
        tools = [
            schema
            for schema in tools
            if schema.get("function", {}).get("name") == WEB_SEARCH_TOOL_NAME
        ]
        if not tools:
            return _StreamToolLoopResult(
                frames=[],
                loop_messages=list(request.messages),
                guest_denied=False,
                iteration_limit_content=None,
                tool_rounds=0,
            )

        loop_messages = self._tool_chat_service._build_loop_messages(request.messages)
        max_iterations = self._tool_chat_service._max_tool_iterations
        tool_rounds = 0
        last_completion_content: str | None = None
        frames: list[str] = []

        for iteration in range(max_iterations):
            if await http_request.is_disconnected():
                logger.info(
                    "Client disconnected during stream tool loop",
                    response_id=response_id,
                    iteration=iteration + 1,
                )
                return _StreamToolLoopResult(
                    frames=frames,
                    loop_messages=loop_messages,
                    guest_denied=False,
                    iteration_limit_content=None,
                    tool_rounds=tool_rounds,
                )

            completion = await asyncio.wait_for(
                provider.complete_chat_with_tools(
                    loop_messages,
                    model,
                    tools,
                    request.temperature,
                ),
                timeout=self._settings.request_timeout_seconds,
            )
            last_completion_content = completion.content

            if not completion.tool_calls:
                return _StreamToolLoopResult(
                    frames=frames,
                    loop_messages=loop_messages,
                    guest_denied=False,
                    iteration_limit_content=None,
                    tool_rounds=tool_rounds,
                )

            assistant_message = _assistant_tool_call_message(completion)
            loop_messages.append(assistant_message)
            tool_rounds += 1

            guest_denied = False
            for tool_call in completion.tool_calls:
                frames.append(
                    format_sse(
                        "tool_start",
                        ToolStartFrame(
                            id=response_id,
                            tool_name=tool_call.name,
                            call_id=tool_call.id,
                        ),
                    )
                )
                (
                    tool_result_content,
                    success,
                    denied,
                ) = await self._execute_stream_tool_call(
                    tool_call=tool_call,
                    caller=caller,
                    http_request=http_request,
                )
                frames.append(
                    format_sse(
                        "tool_end",
                        ToolEndFrame(
                            id=response_id,
                            tool_name=tool_call.name,
                            call_id=tool_call.id,
                            success=success,
                        ),
                    )
                )
                if denied:
                    guest_denied = True
                loop_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result_content,
                    }
                )

            if guest_denied:
                return _StreamToolLoopResult(
                    frames=frames,
                    loop_messages=loop_messages,
                    guest_denied=True,
                    iteration_limit_content=None,
                    tool_rounds=tool_rounds,
                )

        logger.warning(
            "Stream tool iteration cap reached",
            provider=provider_name,
            model=model,
            max_iterations=max_iterations,
            stream_tool_rounds=tool_rounds,
        )
        fallback_content = (
            last_completion_content
            if last_completion_content
            else _TOOL_ITERATION_LIMIT_MESSAGE
        )
        return _StreamToolLoopResult(
            frames=frames,
            loop_messages=loop_messages,
            guest_denied=False,
            iteration_limit_content=fallback_content,
            tool_rounds=tool_rounds,
        )

    async def _execute_stream_tool_call(
        self,
        *,
        tool_call: ProviderToolCall,
        caller: CallerContext | None,
        http_request: Request,
    ) -> tuple[str, bool, bool]:
        if await http_request.is_disconnected():
            return '{"success": false, "error": "Client disconnected."}', False, False

        result_content, denied = await self._tool_chat_service._execute_tool_call(
            tool_call=tool_call,
            caller=caller,
            on_activity=None,
        )
        try:
            parsed = json.loads(result_content)
            success = bool(parsed.get("success")) and not denied
        except json.JSONDecodeError:
            success = False
        return result_content, success, denied

    async def _stream_provider_answer(
        self,
        *,
        provider: LLMProvider,
        messages: list[ChatMessageSchema],
        model: str,
        provider_name: ProviderName,
        temperature: float,
        response_id: str,
        session_id: uuid.UUID | None,
        caller: CallerContext | None,
        prep: _StreamPrep | None,
        http_request: Request,
        tool_rounds: int,
        request_start_time: float | None = None,
        retrieval_latency_ms: int | None = None,
    ) -> AsyncIterator[str]:
        yield format_sse("start", StartFrame(id=response_id, session_id=session_id))

        provider_stream: AsyncIterator[ProviderChunk] | None = None
        closable_provider_stream: ClosableAsyncIterator | None = None
        collected: list[str] = []
        finish_reason = "stop"
        stream_start = time.perf_counter()
        first_delta_logged = False

        try:
            provider_stream = provider.stream_chat(
                messages,
                model,
                temperature,
                max_tokens=resolve_max_tokens(
                    caller,
                    self._settings,
                    provider_name=provider_name,
                ),
            ).__aiter__()
            closable_provider_stream = cast(
                ClosableAsyncIterator | None, provider_stream
            )

            while True:
                if await http_request.is_disconnected():
                    logger.info(
                        "Client disconnected, stopping unified stream",
                        response_id=response_id,
                    )
                    await self._chat_service._persist_stream_result(
                        caller=caller,
                        prep=prep,
                        provider=provider,
                        provider_name=provider_name,
                        model=model,
                        content="".join(collected),
                        finish_reason="interrupted",
                        status="interrupted",
                    )
                    return

                try:
                    chunk = await asyncio.wait_for(
                        anext(provider_stream),
                        timeout=self._settings.request_timeout_seconds,
                    )
                except StopAsyncIteration:
                    break

                if chunk["content"]:
                    if not first_delta_logged and request_start_time is not None:
                        time_to_first_delta_ms = int(
                            (time.perf_counter() - request_start_time) * 1000
                        )
                        logger.info(
                            "Unified stream first delta",
                            response_id=response_id,
                            time_to_first_delta_ms=time_to_first_delta_ms,
                            retrieval_latency_ms=retrieval_latency_ms,
                        )
                        first_delta_logged = True
                    collected.append(chunk["content"])
                    yield format_sse(
                        "delta", DeltaFrame(id=response_id, content=chunk["content"])
                    )
                if chunk["finish_reason"]:
                    finish_reason = chunk["finish_reason"]

            if not collected:
                empty_error = EmptyProviderResponseError()
                logger.warning(
                    "Unified provider stream returned no content",
                    provider=provider_name,
                    model=model,
                    response_id=response_id,
                    finish_reason=finish_reason,
                )
                await self._chat_service._persist_stream_result(
                    caller=caller,
                    prep=prep,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    content="",
                    finish_reason=None,
                    status="error",
                )
                yield format_sse(
                    "error",
                    ErrorFrame(
                        id=response_id,
                        code=empty_error.code,
                        message=empty_error.message,
                    ),
                )
                return

            await self._chat_service._persist_stream_result(
                caller=caller,
                prep=prep,
                provider=provider,
                provider_name=provider_name,
                model=model,
                content="".join(collected),
                finish_reason=finish_reason,
                status="complete",
            )
            latency_ms = int((time.perf_counter() - stream_start) * 1000)
            logger.info(
                "Unified chat stream completed",
                provider=provider_name,
                model=model,
                latency_ms=latency_ms,
                response_id=response_id,
                stream_tool_rounds=tool_rounds,
                retrieval_latency_ms=retrieval_latency_ms,
            )
            yield format_sse(
                "end", EndFrame(id=response_id, finish_reason=finish_reason)
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            app_error = normalize_chat_error(exc)
            logger.exception(
                "Unified stream provider failed",
                response_id=response_id,
                provider=provider_name,
                model=model,
            )
            try:
                await self._chat_service._persist_stream_result(
                    caller=caller,
                    prep=prep,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    content="".join(collected),
                    finish_reason=None,
                    status="error",
                )
            except Exception:  # noqa: BLE001 - best-effort error persistence
                logger.exception(
                    "Failed to persist unified stream error state",
                    response_id=response_id,
                )
            yield format_sse(
                "error",
                ErrorFrame(
                    id=response_id,
                    code=app_error.code,
                    message=app_error.message,
                ),
            )
        finally:
            if closable_provider_stream is not None:
                close_stream = cast(
                    Callable[[], Awaitable[None]] | None,
                    getattr(closable_provider_stream, "aclose", None),
                )
                if callable(close_stream):
                    await close_stream()

    async def _stream_static_content(
        self,
        *,
        response_id: str,
        session_id: uuid.UUID | None,
        content: str,
        finish_reason: str,
        caller: CallerContext | None,
        prep: _StreamPrep | None,
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
    ) -> AsyncIterator[str]:
        yield format_sse("start", StartFrame(id=response_id, session_id=session_id))
        if content:
            yield format_sse("delta", DeltaFrame(id=response_id, content=content))
        await self._chat_service._persist_stream_result(
            caller=caller,
            prep=prep,
            provider=provider,
            provider_name=provider_name,
            model=model,
            content=content,
            finish_reason=finish_reason,
            status="complete",
        )
        yield format_sse("end", EndFrame(id=response_id, finish_reason=finish_reason))

    async def _stream_guest_denial(
        self,
        *,
        request: ChatRequestSchema,
        caller: CallerContext | None,
        response_id: str,
        session_id: uuid.UUID | None,
        prep: _StreamPrep | None,
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
    ) -> AsyncIterator[str]:
        if not self._chat_service._persistence_active(caller):
            async for frame in self._stream_static_content(
                response_id=response_id,
                session_id=session_id,
                content=_GUEST_TOOL_DENIED_MESSAGE,
                finish_reason="stop",
                caller=caller,
                prep=prep,
                provider=provider,
                provider_name=provider_name,
                model=model,
            ):
                yield frame
            return

        assert caller is not None
        chat_store = self._chat_service._chat_store
        assert chat_store is not None
        self._chat_service._enforce_guest_provider_gating(caller, provider_name, model)
        prompt_text = self._chat_service._last_user_content(request)

        try:
            await self._chat_service._maybe_check_quota(caller)
            chat_session = await self._chat_service._resolve_session(request, caller)
            if prep is None:
                user_seq = await chat_store.allocate_seq(chat_session.id)
                await chat_store.add_message(
                    session_id=chat_session.id,
                    seq=user_seq,
                    role="user",
                    content=prompt_text,
                    client_message_id=request.client_message_id,
                )
                await self._chat_service._maybe_set_session_title(
                    chat_session, prompt_text
                )
            assistant_seq = await chat_store.allocate_seq(chat_session.id)
            await chat_store.add_message(
                session_id=chat_session.id,
                seq=assistant_seq,
                role="assistant",
                content=_GUEST_TOOL_DENIED_MESSAGE,
                provider=provider_name,
                model=model,
                status="complete",
                finish_reason="stop",
            )
            await chat_store.mark_last_message_at(chat_session.id)
            await self._chat_service._commit()
        except ChatServiceError:
            raise
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        async for frame in self._stream_static_content(
            response_id=response_id,
            session_id=chat_session.id,
            content=_GUEST_TOOL_DENIED_MESSAGE,
            finish_reason="stop",
            caller=caller,
            prep=prep,
            provider=provider,
            provider_name=provider_name,
            model=model,
        ):
            yield frame

    def _resolve_provider_name(self, request: ChatRequestSchema) -> ProviderName:
        _, _, provider_name = self._chat_service._resolve_provider(request)
        return provider_name

    def _use_agent_runtime(self) -> bool:
        return self._settings.agent_runtime_enabled and self._agent is not None

    def _use_advanced_pipeline(self) -> bool:
        return (
            self._settings.advanced_rag_enabled and self._advanced_pipeline is not None
        )

    async def _retrieve_document_context(
        self,
        *,
        question: str,
        user_id: uuid.UUID,
    ) -> _DocumentRetrievalResult:
        """Run V1 dense retrieve→context or flag-on advanced pipeline."""
        if self._use_advanced_pipeline():
            assert self._advanced_pipeline is not None
            result = await self._advanced_pipeline.retrieve(
                RetrievalRequest(
                    question=question,
                    user_id=user_id,
                    top_k=self._settings.rag_top_k,
                )
            )
            return _document_result_from_advanced(result)

        chunks = await self._retriever.retrieve(
            question=question,
            user_id=user_id,
            top_k=self._settings.rag_top_k,
        )
        if not chunks:
            return _DocumentRetrievalResult(
                empty=True,
                context_text="",
                retrieved_chunks=[],
                citations=None,
                retrieval_latency_ms=None,
            )
        built_context = self._context_builder.build(chunks)
        return _DocumentRetrievalResult(
            empty=False,
            context_text=built_context.text,
            retrieved_chunks=[
                _chunk_meta(chunk) for chunk in built_context.included_chunks
            ],
            citations=None,
            retrieval_latency_ms=None,
        )

    @staticmethod
    def _is_guest_or_anonymous(caller: CallerContext | None) -> bool:
        return caller is None or caller.kind == "guest"

    async def _guest_denial_response(
        self,
        request: ChatRequestSchema,
        caller: CallerContext | None,
    ) -> ChatResponseSchema:
        _, model, provider_name = self._chat_service._resolve_provider(request)
        if not self._chat_service._persistence_active(caller):
            return ChatResponseSchema(
                id=f"resp_{uuid.uuid4().hex[:12]}",
                content=_GUEST_TOOL_DENIED_MESSAGE,
                model=model,
                provider=provider_name,
            )

        assert caller is not None
        chat_store = self._chat_service._chat_store
        assert chat_store is not None
        self._chat_service._enforce_guest_provider_gating(caller, provider_name, model)
        prompt_text = self._chat_service._last_user_content(request)

        try:
            await self._chat_service._maybe_check_quota(caller)
            chat_session = await self._chat_service._resolve_session(request, caller)
            user_seq = await chat_store.allocate_seq(chat_session.id)
            await chat_store.add_message(
                session_id=chat_session.id,
                seq=user_seq,
                role="user",
                content=prompt_text,
                client_message_id=request.client_message_id,
            )
            await self._chat_service._maybe_set_session_title(chat_session, prompt_text)
            assistant_seq = await chat_store.allocate_seq(chat_session.id)
            await chat_store.add_message(
                session_id=chat_session.id,
                seq=assistant_seq,
                role="assistant",
                content=_GUEST_TOOL_DENIED_MESSAGE,
                provider=provider_name,
                model=model,
                status="complete",
                finish_reason="stop",
            )
            await chat_store.mark_last_message_at(chat_session.id)
            await self._chat_service._commit()
        except ChatServiceError:
            raise
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        return ChatResponseSchema(
            id=f"resp_{uuid.uuid4().hex[:12]}",
            content=_GUEST_TOOL_DENIED_MESSAGE,
            model=model,
            provider=provider_name,
            session_id=chat_session.id,
        )

    async def _empty_corpus_response(
        self,
        *,
        request: ChatRequestSchema,
        caller: CallerContext | None,
        model: str,
        provider_name: ProviderName,
        citations: list[CitationSchema] | None = None,
    ) -> ChatResponseSchema:
        if not self._chat_service._persistence_active(caller):
            return ChatResponseSchema(
                id=f"resp_{uuid.uuid4().hex[:12]}",
                content=EMPTY_CORPUS_MESSAGE,
                model=model,
                provider=provider_name,
                retrieved_chunks=[],
                citations=citations,
            )

        assert caller is not None
        chat_store = self._chat_service._chat_store
        assert chat_store is not None
        self._chat_service._enforce_guest_provider_gating(caller, provider_name, model)
        prompt_text = self._chat_service._last_user_content(request)

        try:
            await self._chat_service._maybe_check_quota(caller)
            chat_session = await self._chat_service._resolve_session(request, caller)
            user_seq = await chat_store.allocate_seq(chat_session.id)
            await chat_store.add_message(
                session_id=chat_session.id,
                seq=user_seq,
                role="user",
                content=prompt_text,
                client_message_id=request.client_message_id,
            )
            await self._chat_service._maybe_set_session_title(chat_session, prompt_text)
            assistant_seq = await chat_store.allocate_seq(chat_session.id)
            await chat_store.add_message(
                session_id=chat_session.id,
                seq=assistant_seq,
                role="assistant",
                content=EMPTY_CORPUS_MESSAGE,
                provider=provider_name,
                model=model,
                status="complete",
                finish_reason="stop",
            )
            await chat_store.mark_last_message_at(chat_session.id)
            await self._chat_service._commit()
        except ChatServiceError:
            raise
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        return ChatResponseSchema(
            id=f"resp_{uuid.uuid4().hex[:12]}",
            content=EMPTY_CORPUS_MESSAGE,
            model=model,
            provider=provider_name,
            session_id=chat_session.id,
            retrieved_chunks=[],
            citations=citations,
        )

    async def _apply_memory_context(
        self,
        request: ChatRequestSchema,
        caller: CallerContext | None,
        *,
        session_id: uuid.UUID | None,
    ) -> ChatRequestSchema:
        """Inject Memory context via the shared ``ChatService`` helper.

        Used by branches that bypass ``ChatService.complete_chat``/``stream_chat``
        own message resolution (tool loop, agent runtime, RAG/plain streaming)
        so Memory retrieve/inject still runs — orchestrated from this class per
        Part I's architectural boundary.
        """
        messages = await self._chat_service._apply_memory_context(
            session_id=session_id,
            caller=caller,
            messages=request.messages,
        )
        if messages is request.messages:
            return request
        return request.model_copy(update={"messages": messages})

    def _merge_document_context(
        self,
        *,
        request: ChatRequestSchema,
        question: str,
        context_text: str,
    ) -> ChatRequestSchema:
        document_prompt = self._prompt_manager.render(
            "chat",
            "document_context",
            "1",
            {"context": context_text, "question": question},
        )
        prior_messages = list(request.messages[:-1])
        user_message = request.messages[-1]
        merged_messages = [
            *prior_messages,
            ChatMessageSchema.model_construct(
                role="system",
                content=document_prompt,
            ),
            user_message,
        ]
        return request.model_copy(update={"messages": merged_messages})


class _StreamToolLoopResult:
    __slots__ = (
        "frames",
        "loop_messages",
        "guest_denied",
        "iteration_limit_content",
        "tool_rounds",
    )

    def __init__(
        self,
        *,
        frames: list[str],
        loop_messages: list[ChatMessageInput],
        guest_denied: bool,
        iteration_limit_content: str | None,
        tool_rounds: int,
    ) -> None:
        self.frames = frames
        self.loop_messages = loop_messages
        self.guest_denied = guest_denied
        self.iteration_limit_content = iteration_limit_content
        self.tool_rounds = tool_rounds


class _DocumentRetrievalResult:
    __slots__ = (
        "empty",
        "context_text",
        "retrieved_chunks",
        "citations",
        "retrieval_latency_ms",
    )

    def __init__(
        self,
        *,
        empty: bool,
        context_text: str,
        retrieved_chunks: list[RetrievedChunkMetaSchema],
        citations: list[CitationSchema] | None,
        retrieval_latency_ms: int | None,
    ) -> None:
        self.empty = empty
        self.context_text = context_text
        self.retrieved_chunks = retrieved_chunks
        self.citations = citations
        self.retrieval_latency_ms = retrieval_latency_ms


def _chunk_meta(chunk: ScoredChunk) -> RetrievedChunkMetaSchema:
    return RetrievedChunkMetaSchema(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        score=chunk.score,
    )


def _document_result_from_advanced(
    result: RetrievalResult,
) -> _DocumentRetrievalResult:
    if not result.candidates:
        return _DocumentRetrievalResult(
            empty=True,
            context_text="",
            retrieved_chunks=[],
            citations=[],
            retrieval_latency_ms=result.retrieval_latency_ms,
        )

    by_id = {
        candidate.chunk.chunk_id: candidate
        for candidate in result.candidates
        if candidate.chunk.chunk_id is not None
    }
    retrieved_chunks: list[RetrievedChunkMetaSchema] = []
    for citation in result.citations:
        candidate = by_id.get(citation.chunk_id)
        retrieved_chunks.append(
            RetrievedChunkMetaSchema(
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                chunk_index=(
                    candidate.chunk.chunk_index if candidate is not None else None
                ),
                score=citation.score,
            )
        )
    return _DocumentRetrievalResult(
        empty=False,
        context_text=result.context_text,
        retrieved_chunks=retrieved_chunks,
        citations=to_citation_schemas(list(result.citations)),
        retrieval_latency_ms=result.retrieval_latency_ms,
    )
