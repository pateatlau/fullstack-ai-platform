"""Unit and integration tests for RAGService orchestration."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.prompts.manager import create_prompt_manager
from app.ai.rag.context_builder import BuiltContext, ContextBuilder
from app.ai.rag.prompt_builder import BuiltPrompt, PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import RAGResponse
from app.ai.rag.service import EMPTY_CORPUS_MESSAGE, RAGService
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings
from app.db.identity import SqlUserStore
from app.providers.base import ProviderCompletion
from app.schemas.chat import ChatMessageSchema
from app.services.knowledge_service import KnowledgeService
from tests.fakes import FakeProvider

FIXTURES = Path(__file__).resolve().parent / "data" / "documents"
DIMENSIONS = 1536
FIXTURE_TEXT = "Plain text fixture content."


class _FakeEmbeddingProvider:
    dimensions = DIMENSIONS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index % DIMENSIONS), 0.0] + [0.0] * (DIMENSIONS - 2)
            for index, _ in enumerate(texts)
        ]


class _CapturingLLMProvider(FakeProvider):
    def __init__(self, response: str = "Answer from LLM.") -> None:
        super().__init__(response=response)
        self.messages: list[ChatMessageSchema] = []
        self.complete_chat_calls = 0

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> ProviderCompletion:
        self.complete_chat_calls += 1
        self.messages = list(messages)
        return await super().complete_chat(messages, model, temperature)


def _chunk(*, index: int, content: str, score: float) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=index,
        content=content,
        metadata={"source": "fixture.txt"},
        score=score,
    )


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "llm_provider": "openai",
        "openai_model": "gpt-4o-mini",
        "default_temperature": 0.7,
        "rag_top_k": 5,
        "rag_context_max_chars": 8000,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _service(
    *,
    retriever: Retriever | None = None,
    context_builder: ContextBuilder | None = None,
    prompt_builder: PromptBuilder | None = None,
    llm_provider: _CapturingLLMProvider | None = None,
    settings: Settings | None = None,
) -> tuple[
    RAGService, _CapturingLLMProvider, AsyncMock, AsyncMock, MagicMock, Settings
]:
    resolved_settings = settings or _settings()
    embed = AsyncMock()
    store = AsyncMock()
    mock_retriever = retriever or Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=resolved_settings,
    )
    mock_context_builder = context_builder or ContextBuilder(resolved_settings)
    prompt_manager = MagicMock()
    prompt_manager.render.return_value = "rendered prompt"
    mock_prompt_builder = prompt_builder or PromptBuilder(
        prompt_manager=prompt_manager,
        settings=resolved_settings,
    )
    llm = llm_provider or _CapturingLLMProvider()
    service = RAGService(
        retriever=mock_retriever,
        context_builder=mock_context_builder,
        prompt_builder=mock_prompt_builder,
        llm_provider=llm,
        settings=resolved_settings,
    )
    return service, llm, embed, store, prompt_manager, resolved_settings


async def _pgvector_available(session) -> bool:
    try:
        result = await session.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result == 1
    except Exception:
        return False


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"rag-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _knowledge_service(session) -> KnowledgeService:
    settings = _settings()
    pipeline = IngestionPipeline(settings, embedding_provider=_FakeEmbeddingProvider())
    vector_store = PgVectorStore(session, settings)
    return KnowledgeService(
        session=session,
        settings=settings,
        pipeline=pipeline,
        vector_store=vector_store,
    )


def _integration_rag_service(
    session,
    *,
    llm_provider: _CapturingLLMProvider | None = None,
) -> RAGService:
    settings = _settings()
    retriever = Retriever(
        embedding_provider=_FakeEmbeddingProvider(),
        vector_store=PgVectorStore(session, settings),
        settings=settings,
    )
    return RAGService(
        retriever=retriever,
        context_builder=ContextBuilder(settings),
        prompt_builder=PromptBuilder(
            prompt_manager=create_prompt_manager(),
            settings=settings,
        ),
        llm_provider=llm_provider or _CapturingLLMProvider(),
        settings=settings,
    )


@pytest.fixture
async def pgvector_session(db_session):
    if not await _pgvector_available(db_session):
        pytest.skip("pgvector extension not available — run alembic upgrade head")
    yield db_session


@pytest.mark.anyio
async def test_rag_service_ask_happy_path() -> None:
    user_id = uuid.uuid4()
    chunks = [_chunk(index=0, content="alpha facts", score=0.95)]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=chunks)
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    llm = _CapturingLLMProvider(response="The answer is alpha.")
    service, llm, _, _, _, _ = _service(
        retriever=retriever,
        llm_provider=llm,
        settings=settings,
    )

    response = await service.ask(user_id=user_id, question="What is alpha?")

    assert isinstance(response, RAGResponse)
    assert response.answer == "The answer is alpha."
    assert len(response.retrieved_chunks) == 1
    assert response.retrieved_chunks[0].score == 0.95
    assert response.truncated is False
    assert response.provider == "openai"
    assert response.model == "gpt-4o-mini"
    assert llm.complete_chat_calls == 1


@pytest.mark.anyio
async def test_rag_service_orchestration_order() -> None:
    user_id = uuid.uuid4()
    chunks = [_chunk(index=0, content="context body", score=0.9)]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=chunks)
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    context_builder = MagicMock(spec=ContextBuilder)
    context_builder.build.return_value = BuiltContext(
        text="numbered context",
        included_chunks=chunks,
        truncated=False,
    )
    prompt_builder = MagicMock(spec=PromptBuilder)
    prompt_builder.build.return_value = BuiltPrompt(
        system_prompt=None,
        user_prompt="rendered prompt with context",
    )
    llm = _CapturingLLMProvider()
    service = RAGService(
        retriever=retriever,
        context_builder=context_builder,
        prompt_builder=prompt_builder,
        llm_provider=llm,
        settings=settings,
    )

    await service.ask(user_id=user_id, question="What is in the doc?")

    embed.embed_texts.assert_awaited_once()
    context_builder.build.assert_called_once_with(chunks)
    prompt_builder.build.assert_called_once()
    assert llm.complete_chat_calls == 1
    assert llm.messages[0].role == "user"
    assert llm.messages[0].content == "rendered prompt with context"


@pytest.mark.anyio
async def test_rag_service_empty_retrieval_skips_llm() -> None:
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[])
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    llm = _CapturingLLMProvider()
    service, llm, _, _, _, _ = _service(
        retriever=retriever,
        llm_provider=llm,
        settings=settings,
    )

    response = await service.ask(user_id=uuid.uuid4(), question="anything?")

    assert response.answer == EMPTY_CORPUS_MESSAGE
    assert response.retrieved_chunks == []
    assert response.truncated is False
    assert response.llm_latency_ms == 0
    assert llm.complete_chat_calls == 0


@pytest.mark.anyio
async def test_rag_service_prompt_template_override_passed_to_prompt_builder() -> None:
    chunks = [_chunk(index=0, content="x", score=1.0)]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=chunks)
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    prompt_builder = MagicMock(spec=PromptBuilder)
    prompt_builder.build.return_value = BuiltPrompt(
        system_prompt=None,
        user_prompt="custom template output",
    )
    llm = _CapturingLLMProvider()
    service = RAGService(
        retriever=retriever,
        context_builder=ContextBuilder(settings),
        prompt_builder=prompt_builder,
        llm_provider=llm,
        settings=settings,
    )

    await service.ask(
        user_id=uuid.uuid4(),
        question="q",
        prompt_template="rag/custom/v2",
    )

    prompt_builder.build.assert_called_once()
    assert prompt_builder.build.call_args.kwargs["template_ref"] == "rag/custom/v2"


@pytest.mark.anyio
async def test_rag_service_instructions_passed_to_prompt_builder() -> None:
    chunks = [_chunk(index=0, content="x", score=1.0)]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=chunks)
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    prompt_builder = MagicMock(spec=PromptBuilder)
    prompt_builder.build.return_value = BuiltPrompt(
        system_prompt=None,
        user_prompt="with instructions",
    )
    llm = _CapturingLLMProvider()
    service = RAGService(
        retriever=retriever,
        context_builder=ContextBuilder(settings),
        prompt_builder=prompt_builder,
        llm_provider=llm,
        settings=settings,
    )

    await service.ask(
        user_id=uuid.uuid4(),
        question="q",
        instructions="Be concise.",
    )

    assert prompt_builder.build.call_args.kwargs["instructions"] == "Be concise."


@pytest.mark.anyio
async def test_rag_service_truncation_reflected_in_response() -> None:
    chunks = [
        _chunk(index=0, content="keep", score=0.95),
        _chunk(index=1, content="drop", score=0.50),
    ]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=chunks)
    settings = _settings(rag_context_max_chars=30)
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    llm = _CapturingLLMProvider()
    service, _, _, _, _, _ = _service(
        retriever=retriever,
        llm_provider=llm,
        settings=settings,
    )

    response = await service.ask(user_id=uuid.uuid4(), question="q")

    assert response.truncated is True
    assert len(response.retrieved_chunks) == 1
    assert response.retrieved_chunks[0].score == 0.95


@pytest.mark.anyio
async def test_rag_service_retrieved_chunks_metadata_populated() -> None:
    chunk = _chunk(index=3, content="secret body", score=0.88)
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[chunk])
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    llm = _CapturingLLMProvider()
    service, _, _, _, _, _ = _service(
        retriever=retriever,
        llm_provider=llm,
        settings=settings,
    )

    response = await service.ask(user_id=uuid.uuid4(), question="q")

    meta = response.retrieved_chunks[0]
    assert meta.chunk_id == chunk.chunk_id
    assert meta.document_id == chunk.document_id
    assert meta.chunk_index == 3
    assert meta.score == 0.88


@pytest.mark.anyio
async def test_rag_service_emits_metrics_log_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.rag.service")
    chunks = [_chunk(index=0, content="x", score=0.75)]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=chunks)
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    llm = _CapturingLLMProvider()
    service, _, _, _, _, _ = _service(
        retriever=retriever,
        llm_provider=llm,
        settings=settings,
    )

    await service.ask(user_id=uuid.uuid4(), question="metrics test")

    records = [
        record for record in caplog.records if record.name == "app.ai.rag.service"
    ]
    assert records
    record = records[-1]
    assert getattr(record, "rag_requests_total") == 1
    assert getattr(record, "rag_request_duration_ms") is not None
    assert getattr(record, "retrieval_count") == 1
    assert getattr(record, "included_count") == 1
    assert getattr(record, "top_score") == 0.75
    assert getattr(record, "truncated") is False
    assert getattr(record, "retrieval_latency_ms") is not None
    assert getattr(record, "llm_latency_ms") is not None


@pytest.mark.anyio
async def test_rag_service_logs_no_sensitive_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.rag.service")
    secret_question = "classified-user-question"
    secret_content = "classified-chunk-body"
    secret_answer = "classified-llm-response"
    chunks = [_chunk(index=0, content=secret_content, score=0.9)]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=chunks)
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    llm = _CapturingLLMProvider(response=secret_answer)
    service, _, _, _, _, _ = _service(
        retriever=retriever,
        llm_provider=llm,
        settings=settings,
    )

    await service.ask(user_id=uuid.uuid4(), question=secret_question)

    assert secret_question not in caplog.text
    assert secret_content not in caplog.text
    assert secret_answer not in caplog.text


@pytest.mark.anyio
async def test_rag_service_completes_within_eight_second_target() -> None:
    chunks = [_chunk(index=0, content="x", score=1.0)]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=chunks)
    settings = _settings()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    llm = _CapturingLLMProvider()
    service, _, _, _, _, _ = _service(
        retriever=retriever,
        llm_provider=llm,
        settings=settings,
    )

    start = time.perf_counter()
    await service.ask(user_id=uuid.uuid4(), question="speed test")
    elapsed = time.perf_counter() - start

    assert elapsed < 8.0


@pytest.mark.anyio
async def test_rag_service_integration_ingest_fixture_then_ask(
    pgvector_session,
) -> None:
    user_id = await _make_user(pgvector_session)
    knowledge = _knowledge_service(pgvector_session)
    llm = _CapturingLLMProvider(response="Based on the fixture.")
    rag = _integration_rag_service(pgvector_session, llm_provider=llm)

    await knowledge.ingest_document(
        user_id=user_id,
        file_bytes=(FIXTURES / "sample.txt").read_bytes(),
        filename="sample.txt",
        mime_type="text/plain",
    )

    response = await rag.ask(
        user_id=user_id,
        question="What does the plain text fixture say?",
    )

    assert response.answer == "Based on the fixture."
    assert response.retrieved_chunks
    assert llm.messages
    assert FIXTURE_TEXT in llm.messages[0].content


@pytest.mark.anyio
async def test_rag_service_integration_empty_corpus_graceful_response(
    pgvector_session,
) -> None:
    user_id = await _make_user(pgvector_session)
    llm = _CapturingLLMProvider()
    rag = _integration_rag_service(pgvector_session, llm_provider=llm)

    response = await rag.ask(user_id=user_id, question="Anything at all?")

    assert response.answer == EMPTY_CORPUS_MESSAGE
    assert response.retrieved_chunks == []
    assert llm.complete_chat_calls == 0


@pytest.mark.anyio
async def test_rag_service_integration_owner_isolation(pgvector_session) -> None:
    owner_id = await _make_user(pgvector_session)
    other_id = await _make_user(pgvector_session)
    knowledge = _knowledge_service(pgvector_session)
    llm = _CapturingLLMProvider()
    rag = _integration_rag_service(pgvector_session, llm_provider=llm)

    await knowledge.ingest_document(
        user_id=owner_id,
        file_bytes=(FIXTURES / "sample.txt").read_bytes(),
        filename="sample.txt",
        mime_type="text/plain",
    )

    response = await rag.ask(
        user_id=other_id,
        question="What does the plain text fixture say?",
    )

    assert response.answer == EMPTY_CORPUS_MESSAGE
    assert response.retrieved_chunks == []
    assert llm.complete_chat_calls == 0
