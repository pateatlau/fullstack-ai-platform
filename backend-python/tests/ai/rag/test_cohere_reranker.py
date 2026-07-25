"""Tests for CohereReranker httpx adapter (Epic 02 Phase 6)."""

from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.rerank import CohereReranker
from app.ai.rag.schemas import RetrievedCandidate
from app.core.config import Settings


def _settings(
    *,
    cohere_api_key: str | None = "test-cohere-key",
    rerank_model: str = "rerank-v3.5",
    rerank_timeout_ms: int = 1500,
) -> Settings:
    return Settings(
        openai_api_key="test-key",
        cohere_api_key=cohere_api_key,
        rerank_model=rerank_model,
        rerank_timeout_ms=rerank_timeout_ms,
    )


def _candidate(
    *,
    content: str,
    final_score: float,
    parent: str | None = None,
) -> RetrievedCandidate:
    chunk = ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        metadata={"source": "doc.txt"},
        score=final_score,
    )
    return RetrievedCandidate(
        chunk=chunk,
        parent=parent,
        metadata=dict(chunk.metadata),
        final_score=final_score,
        dense_score=final_score,
        rrf_score=final_score,
    )


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        status_code: int = 200,
        body: dict[str, object] | bytes | None = None,
        raise_timeout: bool = False,
    ) -> None:
        self.status_code = status_code
        self.body: dict[str, object] | bytes = (
            body if body is not None else {"results": []}
        )
        self.raise_timeout = raise_timeout
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_timeout:
            raise httpx.ReadTimeout("timed out", request=request)
        if isinstance(self.body, dict):
            return httpx.Response(
                self.status_code,
                json=self.body,
                request=request,
            )
        return httpx.Response(
            self.status_code,
            content=self.body,
            request=request,
        )


@pytest.mark.anyio
async def test_cohere_reranker_success_reorders_and_sets_scores() -> None:
    candidates = [
        _candidate(content="alpha", final_score=0.9),
        _candidate(content="beta", final_score=0.8),
        _candidate(content="gamma", final_score=0.7),
    ]
    transport = _RecordingTransport(
        body={
            "results": [
                {"index": 2, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.55},
            ]
        }
    )
    reranker = CohereReranker(settings=_settings(), transport=transport)
    out = await reranker.rerank("what is gamma?", candidates, top_n=2)

    assert [c.chunk.content for c in out] == ["gamma", "alpha"]
    assert out[0].rerank_score == 0.95
    assert out[0].final_score == 0.95
    assert out[1].rerank_score == 0.55
    assert out[1].final_score == 0.55
    # Intermediate scores remain diagnostic (unchanged).
    assert out[0].rrf_score == 0.7
    assert out[1].rrf_score == 0.9

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url.path == "/v2/rerank"
    assert request.headers["Authorization"] == "Bearer test-cohere-key"
    payload = json.loads(request.content.decode())
    assert payload == {
        "model": "rerank-v3.5",
        "query": "what is gamma?",
        "documents": ["alpha", "beta", "gamma"],
        "top_n": 2,
    }


@pytest.mark.anyio
async def test_cohere_reranker_uses_parent_text_when_present() -> None:
    candidates = [
        _candidate(content="child", final_score=0.5, parent="parent block text"),
    ]
    transport = _RecordingTransport(
        body={"results": [{"index": 0, "relevance_score": 0.88}]}
    )
    reranker = CohereReranker(settings=_settings(), transport=transport)
    await reranker.rerank("q", candidates, top_n=1)
    payload = json.loads(transport.requests[0].content.decode())
    assert payload["documents"] == ["parent block text"]


