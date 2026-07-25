"""Tests for LLMQueryRewriter and pipeline rewrite wiring (Epic 02 Phase 5)."""

from __future__ import annotations

import logging
import uuid
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.prompts.manager import PromptManager, create_prompt_manager
from app.ai.rag.pipeline import DefaultAdvancedRetrievalPipeline
from app.ai.rag.retriever import Retriever
from app.ai.rag.rewrite import LLMQueryRewriter
from app.ai.rag.schemas import RetrievalRequest
from app.core.config import Settings
from app.providers.base import ProviderCompletion
from tests.fakes import FakeProvider


def _settings(
    *,
    advanced_rag_enabled: bool = True,
    query_rewrite_enabled: bool = True,
    llm_provider: str = "openai",
) -> Settings:
    return Settings(
        openai_api_key="test-key",
        llm_provider=llm_provider,
        advanced_rag_enabled=advanced_rag_enabled,
        query_rewrite_enabled=query_rewrite_enabled,
    )


def _chunk(*, content: str = "body", score: float = 0.5) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        metadata={"source": "doc.txt"},
        score=score,
    )


class _CapturingRetriever:
    def __init__(self, chunks: list[ScoredChunk]) -> None:
        self._chunks = chunks
        self.questions: list[str] = []
        self.call_count = 0

    async def retrieve(
        self,
        *,
        question: str,
        user_id: uuid.UUID,
        top_k: int | None = None,
        filters: object | None = None,
    ) -> list[ScoredChunk]:
        _ = (user_id, top_k, filters)
        self.call_count += 1
        self.questions.append(question)
        return list(self._chunks)


class _CountingRewriter:
    def __init__(self, rewritten: str) -> None:
        self.rewritten = rewritten
        self.calls: list[str] = []

    async def rewrite(self, query: str, *, user_id: uuid.UUID) -> str:
        _ = user_id
        self.calls.append(query)
        # Guard: rewritten output must never be fed back into the rewriter.
        if query.startswith("rewritten:"):
            raise AssertionError("rewritten query was fed back into rewriter")
        return self.rewritten


class _FailingProvider(FakeProvider):
    async def complete_chat(self, messages, model, temperature=0.7, *, max_tokens=None):
        _ = (messages, model, temperature, max_tokens)
        raise RuntimeError("provider down")


@pytest.mark.anyio
async def test_llm_query_rewriter_success_returns_normalized_text() -> None:
    provider = FakeProvider(response='  "refund policy timeline"  \nextra noise')
    rewriter = LLMQueryRewriter(
        provider=provider,
        prompt_manager=create_prompt_manager(),
        settings=_settings(),
    )
    result = await rewriter.rewrite(
        "when do I get my money back?", user_id=uuid.uuid4()
    )
    assert result == "refund policy timeline"
    assert provider.last_max_tokens == 128


@pytest.mark.anyio
async def test_llm_query_rewriter_failure_falls_back_to_original(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = "secret-question-should-not-log"
    caplog.set_level(logging.WARNING, logger="app.ai.rag.rewrite.query_rewriter")
    rewriter = LLMQueryRewriter(
        provider=_FailingProvider(),
        prompt_manager=create_prompt_manager(),
        settings=_settings(),
    )
    result = await rewriter.rewrite(original, user_id=uuid.uuid4())
    assert result == original

    failed_records = [
        record
        for record in caplog.records
        if record.name == "app.ai.rag.rewrite.query_rewriter"
        and getattr(record, "query_rewrite_failed", None) is True
    ]
    assert len(failed_records) == 1
    assert hasattr(failed_records[0], "query_rewrite_latency_ms")
    assert original not in caplog.text


@pytest.mark.anyio
async def test_llm_query_rewriter_empty_output_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = "keep me"
    caplog.set_level(logging.WARNING, logger="app.ai.rag.rewrite.query_rewriter")
    rewriter = LLMQueryRewriter(
        provider=FakeProvider(response="   \n  "),
        prompt_manager=create_prompt_manager(),
        settings=_settings(),
    )
    assert await rewriter.rewrite(original, user_id=uuid.uuid4()) == original
    assert any(
        getattr(record, "query_rewrite_failure_reason", None) == "empty_output"
        for record in caplog.records
    )


@pytest.mark.anyio
async def test_llm_query_rewriter_blank_query_skips_provider() -> None:
    provider = FakeProvider(response="should-not-be-used")
    provider.complete_chat = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("complete_chat must not be called for blank query")
    )
    rewriter = LLMQueryRewriter(
        provider=provider,
        prompt_manager=create_prompt_manager(),
        settings=_settings(),
    )
    assert await rewriter.rewrite("   ", user_id=uuid.uuid4()) == "   "


