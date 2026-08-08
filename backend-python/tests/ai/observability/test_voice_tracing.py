"""Voice session span instrumentation tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.voice.config import VoiceConfig
from app.ai.voice.session import VoiceSessionManager
from app.core.caller import CallerContext
from app.core.config import Settings
from tests.fakes import FakeChatStore

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


@pytest.fixture
def chat_store() -> FakeChatStore:
    return FakeChatStore()


@pytest.fixture
def manager(
    memory_exporter: InMemorySpanExporter,
    chat_store: FakeChatStore,
) -> VoiceSessionManager:
    del memory_exporter
    return VoiceSessionManager(VoiceConfig(), chat_store)


async def test_voice_session_span_records_duration_and_status_on_teardown(
    memory_exporter: InMemorySpanExporter,
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    chat_session = await chat_store.create_session(user_id=user_id, title="voice")
    voice_session = await manager.create(
        chat_session.id, CallerContext.for_user(user_id)
    )

    assert len(memory_exporter.get_finished_spans()) == 0

    torn_down = await manager.teardown(
        voice_session.voice_session_id, reason="client_end"
    )

    assert torn_down is True
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "voice.session"
    attributes = dict(spans[0].attributes or {})
    assert attributes["status"] == "client_end"
    assert isinstance(attributes["latency_ms"], int)
    assert attributes["latency_ms"] >= 0
    assert all(str(user_id) not in str(value) for value in attributes.values())
