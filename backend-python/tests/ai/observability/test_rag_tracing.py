"""RAG retriever span instrumentation tests."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import MetadataFilter
from app.core.config import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _reset_tracer_registry() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    settings = Settings(openai_api_key="test-key", observability_enabled=True)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    return exporter


def _chunk(content: str = "secret chunk body") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        metadata={"source": "doc.txt"},
    )


async def test_retriever_emits_rag_span_with_counts(
    memory_exporter: InMemorySpanExporter,
) -> None:
    user_id = uuid.uuid4()
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1, 0.2]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[_chunk(), _chunk("other")])
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=Settings(openai_api_key="test-key", rag_top_k=7),
    )

    results = await retriever.retrieve(question="find docs", user_id=user_id)

    assert len(results) == 2
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "rag.retrieve"
    attributes = dict(spans[0].attributes or {})
    assert attributes["top_k"] == 7
    assert attributes["retrieved_count"] == 2
    assert isinstance(attributes["latency_ms"], int)
    assert attributes["latency_ms"] >= 0
    assert all("secret" not in str(value) for value in attributes.values())


async def test_retriever_unsatisfiable_filter_emits_zero_count_span(
    memory_exporter: InMemorySpanExporter,
) -> None:
    embed = AsyncMock()
    store = AsyncMock()
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=Settings(openai_api_key="test-key", rag_top_k=5),
    )

    results = await retriever.retrieve(
        question="q",
        user_id=uuid.uuid4(),
        filters=MetadataFilter(document_ids=frozenset()),
    )

    assert results == []
    embed.embed_texts.assert_not_called()
    attributes = dict(memory_exporter.get_finished_spans()[0].attributes or {})
    assert attributes["retrieved_count"] == 0
    assert attributes["top_k"] == 5


async def test_rag_telemetry_failure_is_fail_open(
    memory_exporter: InMemorySpanExporter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    broken_tracer = MagicMock()
    broken_tracer.start_span.side_effect = RuntimeError("telemetry down")
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[_chunk()])
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=Settings(openai_api_key="test-key"),
    )

    with patch(
        "app.ai.observability.tracing.spans.get_tracer",
        return_value=broken_tracer,
    ):
        results = await retriever.retrieve(question="q", user_id=uuid.uuid4())

    assert len(results) == 1
    assert any(
        "Observability span setup failed" in record.message for record in caplog.records
    )
    assert len(memory_exporter.get_finished_spans()) == 0
