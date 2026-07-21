"""Unified chat orchestration (V1.1b non-streaming, V1.1c streaming tools).

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

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.prompts.manager import PromptManager
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.retriever import Retriever
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
    DeltaFrame,
    EndFrame,
    ErrorFrame,
    ProviderName,
    RetrievedChunkMetaSchema,
    StartFrame,
    ToolEndFrame,
    ToolStartFrame,
)
from app.services.chat_service import (
    ChatService,
    ChatServiceError,
    ClosableAsyncIterator,
    DbUnavailableError,
    _StreamPrep,
    format_sse,
    normalize_chat_error,
)
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
    ) -> None:
        self._chat_service = chat_service
        self._tool_chat_service = tool_chat_service
        self._retriever = retriever
        self._context_builder = context_builder
        self._prompt_manager = prompt_manager
        self._settings = settings

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

        if effective_documents:
            assert caller is not None and caller.user_id is not None
            question = self._chat_service._last_user_content(request)
            if on_activity is not None:
                await on_activity("document_retrieval")
            try:
                chunks = await self._retriever.retrieve(
                    question=question,
                    user_id=caller.user_id,
                    top_k=self._settings.rag_top_k,
                )
            finally:
                if on_activity is not None:
                    await on_activity("thinking")
            if not chunks:
                return await self._empty_corpus_response(
                    request=request,
                    caller=caller,
                    model=model,
                    provider_name=provider_name,
                )

            built_context = self._context_builder.build(chunks)
            retrieved_chunks = [
                _chunk_meta(chunk) for chunk in built_context.included_chunks
            ]
            working_request = self._merge_document_context(
                request=request,
                question=question,
                context_text=built_context.text,
            )

        if effective_web_search:
            response = await self._tool_chat_service.complete_chat(
                working_request,
                caller,
                on_activity=on_activity,
                allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
            )
        else:
            response = await self._chat_service.complete_chat(working_request, caller)

        if retrieved_chunks is not None:
            response = response.model_copy(
                update={"retrieved_chunks": retrieved_chunks}
            )
        return response

    async def stream_execute(
        self,
        request: ChatRequestSchema,
        http_request: Request,
        caller: CallerContext | None = None,
        prep: _StreamPrep | None = None,
    ) -> AsyncIterator[str]:
        """SSE generator for streaming chat with web search tool loop (V1.1c)."""
        provider, model, provider_name = self._chat_service._resolve_provider(request)
        response_id = f"resp_{uuid.uuid4().hex[:12]}"
        session_id = prep.session_id if prep is not None else None

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

        stream_messages: list[ChatMessageInput] = list(request.messages)
        tool_rounds = 0

        try:
            loop_result = await self._run_stream_tool_loop(
                provider=provider,
                request=request,
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
                temperature=request.temperature,
                response_id=response_id,
                session_id=session_id,
                caller=caller,
                prep=prep,
                http_request=http_request,
                tool_rounds=tool_rounds,
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
    ) -> AsyncIterator[str]:
        yield format_sse("start", StartFrame(id=response_id, session_id=session_id))

        provider_stream: AsyncIterator[ProviderChunk] | None = None
        closable_provider_stream: ClosableAsyncIterator | None = None
        collected: list[str] = []
        finish_reason = "stop"
        stream_start = time.perf_counter()

        try:
            provider_stream = provider.stream_chat(
                messages, model, temperature
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
                    collected.append(chunk["content"])
                    yield format_sse(
                        "delta", DeltaFrame(id=response_id, content=chunk["content"])
                    )
                if chunk["finish_reason"]:
                    finish_reason = chunk["finish_reason"]

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
    ) -> ChatResponseSchema:
        if not self._chat_service._persistence_active(caller):
            return ChatResponseSchema(
                id=f"resp_{uuid.uuid4().hex[:12]}",
                content=EMPTY_CORPUS_MESSAGE,
                model=model,
                provider=provider_name,
                retrieved_chunks=[],
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
        )

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


def _chunk_meta(chunk: ScoredChunk) -> RetrievedChunkMetaSchema:
    return RetrievedChunkMetaSchema(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        score=chunk.score,
    )
