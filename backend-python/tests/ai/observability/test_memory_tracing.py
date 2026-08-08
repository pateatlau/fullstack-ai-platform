"""Memory retrieval/extraction span instrumentation tests."""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.manager import MemoryManager
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings
from app.schemas.chat import ChatMessageSchema
from tests.ai.memory.test_manager import FakeMemoryProvider, _FakeEmbeddingProvider

pytestmark = pytest.mark.anyio

_NOW = datetime.datetime.now(datetime.timezone.utc)
_DIMENSIONS = 8


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


class _AlwaysOwnsSessionChecker:
    async def user_owns_session(
        self, *, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> bool:
        del user_id, session_id
        return True


class _ExtractingLLMProvider:
    async def complete_chat(self, *args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs
        from app.providers.base import ProviderCompletion

        return ProviderCompletion(
            content='[{"content":"User prefers dark mode.","memory_type":"user","confidence":0.9}]',
            usage=None,
        )


def _manager(provider: FakeMemoryProvider) -> MemoryManager:
    return MemoryManager(
        provider=provider,  # type: ignore[arg-type]
        settings=Settings(
            openai_api_key="test-key",
            memory_enabled=True,
            memory_extraction_enabled=True,
            embedding_dimensions=_DIMENSIONS,
        ),
        embedding_provider=_FakeEmbeddingProvider(),
        prompt_manager=create_prompt_manager(),
        session_ownership_checker=_AlwaysOwnsSessionChecker(),  # type: ignore[arg-type]
    )


async def test_retrieve_context_emits_memory_retrieve_span_without_content(
    memory_exporter: InMemorySpanExporter,
) -> None:
    owner_id = uuid.uuid4()
    provider = FakeMemoryProvider()
    provider.existing_records = [
        MemoryRecord(
            id=uuid.uuid4(),
            memory_type=MemoryType.USER,
            scope=MemoryScope.USER,
            owner_id=owner_id,
            content="Secret preference text.",
            embedding=[0.1] * _DIMENSIONS,
            confidence=0.9,
            quality_score=0.9,
            created_at=_NOW,
            updated_at=_NOW,
            lifecycle_state=LifecycleState.ACTIVE,
            source="test",
        ),
    ]
    manager = _manager(provider)

    await manager.retrieve_context(
        owner_id=owner_id,
        session_id=None,
        messages=[ChatMessageSchema(role="user", content="What do I prefer?")],
    )

    spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "memory.retrieve"
    ]
    assert len(spans) == 1
    attributes = dict(spans[0].attributes or {})
    assert attributes["retrieved_count"] == 1
    assert isinstance(attributes["latency_ms"], int)
    assert all("Secret" not in str(value) for value in attributes.values())


async def test_extraction_pipeline_emits_memory_extract_span(
    memory_exporter: InMemorySpanExporter,
) -> None:
    owner_id = uuid.uuid4()
    provider = FakeMemoryProvider()
    manager = _manager(provider)
    done = asyncio.Event()

    async def _pipeline(**kwargs):  # noqa: ANN003
        await manager._run_extraction_pipeline(**kwargs)
        done.set()

    await asyncio.create_task(
        _pipeline(
            owner_id=owner_id,
            session_id=None,
            messages=[ChatMessageSchema(role="user", content="Remember my theme.")],
            provider=_ExtractingLLMProvider(),  # type: ignore[arg-type]
            provider_name="openai",
            model="gpt-test",
        )
    )
    await asyncio.wait_for(done.wait(), timeout=2.0)

    spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "memory.extract"
    ]
    assert len(spans) == 1
    attributes = dict(spans[0].attributes or {})
    retrieved_count = attributes["retrieved_count"]
    assert isinstance(retrieved_count, int)
    assert retrieved_count >= 0
    assert isinstance(attributes["latency_ms"], int)
    assert all("theme" not in str(value).lower() for value in attributes.values())
