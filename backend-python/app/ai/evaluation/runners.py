"""Evaluation runners for prompt, retrieval, and end-to-end levels."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.evaluation.datasets import EvalCase
from app.ai.evaluation.metrics import (
    TARGET_RAG_RESPONSE_MS,
    TARGET_RETRIEVAL_MS,
    answer_matches,
    faithfulness_score,
    hallucination_detected,
    latency_within_target,
    precision,
    recall,
)
from app.ai.evaluation.report import EvalCaseResult
from app.ai.prompts.manager import PromptManager
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.prompt_builder import PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.service import RAGService
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings
from app.db.documents import SqlDocumentStore
from app.db.identity import SqlUserStore
from app.providers.base import (
    ChatMessageInput,
    LLMProvider,
    ProviderChunk,
    ProviderCompletion,
    ProviderToolCompletion,
    ProviderUsage,
)
from app.schemas.chat import ChatMessageSchema
from app.services.knowledge_service import KnowledgeService

DOCUMENT_FIXTURES_ROOT = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "documents"
)
EMBEDDING_DIMENSIONS = 1536


class _FakeEmbeddingProvider:
    dimensions = EMBEDDING_DIMENSIONS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index % EMBEDDING_DIMENSIONS), 0.0]
            + [0.0] * (EMBEDDING_DIMENSIONS - 2)
            for index, _ in enumerate(texts)
        ]


class _EvalLLMProvider:
    """Deterministic LLM double for offline evaluation runs."""

    def __init__(self, *, default_response: str = "", judge_mode: bool = False) -> None:
        self._response = default_response
        self._judge_mode = judge_mode
        self.last_messages: list[ChatMessageSchema] = []

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        del messages, model, temperature
        if False:
            yield ProviderChunk(content="", finish_reason=None)

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> ProviderCompletion:
        del model, temperature
        self.last_messages = list(messages)
        content = self._response
        if self._judge_mode and messages:
            prompt = messages[-1].content
            if "Respond in JSON only" in prompt:
                content = (
                    '{"faithful": true, "hallucination": false, '
                    '"reason": "Answer aligns with context."}'
                )
        return ProviderCompletion(
            content=content,
            finish_reason="stop",
            usage=ProviderUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        )

    async def complete_chat_with_tools(
        self,
        messages: list[ChatMessageInput],
        model: str,
        tools: list[dict[str, object]],
        temperature: float = 0.7,
    ) -> ProviderToolCompletion:
        del messages, model, tools, temperature
        return ProviderToolCompletion(
            content=self._response,
            tool_calls=[],
            finish_reason="stop",
            usage=ProviderUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        )


@dataclass(frozen=True)
class PromptEvalRunner:
    """Render prompt templates and assert expected output."""

    prompt_manager: PromptManager

    def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        try:
            rendered = self.prompt_manager.render(
                case.prompt_category or "",
                case.prompt_name or "",
                case.prompt_version or "",
                case.prompt_variables,
            )
            passed = True
            if case.expected_render_exact is not None:
                passed = rendered == case.expected_render_exact
            for substring in case.expected_render_contains:
                if substring not in rendered:
                    passed = False
                    break
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="prompt",
                passed=passed,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="prompt",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )


@dataclass(frozen=True)
class RetrievalEvalRunner:
    """Ingest fixture documents and evaluate retriever precision/recall."""

    session: AsyncSession
    settings: Settings
    fixtures_root: Path = DOCUMENT_FIXTURES_ROOT

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        try:
            user_id = await self._create_user()
            relevant_ids = set(case.relevant_chunk_ids)

            if case.document_fixture:
                document_id = await self._ingest_fixture(
                    user_id=user_id,
                    filename=case.document_fixture,
                )
                if not relevant_ids:
                    relevant_ids = await self._all_chunk_ids(document_id)

            retrieved = await self._retriever().retrieve(
                question=case.question or "",
                user_id=user_id,
            )
            retrieved_ids = {
                chunk.chunk_id for chunk in retrieved if chunk.chunk_id is not None
            }
            case_precision = precision(retrieved_ids, relevant_ids)
            case_recall = recall(retrieved_ids, relevant_ids)
            if relevant_ids:
                passed = case_recall > 0.0
            else:
                passed = not retrieved_ids

            latency_ms = int((time.perf_counter() - start) * 1000)
            warning = None
            if not latency_within_target("retrieval", latency_ms, TARGET_RETRIEVAL_MS):
                warning = (
                    f"retrieval latency {latency_ms}ms exceeds {TARGET_RETRIEVAL_MS}ms"
                )

            return EvalCaseResult(
                case_id=case.id,
                level="retrieval",
                passed=passed,
                latency_ms=latency_ms,
                precision=case_precision,
                recall=case_recall,
                retrieved_count=len(retrieved_ids),
                latency_warning=warning,
            )
        except Exception as exc:
            await self.session.rollback()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="retrieval",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )

    async def _create_user(self) -> uuid.UUID:
        user = await SqlUserStore(self.session).create(
            sub=f"eval-{uuid.uuid4()}",
            email=None,
            name=None,
            picture=None,
        )
        return user.id

    async def _ingest_fixture(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
    ) -> uuid.UUID:
        fixture_path = self.fixtures_root / filename
        if not fixture_path.is_file():
            raise FileNotFoundError(f"Document fixture not found: {fixture_path}")
        knowledge = self._knowledge_service()
        return await knowledge.ingest_document(
            user_id=user_id,
            file_bytes=fixture_path.read_bytes(),
            filename=filename,
            mime_type=_guess_mime_type(filename),
        )

    async def _all_chunk_ids(self, document_id: uuid.UUID) -> set[uuid.UUID]:
        chunks = await SqlDocumentStore(self.session).list_chunks(document_id)
        return {chunk.id for chunk in chunks}

    def _knowledge_service(self) -> KnowledgeService:
        pipeline = IngestionPipeline(
            self.settings, embedding_provider=_FakeEmbeddingProvider()
        )
        vector_store = PgVectorStore(self.session, self.settings)
        return KnowledgeService(
            session=self.session,
            settings=self.settings,
            pipeline=pipeline,
            vector_store=vector_store,
        )

    def _retriever(self) -> Retriever:
        return Retriever(
            embedding_provider=_FakeEmbeddingProvider(),
            vector_store=PgVectorStore(self.session, self.settings),
            settings=self.settings,
        )


@dataclass(frozen=True)
class EndToEndEvalRunner:
    """Run the full RAG pipeline and evaluate answer quality."""

    session: AsyncSession
    settings: Settings
    prompt_manager: PromptManager
    use_judge: bool = False
    fixtures_root: Path = DOCUMENT_FIXTURES_ROOT

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        try:
            user = await SqlUserStore(self.session).create(
                sub=f"eval-e2e-{uuid.uuid4()}",
                email=None,
                name=None,
                picture=None,
            )
            user_id = user.id

            if case.document_fixture:
                retrieval_runner = RetrievalEvalRunner(
                    session=self.session,
                    settings=self.settings,
                    fixtures_root=self.fixtures_root,
                )
                await retrieval_runner._ingest_fixture(
                    user_id=user_id,
                    filename=case.document_fixture,
                )

            llm = _EvalLLMProvider(
                default_response=case.expected_answer or "",
                judge_mode=self.use_judge,
            )
            rag = self._rag_service(llm)
            response = await rag.ask(user_id=user_id, question=case.question or "")

            correctness = answer_matches(
                response.answer,
                case.expected_answer or "",
                case.expected_answer_match,
            )
            context = _extract_context(_messages_from_llm(llm))
            if self.use_judge:
                faithful_bool, hallucination = await self._run_judge(
                    context=context,
                    question=case.question or "",
                    answer=response.answer,
                    llm=llm,
                )
                faithful = 1.0 if faithful_bool else 0.0
            else:
                faithful = faithfulness_score(context, response.answer)
                hallucination = hallucination_detected(context, response.answer)

            passed = correctness and faithful >= 0.5 and not hallucination

            total_latency_ms = int((time.perf_counter() - start) * 1000)
            warning = None
            if not latency_within_target(
                "e2e",
                total_latency_ms,
                TARGET_RAG_RESPONSE_MS,
            ):
                warning = (
                    f"e2e latency {total_latency_ms}ms exceeds "
                    f"{TARGET_RAG_RESPONSE_MS}ms"
                )

            return EvalCaseResult(
                case_id=case.id,
                level="e2e",
                passed=passed,
                latency_ms=total_latency_ms,
                correctness=correctness,
                faithfulness=faithful,
                hallucination=hallucination,
                latency_warning=warning,
            )
        except Exception as exc:
            await self.session.rollback()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="e2e",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )

    def _rag_service(self, llm: LLMProvider) -> RAGService:
        retriever = Retriever(
            embedding_provider=_FakeEmbeddingProvider(),
            vector_store=PgVectorStore(self.session, self.settings),
            settings=self.settings,
        )
        return RAGService(
            retriever=retriever,
            context_builder=ContextBuilder(self.settings),
            prompt_builder=PromptBuilder(
                prompt_manager=self.prompt_manager,
                settings=self.settings,
            ),
            llm_provider=llm,
            settings=self.settings,
        )

    async def _run_judge(
        self,
        *,
        context: str,
        question: str,
        answer: str,
        llm: LLMProvider,
    ) -> tuple[bool, bool]:
        prompt = self.prompt_manager.render(
            "evaluation",
            "judge",
            "1",
            {"context": context, "question": question, "answer": answer},
        )
        completion = await llm.complete_chat(
            [ChatMessageSchema(role="user", content=prompt)],
            self.settings.openai_model,
            self.settings.default_temperature,
        )
        return _parse_judge_response(completion.content)


async def pgvector_available(session: AsyncSession) -> bool:
    """Return True when the pgvector extension is installed."""
    try:
        result = await session.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result == 1
    except Exception:
        return False


def _messages_from_llm(llm: LLMProvider) -> list[ChatMessageSchema]:
    if isinstance(llm, _EvalLLMProvider):
        return llm.last_messages
    return []


def _extract_context(messages: list[ChatMessageSchema]) -> str:
    for message in messages:
        if message.role != "user":
            continue
        match = re.search(r"Context:\s*(.+?)\n\nQuestion:", message.content, re.S)
        if match:
            return match.group(1).strip()
        return message.content
    return ""


def _parse_judge_response(content: str) -> tuple[bool, bool]:
    try:
        payload = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge response is not valid JSON: {content}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Judge response must be a JSON object.")
    faithful = payload.get("faithful")
    hallucination = payload.get("hallucination")
    if not isinstance(faithful, bool) or not isinstance(hallucination, bool):
        raise ValueError("Judge response must include boolean faithful/hallucination.")
    return faithful, hallucination


def _guess_mime_type(filename: str) -> str | None:
    lowered = filename.lower()
    if lowered.endswith(".txt"):
        return "text/plain"
    if lowered.endswith(".md"):
        return "text/markdown"
    if lowered.endswith(".pdf"):
        return "application/pdf"
    if lowered.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return None
