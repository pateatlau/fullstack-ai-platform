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
from app.providers.factory import ProviderFactory
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
        settings: Settings,
    ) -> None:
        self._retriever = retriever
        self._context_builder = context_builder
        self._prompt_builder = prompt_builder
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
                model=resolved_model,
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
            included_count=len(built_context.included_chunks),
            top_score=top_score,
            truncated=built_context.truncated,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=llm_latency_ms,
        )

        return RAGResponse(
            answer=completion.content or "",
            retrieved_chunks=[
                _chunk_meta(chunk) for chunk in built_context.included_chunks
            ],
            truncated=built_context.truncated,
            model=resolved_model,
            provider=provider_name,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=llm_latency_ms,
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