@pytest.mark.anyio
async def test_cohere_reranker_timeout_keeps_pre_rerank_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [
        _candidate(content="first", final_score=0.9),
        _candidate(content="second", final_score=0.8),
    ]
    transport = _RecordingTransport(raise_timeout=True)
    caplog.set_level(logging.WARNING, logger="app.ai.rag.rerank.cohere")
    reranker = CohereReranker(
        settings=_settings(rerank_timeout_ms=10),
        transport=transport,
    )
    out = await reranker.rerank("secret-query-text", candidates, top_n=2)

    assert [c.chunk.content for c in out] == ["first", "second"]
    assert [c.final_score for c in out] == [0.9, 0.8]
    assert all(c.rerank_score is None for c in out)
    failed = [
        record
        for record in caplog.records
        if getattr(record, "rerank_failed", None) is True
    ]
    assert len(failed) == 1
    assert getattr(failed[0], "rerank_failure_reason", None) == "timeout"
    assert "secret-query-text" not in caplog.text
    assert "first" not in caplog.text


@pytest.mark.anyio
async def test_cohere_reranker_http_error_keeps_pre_rerank_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [_candidate(content="only", final_score=0.4)]
    transport = _RecordingTransport(status_code=500, body={"message": "down"})
    caplog.set_level(logging.WARNING, logger="app.ai.rag.rerank.cohere")
    reranker = CohereReranker(settings=_settings(), transport=transport)
    out = await reranker.rerank("q", candidates, top_n=1)
    assert out == candidates
    assert any(
        getattr(record, "rerank_failure_reason", None) == "http_error"
        for record in caplog.records
    )


@pytest.mark.anyio
async def test_cohere_reranker_missing_api_key_keeps_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [_candidate(content="a", final_score=0.1)]
    transport = _RecordingTransport(
        body={"results": [{"index": 0, "relevance_score": 1.0}]}
    )
    caplog.set_level(logging.WARNING, logger="app.ai.rag.rerank.cohere")
    reranker = CohereReranker(
        settings=_settings(cohere_api_key=None),
        transport=transport,
    )
    out = await reranker.rerank("q", candidates, top_n=1)
    assert out == candidates
    assert transport.requests == []
    assert any(
        getattr(record, "rerank_failure_reason", None) == "missing_api_key"
        for record in caplog.records
    )


@pytest.mark.anyio
async def test_cohere_reranker_empty_candidates_skips_http() -> None:
    transport = _RecordingTransport(
        body={"results": [{"index": 0, "relevance_score": 1.0}]}
    )
    reranker = CohereReranker(settings=_settings(), transport=transport)
    assert await reranker.rerank("q", [], top_n=5) == []
    assert transport.requests == []


@pytest.mark.anyio
async def test_cohere_reranker_invalid_response_keeps_order() -> None:
    candidates = [_candidate(content="a", final_score=0.2)]
    transport = _RecordingTransport(body={"results": []})
    # Empty results list is treated as an invalid/unusable rerank payload.
    reranker = CohereReranker(settings=_settings(), transport=transport)
    out = await reranker.rerank("q", candidates, top_n=1)
    assert out == candidates


@pytest.mark.anyio
async def test_cohere_reranker_non_json_body_keeps_order() -> None:
    candidates = [_candidate(content="a", final_score=0.2)]
    transport = _RecordingTransport(body=b"not-json")
    reranker = CohereReranker(settings=_settings(), transport=transport)
    out = await reranker.rerank("q", candidates, top_n=1)
    assert out == candidates


@pytest.mark.anyio
async def test_cohere_reranker_respects_top_n() -> None:
    candidates = [
        _candidate(content="a", final_score=0.9),
        _candidate(content="b", final_score=0.8),
        _candidate(content="c", final_score=0.7),
    ]
    transport = _RecordingTransport(
        body={
            "results": [
                {"index": 1, "relevance_score": 0.99},
                {"index": 2, "relevance_score": 0.88},
                {"index": 0, "relevance_score": 0.11},
            ]
        }
    )
    reranker = CohereReranker(settings=_settings(), transport=transport)
    out = await reranker.rerank("q", candidates, top_n=1)
    assert [c.chunk.content for c in out] == ["b"]
    payload = json.loads(transport.requests[0].content.decode())
    assert payload["top_n"] == 1


@pytest.mark.anyio
async def test_cohere_module_has_no_cohere_sdk_import() -> None:
    import inspect

    import app.ai.rag.rerank.cohere as cohere_mod

    source = inspect.getsource(cohere_mod)
    assert "import cohere" not in source
    assert "from cohere" not in source
    assert "import httpx" in source
