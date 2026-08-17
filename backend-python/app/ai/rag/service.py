"""Generic RAG orchestration: retrieval → context → prompt → LLM."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, cast

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.pipeline import AdvancedRetrievalPipeline
from app.ai.rag.prompt_builder import PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import (
    Citation,
    RAGResponse,
    RetrievalRequest,
    RetrievalResult,
    RetrievedChunkMeta,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import LLMProvider
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ProviderName

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from app.ai.security.audit.logger import AuditLogger
    from app.ai.security.guardrails.engine import GuardrailEngine

EMPTY_CORPUS_MESSAGE = "I couldn't find any relevant documents to answer your question."


class RAGService:
    """Domain-agnostic RAG pipeline orchestrator (non-streaming).

    When ``advanced_rag_enabled``, uses :class:`AdvancedRetrievalPipeline`.
    Otherwise keeps the V1 dense ``Retriever`` → ``ContextBuilder`` path.
    """

    def __init__(
        self,
        *,
        retriever: Retriever,
        context_builder: ContextBuilder,
        prompt_builder: PromptBuilder,
        settings: Settings,
        advanced_pipeline: AdvancedRetrievalPipeline | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._retriever = retriever
        self._context_builder = context_builder
        self._prompt_builder = prompt_builder
        self._settings = settings
        self._advanced_pipeline = advanced_pipeline
        self._guardrail_engine = guardrail_engine
        self._audit_logger = audit_logger

    async def ask(
        self,
        *,
        user_id: uuid.UUID,
        question: str,
        prompt_template: str | None = None,
        instructions: str | None = None,
        top_k: int | None = None,
        temperature: float | None = None,
        provider: ProviderName | None = None,
        model: str | None = None,
    ) -> RAGResponse:
        start = time.perf_counter()
        llm, resolved_model, provider_name = self._resolve_provider(
            provider=provider,
            model=model,
        )
        effective_temperature = (
            temperature
            if temperature is not None
            else self._settings.default_temperature
        )

        retrieval_start = time.perf_counter()
        if self._use_advanced_pipeline():
            retrieval = await self._advanced_retrieve(
                question=question,
                user_id=user_id,
                top_k=top_k,
            )
            retrieval_latency_ms = (
                retrieval.retrieval_latency_ms
                if retrieval.retrieval_latency_ms is not None
                else int((time.perf_counter() - retrieval_start) * 1000)
            )
            if not retrieval.candidates:
                return self._empty_response(
                    start=start,
                    resolved_model=resolved_model,
                    provider_name=provider_name,
                    retrieval_latency_ms=retrieval_latency_ms,
                    citations=[],
                )
            context_text = retrieval.context_text
            truncated = retrieval.truncated
            retrieved_chunks = _chunk_metas_from_retrieval(retrieval)
            citations: list[Citation] | None = list(retrieval.citations)
            retrieval_count = len(retrieval.candidates)
            included_count = len(retrieved_chunks)
            top_score = max(
                (c.final_score for c in retrieval.candidates),
                default=None,
            )
        else:
            chunks = await self._retriever.retrieve(
                question=question,
                user_id=user_id,
                top_k=top_k,
            )
            retrieval_latency_ms = int((time.perf_counter() - retrieval_start) * 1000)
            retrieval_count = len(chunks)
            top_score = max((chunk.score for chunk in chunks), default=None)

            if not chunks:
                return self._empty_response(
                    start=start,
                    resolved_model=resolved_model,
                    provider_name=provider_name,
                    retrieval_latency_ms=retrieval_latency_ms,
                    citations=None,
                )

            chunks = await self._filter_guarded_chunks(chunks, user_id=user_id)

            built_context = self._context_builder.build(chunks)
            context_text = built_context.text
            truncated = built_context.truncated
            retrieved_chunks = [
                _chunk_meta(chunk) for chunk in built_context.included_chunks
            ]
            citations = None
            included_count = len(built_context.included_chunks)

        built_prompt = self._prompt_builder.build(
            question=question,
            context=context_text,
            template_ref=prompt_template,
            instructions=instructions,
        )
        messages = self._build_messages(
            built_prompt.system_prompt, built_prompt.user_prompt
        )

        llm_start = time.perf_counter()
        completion = await llm.complete_chat(
            messages,
            resolved_model,
            effective_temperature,
        )
        llm_latency_ms = int((time.perf_counter() - llm_start) * 1000)

        duration_ms = int((time.perf_counter() - start) * 1000)
        self._log_request(
            duration_ms=duration_ms,
            retrieval_count=retrieval_count,
            included_count=included_count,
            top_score=top_score,
            truncated=truncated,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=llm_latency_ms,
            advanced_rag_enabled=self._use_advanced_pipeline(),
            citation_count=len(citations) if citations is not None else None,
        )

        return RAGResponse(
            answer=completion.content or "",
            retrieved_chunks=retrieved_chunks,
            truncated=truncated,
            model=resolved_model,
            provider=provider_name,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=llm_latency_ms,
            citations=citations,
        )

    async def _filter_guarded_chunks(
        self, chunks: list[ScoredChunk], *, user_id: uuid.UUID
    ) -> list[ScoredChunk]:
        if self._guardrail_engine is None:
            return chunks

        from app.ai.security.guardrails.enforcement import evaluate_guardrail
        from app.ai.security.guardrails.models import (
            GuardrailAction,
            GuardrailContext,
        )
        from app.core.caller import CallerContext

        included: list[ScoredChunk] = []
        actor = CallerContext.for_user(user_id)
        for chunk in chunks:
            verdict = await evaluate_guardrail(
                self._guardrail_engine,
                GuardrailContext(
                    content_text=chunk.content,
                    source="rag_chunk",
                    document_id=str(chunk.document_id),
                ),
                audit_logger=self._audit_logger,
                actor=actor,
            )
            if verdict.action is not GuardrailAction.BLOCK:
                included.append(chunk)
        return included

    def _use_advanced_pipeline(self) -> bool:
        return (
            self._settings.advanced_rag_enabled and self._advanced_pipeline is not None
        )

    async def _advanced_retrieve(
        self,
        *,
        question: str,
        user_id: uuid.UUID,
        top_k: int | None,
    ) -> RetrievalResult:
        assert self._advanced_pipeline is not None
        return await self._advanced_pipeline.retrieve(
            RetrievalRequest(
                question=question,
                user_id=user_id,
                top_k=top_k,
            )
        )

    def _empty_response(
        self,
        *,
        start: float,
        resolved_model: str,
        provider_name: ProviderName,
        retrieval_latency_ms: int,
        citations: list[Citation] | None,
    ) -> RAGResponse:
        duration_ms = int((time.perf_counter() - start) * 1000)
        self._log_request(
            duration_ms=duration_ms,
            retrieval_count=0,
            included_count=0,
            top_score=None,
            truncated=False,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=0,
            advanced_rag_enabled=self._use_advanced_pipeline(),
            citation_count=0 if citations is not None else None,
        )
        return RAGResponse(
            answer=EMPTY_CORPUS_MESSAGE,
            retrieved_chunks=[],
            truncated=False,
            model=resolved_model,
            provider=provider_name,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=0,
            citations=citations,
        )

    def _resolve_provider(
        self,
        *,
        provider: ProviderName | None,
        model: str | None,
    ) -> tuple[LLMProvider, str, ProviderName]:
        from app.services.chat_service import ChatServiceError

        provider_name_raw = provider or self._settings.llm_provider
        allowed_providers: tuple[ProviderName, ...] = (
            "openai",
            "gemini",
            "groq",
            "anthropic",
        )
        if provider_name_raw not in allowed_providers:
            raise ChatServiceError(
                code="validation_error",
                message=(
                    f"Unsupported provider '{provider_name_raw}'. "
                    "Supported providers: openai, gemini, groq, anthropic."
                ),
                status_code=422,
            )

        provider_name = cast(ProviderName, provider_name_raw)
        required_key_by_provider: dict[ProviderName, tuple[str, str | None]] = {
            "openai": ("OPENAI_API_KEY", self._settings.openai_api_key),
            "gemini": ("GEMINI_API_KEY", self._settings.gemini_api_key),
            "groq": ("GROQ_API_KEY", self._settings.groq_api_key),
            "anthropic": ("ANTHROPIC_API_KEY", self._settings.anthropic_api_key),
        }
        key_env_name, key_value = required_key_by_provider[provider_name]
        if not key_value:
            raise ChatServiceError(
                code="validation_error",
                message=(
                    f"Provider '{provider_name}' is selected but {key_env_name} "
                    "is not set."
                ),
                status_code=422,
            )

        llm = ProviderFactory.get_provider(provider_name, self._settings)
        resolved_model = model or self._default_model(provider_name)
        return llm, resolved_model, provider_name

    def _default_model(self, provider_name: ProviderName) -> str:
        default_models: dict[ProviderName, str] = {
            "openai": self._settings.openai_model,
            "gemini": self._settings.gemini_model,
            "groq": self._settings.groq_model,
            "anthropic": self._settings.anthropic_model,
        }
        return default_models[provider_name]

    def _build_messages(
        self,
        system_prompt: str | None,
        user_prompt: str,
    ) -> list[ChatMessageSchema]:
        """Build provider messages without API ``max_message_length`` validation.

        RAG prompts include retrieved document context and may exceed the chat
        request body limit (``max_message_length``). Those limits apply to user
        input on ``POST /api/chat``, not to server-assembled RAG prompts.
        """
        messages: list[ChatMessageSchema] = []
        if system_prompt:
            messages.append(
                ChatMessageSchema.model_construct(
                    role="system",
                    content=system_prompt,
                )
            )
        messages.append(
            ChatMessageSchema.model_construct(role="user", content=user_prompt)
        )
        return messages

    def _log_request(
        self,
        *,
        duration_ms: int,
        retrieval_count: int,
        included_count: int,
        top_score: float | None,
        truncated: bool,
        retrieval_latency_ms: int,
        llm_latency_ms: int,
        advanced_rag_enabled: bool = False,
        citation_count: int | None = None,
    ) -> None:
        _logger.info(
            "RAG request completed",
            rag_requests_total=1,
            rag_request_duration_ms=duration_ms,
            retrieval_count=retrieval_count,
            included_count=included_count,
            top_score=top_score,
            truncated=truncated,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=llm_latency_ms,
            advanced_rag_enabled=advanced_rag_enabled,
            citation_count=citation_count,
        )


def _chunk_meta(chunk: ScoredChunk) -> RetrievedChunkMeta:
    return RetrievedChunkMeta(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        score=chunk.score,
    )


def _chunk_metas_from_retrieval(result: RetrievalResult) -> list[RetrievedChunkMeta]:
    """Map post-compression citations to ``retrieved_chunks`` (included only)."""
    by_id = {
        candidate.chunk.chunk_id: candidate
        for candidate in result.candidates
        if candidate.chunk.chunk_id is not None
    }
    metas: list[RetrievedChunkMeta] = []
    for citation in result.citations:
        candidate = by_id.get(citation.chunk_id)
        metas.append(
            RetrievedChunkMeta(
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                chunk_index=(
                    candidate.chunk.chunk_index if candidate is not None else None
                ),
                score=citation.score,
            )
        )
    return metas
