"""In-memory ingestion pipeline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.documents.pipeline import IngestionPipeline
from app.core.config import Settings

FIXTURES = Path(__file__).resolve().parent / "data" / "documents"


@pytest.mark.anyio
async def test_ingestion_pipeline_parse_and_chunk_without_db() -> None:
    settings = Settings(chunk_size=50, chunk_overlap=10, openai_api_key="test-key")
    pipeline = IngestionPipeline(settings)
    file_bytes = (FIXTURES / "sample.txt").read_bytes()

    parsed = await pipeline.parse(file_bytes, "sample.txt", "text/plain")
    chunks = pipeline.chunk(parsed)

    assert "Plain text fixture" in parsed.text
    assert chunks
    assert all(chunk.metadata["source"] == "sample.txt" for chunk in chunks)
