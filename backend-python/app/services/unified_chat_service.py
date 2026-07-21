"""Unified non-streaming chat orchestration (V1.1b).

Composes ``ChatService``, ``ToolChatService``, and RAG retrieval components
without adding domain logic to ``app/ai/rag/`` framework modules.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.prompts.manager import PromptManager
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.service import EMPTY_CORPUS_MESSAGE
from app.ai.tools.implementations.web_search import WEB_SEARCH_TOOL_NAME
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.capabilities import get_capabilities
from app.schemas.chat import (
    ChatMessageSchema,
    ChatRequestSchema,
    ChatResponseSchema,
    ProviderName,
    RetrievedChunkMetaSchema,
)
from app.services.chat_service import ChatService, ChatServiceError, DbUnavailableError
from app.services.tool_chat_service import (
    ChatActivityCallback,
    ToolChatService,
    _GUEST_TOOL_DENIED_MESSAGE,
)


class UnifiedChatService:
    """Canonical non-streaming chat orchestrator for unified toggles."""

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


def _chunk_meta(chunk: ScoredChunk) -> RetrievedChunkMetaSchema:
    return RetrievedChunkMetaSchema(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        score=chunk.score,
    )