@pytest.mark.anyio
async def test_llm_query_rewriter_uses_prompt_manager_template() -> None:
    provider = FakeProvider(response="rewritten keywords")
    prompt_manager = create_prompt_manager()
    original_render = prompt_manager.render
    captured: list[tuple[str, str, str, dict[str, object]]] = []

    def _capture(
        category: str, name: str, version: str, variables: dict[str, object]
    ) -> str:
        captured.append((category, name, version, variables))
        return original_render(category, name, version, variables)

    prompt_manager.render = _capture  # type: ignore[method-assign]
    rewriter = LLMQueryRewriter(
        provider=provider,
        prompt_manager=prompt_manager,
        settings=_settings(),
    )
    question = "how does billing work?"
    await rewriter.rewrite(question, user_id=uuid.uuid4())
    assert captured == [("rag", "query_rewrite", "1", {"question": question})]


@pytest.mark.anyio
async def test_pipeline_rewrites_at_most_once_and_uses_rewritten_query() -> None:
    chunk = _chunk(content="hit")
    retriever = _CapturingRetriever([chunk])
    rewriter = _CountingRewriter("rewritten:billing policy")
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, retriever),
        query_rewriter=rewriter,
        settings=_settings(advanced_rag_enabled=True, query_rewrite_enabled=True),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="tell me about billing", user_id=uuid.uuid4())
    )
    assert rewriter.calls == ["tell me about billing"]
    assert retriever.questions == ["rewritten:billing policy"]
    assert len(result.candidates) == 1
    assert result.candidates[0].chunk.content == "hit"


@pytest.mark.anyio
async def test_pipeline_skips_rewrite_when_query_rewrite_disabled() -> None:
    retriever = _CapturingRetriever([_chunk()])
    rewriter = _CountingRewriter("rewritten:x")
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, retriever),
        query_rewriter=rewriter,
        settings=_settings(advanced_rag_enabled=True, query_rewrite_enabled=False),
    )
    await pipeline.retrieve(
        RetrievalRequest(question="original question", user_id=uuid.uuid4())
    )
    assert rewriter.calls == []
    assert retriever.questions == ["original question"]


@pytest.mark.anyio
async def test_pipeline_skips_rewrite_when_advanced_flag_off() -> None:
    retriever = _CapturingRetriever([_chunk()])
    rewriter = _CountingRewriter("rewritten:x")
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, retriever),
        query_rewriter=rewriter,
        settings=_settings(advanced_rag_enabled=False, query_rewrite_enabled=True),
    )
    await pipeline.retrieve(
        RetrievalRequest(question="original question", user_id=uuid.uuid4())
    )
    assert rewriter.calls == []
    assert retriever.questions == ["original question"]


@pytest.mark.anyio
async def test_pipeline_skips_rewrite_without_rewriter() -> None:
    retriever = _CapturingRetriever([_chunk()])
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, retriever),
        settings=_settings(advanced_rag_enabled=True, query_rewrite_enabled=True),
    )
    await pipeline.retrieve(
        RetrievalRequest(question="original question", user_id=uuid.uuid4())
    )
    assert retriever.questions == ["original question"]


@pytest.mark.anyio
async def test_pipeline_uses_original_when_rewriter_returns_original_on_failure() -> (
    None
):
    """LLMQueryRewriter failure path: original query reaches the retriever."""
    original = "fallback question"
    retriever = _CapturingRetriever([_chunk()])
    rewriter = LLMQueryRewriter(
        provider=_FailingProvider(),
        prompt_manager=cast(PromptManager, create_prompt_manager()),
        settings=_settings(),
    )
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, retriever),
        query_rewriter=rewriter,
        settings=_settings(),
    )
    await pipeline.retrieve(RetrievalRequest(question=original, user_id=uuid.uuid4()))
    assert retriever.questions == [original]


@pytest.mark.anyio
async def test_llm_query_rewriter_success_log_has_no_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "super-secret-user-question"
    caplog.set_level(logging.INFO, logger="app.ai.rag.rewrite.query_rewriter")
    rewriter = LLMQueryRewriter(
        provider=FakeProvider(response="safe keywords"),
        prompt_manager=create_prompt_manager(),
        settings=_settings(),
    )
    await rewriter.rewrite(secret, user_id=uuid.uuid4())
    success = [
        record
        for record in caplog.records
        if record.name == "app.ai.rag.rewrite.query_rewriter"
        and getattr(record, "query_rewrite_failed", None) is False
    ]
    assert len(success) == 1
    assert hasattr(success[0], "query_rewrite_latency_ms")
    assert secret not in caplog.text
    assert "safe keywords" not in caplog.text


@pytest.mark.anyio
async def test_complete_chat_receives_temperature_zero() -> None:
    provider = FakeProvider(response="ok")
    captured: dict[str, object] = {}

    async def _capture(
        messages,
        model,
        temperature=0.7,
        *,
        max_tokens=None,
    ) -> ProviderCompletion:
        captured["temperature"] = temperature
        captured["model"] = model
        captured["max_tokens"] = max_tokens
        return await FakeProvider.complete_chat(
            provider, messages, model, temperature, max_tokens=max_tokens
        )

    provider.complete_chat = _capture  # type: ignore[method-assign]
    rewriter = LLMQueryRewriter(
        provider=provider,
        prompt_manager=create_prompt_manager(),
        settings=_settings(llm_provider="openai"),
    )
    await rewriter.rewrite("q", user_id=uuid.uuid4())
    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == 128
    assert captured["model"] == Settings().openai_model
