"""Generic RAG orchestration: retrieval → context → prompt → LLM."""

from __future__ import annotations

import time
import uuid
from typing import cast

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.prompt_builder import PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import RAGResponse, RetrievedChunkMeta
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import LLMProvider
from app.schemas.chat import ChatMessageSchema, ProviderName

_logger = get_logger(__name__)

EMPTY_CORPUS_MESSAGE = "I couldn't find any relevant documents to answer your question."


class RAGService:
    """Domain-agnostic RAG pipeline orchestrator (non-streaming, V1)."""

    def __init__(
        self,
        *,
        retriever: Retriever,
        context_builder: ContextBuilder,
        prompt_builder: PromptBuilder,
        llm_provider: LLMProvider,
        settings: Settings,
    ) -> None:
        self._retriever = retriever
        self._context_builder = context_builder
        self._prompt_builder = prompt_builder
        self._llm_provider = llm_provider
        self._settings = settings

    async def ask(
        self,
        *,
        user_id: uuid.UUID,
        question: str,
        prompt_template: str | None = None,
        instructions: str | None = None,
        top_k: int | None = None,
        temperature: float | None = None,
    ) -> RAGResponse:
        start = time.perf_counter()
        provider_name = cast(ProviderName, self._settings.llm_provider)
        model = self._default_model(provider_name)
        effective_temperature = (
            temperature
            if temperature is not None
            else self._settings.default_temperature
        )

        retrieval_start = time.perf_counter()
        chunks = await self._retriever.retrieve(
            question=question,
            user_id=user_id,
            top_k=top_k,
        )
        retrieval_latency_ms = int((time.perf_counter() - retrieval_start) * 1000)
        retrieval_count = len(chunks)
        top_score = max((chunk.score for chunk in chunks), default=None)

        if not chunks:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self._log_request(
                duration_ms=duration_ms,
                retrieval_count=0,
                included_count=0,
                top_score=None,
                truncated=False,
                retrieval_latency_ms=retrieval_latency_ms,
                llm_latency_ms=0,
            )
            return RAGResponse(
                answer=EMPTY_CORPUS_MESSAGE,
                retrieved_chunks=[],
                truncated=False,
                model=model,
                provider=provider_name,
                retrieval_latency_ms=retrieval_latency_ms,
                llm_latency_ms=0,
            )

        built_context = self._context_builder.build(chunks)
        built_prompt = self._prompt_builder.build(
            question=question,
            context=built_context.text,
            template_ref=prompt_template,
            instructions=instructions,
        )
        messages = self._build_messages(
            built_prompt.system_prompt, built_prompt.user_prompt
        )

        llm_start = time.perf_counter()
        completion = await self._llm_provider.complete_chat(
            messages,
            model,
            effective_temperature,
        )
        llm_latency_ms = int((time.perf_counter() - llm_start) * 1000)

        duration_ms = int((time.perf_counter() - start) * 1000)
        self._log_request(
            duration_ms=duration_ms,
            retrieval_count=retrieval_count,
            included_count=len(built_context.included_chunks),
            top_score=top_score,
            truncated=built_context.truncated,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=llm_latency_ms,
        )

        return RAGResponse(
            answer=completion.content,
            retrieved_chunks=[
                _chunk_meta(chunk) for chunk in built_context.included_chunks
            ],
            truncated=built_context.truncated,
            model=model,
            provider=provider_name,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=llm_latency_ms,
        )

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
        messages: list[ChatMessageSchema] = []
        if system_prompt:
            messages.append(ChatMessageSchema(role="system", content=system_prompt))
        messages.append(ChatMessageSchema(role="user", content=user_prompt))
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
        )


def _chunk_meta(chunk: ScoredChunk) -> RetrievedChunkMeta:
    return RetrievedChunkMeta(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        score=chunk.score,
    )
