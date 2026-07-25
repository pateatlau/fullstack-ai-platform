"""In-memory ingestion pipeline tests."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Protocol

import pytest

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.documents.schemas import DocumentChunk
from app.core.config import Settings

FIXTURES = Path(__file__).resolve().parent / "data" / "documents"


class FakeEmbeddingProvider(Protocol):
    dimensions: int

    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class _FakeEmbeddingProvider:
    def __init__(self, dimensions: int = 8) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [float(index), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            for index, _ in enumerate(texts)
        ]


@pytest.mark.anyio
async def test_ingestion_pipeline_parse_and_chunk_without_db() -> None:
    settings = Settings(
        chunk_size=50,
        chunk_overlap=10,
        advanced_rag_enabled=False,
        openai_api_key="test-key",
    )
    pipeline = IngestionPipeline(settings)
    file_bytes = (FIXTURES / "sample.txt").read_bytes()

    parsed = await pipeline.parse(file_bytes, "sample.txt", "text/plain")
    chunks = pipeline.chunk(parsed)

    assert "Plain text fixture" in parsed.text
    assert chunks
    assert all(chunk.metadata["source"] == "sample.txt" for chunk in chunks)
    assert all(chunk.embedding is None for chunk in chunks)
    assert all(chunk.metadata.get("chunk_kind") is None for chunk in chunks)


@pytest.mark.anyio
async def test_ingestion_pipeline_flag_on_uses_parent_child_chunker() -> None:
    settings = Settings(
        advanced_rag_enabled=True,
        child_chunk_size=40,
        child_chunk_overlap=8,
        parent_chunk_size=80,
        parent_chunk_overlap=10,
        openai_api_key="test-key",
    )
    pipeline = IngestionPipeline(settings)
    file_bytes = (FIXTURES / "sample.txt").read_bytes()

    parsed = await pipeline.parse(file_bytes, "sample.txt", "text/plain")
    chunks = pipeline.chunk(parsed)

    kinds = {chunk.metadata.get("chunk_kind") for chunk in chunks}
    assert kinds == {"parent", "child"}
    children = [c for c in chunks if c.metadata.get("chunk_kind") == "child"]
    parents = [c for c in chunks if c.metadata.get("chunk_kind") == "parent"]
    assert children and parents
    parent_ids = {str(p.id) for p in parents}
    assert all(c.metadata["parent_id"] in parent_ids for c in children)


@pytest.mark.anyio
async def test_ingestion_pipeline_embed_attaches_vectors_and_preserves_metadata() -> (
    None
):
    settings = Settings(openai_api_key="test-key")
    provider = _FakeEmbeddingProvider(dimensions=8)
    pipeline = IngestionPipeline(settings, embedding_provider=provider)
    chunks = [
        DocumentChunk(
            chunk_index=0,
            content="first chunk",
            metadata={"source": "sample.txt", "page": 1},
        ),
        DocumentChunk(
            chunk_index=1,
            content="second chunk",
            metadata={"source": "sample.txt", "page": 2},
        ),
    ]

    embedded = await pipeline.embed(chunks)

    assert provider.calls == [["first chunk", "second chunk"]]
    assert len(embedded) == 2
    for original, result in zip(chunks, embedded, strict=True):
        assert result.chunk_index == original.chunk_index
        assert result.content == original.content
        assert result.metadata == original.metadata
        assert result.embedding is not None
        assert len(result.embedding) == provider.dimensions


@pytest.mark.anyio
async def test_ingestion_pipeline_embed_without_provider_raises() -> None:
    pipeline = IngestionPipeline(Settings(openai_api_key="test-key"))
    chunks = [DocumentChunk(chunk_index=0, content="orphan")]

    with pytest.raises(RuntimeError, match="Embedding provider is not configured"):
        await pipeline.embed(chunks)


@pytest.mark.anyio
async def test_ingestion_pipeline_parse_chunk_embed_in_memory() -> None:
    settings = Settings(chunk_size=50, chunk_overlap=10, openai_api_key="test-key")
    provider = _FakeEmbeddingProvider(dimensions=8)
    pipeline = IngestionPipeline(settings, embedding_provider=provider)
    file_bytes = (FIXTURES / "sample.txt").read_bytes()

    chunks = await pipeline.parse_chunk_embed(
        file_bytes,
        "sample.txt",
        "text/plain",
    )

    assert chunks
    assert provider.calls
    assert all(chunk.embedding is not None for chunk in chunks)
    assert all(len(chunk.embedding or []) == provider.dimensions for chunk in chunks)


@pytest.mark.anyio
async def test_ingestion_pipeline_embed_logs_latency_and_count_not_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.documents.pipeline")
    settings = Settings(openai_api_key="test-key")
    provider = _FakeEmbeddingProvider(dimensions=8)
    pipeline = IngestionPipeline(settings, embedding_provider=provider)
    secret = "classified-chunk-body"
    chunks = [DocumentChunk(chunk_index=0, content=secret)]

    await pipeline.embed(chunks)

    records = [
        record
        for record in caplog.records
        if record.name == "app.ai.documents.pipeline"
        and "Document chunks embedded" in record.message
    ]
    assert len(records) == 1
    assert getattr(records[0], "embedding_latency_ms") is not None
    assert getattr(records[0], "text_count") == 1
    assert secret not in caplog.text


@pytest.mark.anyio
async def test_chunk_without_embed_still_valid() -> None:
    chunk = DocumentChunk(chunk_index=0, content="text only")
    assert chunk.embedding is None
    updated = replace(chunk, embedding=[0.1, 0.2])
    assert updated.embedding == [0.1, 0.2]
