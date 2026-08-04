"""Tests for MemoryManager orchestration (Phase 1 scaffold + Phase 3 extraction)."""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from typing import cast

import pytest

from app.ai.memory.events import MemoryEvent, MemoryEventType
from app.ai.memory.manager import MemoryManager
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings
from app.providers.base import LLMProvider
from app.schemas.chat import ChatMessageSchema
from tests.fakes import FakeProvider as FakeLLMProvider

_NOW = datetime.datetime.now(datetime.timezone.utc)
DIMENSIONS = 8


class FakeMemoryProvider:
    """Minimal fake ``MemoryProvider`` recording calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.record: MemoryRecord | None = None
        self.preferences: dict[tuple[uuid.UUID, str], dict[str, object]] = {}
        self.created_records: list[MemoryRecord] = []
        self.existing_records: list[MemoryRecord] = []

    async def create_record(self, record: MemoryRecord) -> MemoryRecord:
        self.calls.append(("create_record", {"record_id": record.id}))
        self.created_records.append(record)
        self.record = record
        return record

    async def update_record(self, record: MemoryRecord) -> MemoryRecord:
        self.calls.append(("update_record", {"record_id": record.id}))
        return record

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        self.calls.append(
            ("get_record", {"record_id": record_id, "owner_id": owner_id})
        )
        return self.record

    async def delete_record(self, record_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        self.calls.append(
            ("delete_record", {"record_id": record_id, "owner_id": owner_id})
        )

    async def search_records(
        self,
        query_embedding: list[float],
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
        top_k: int,
    ) -> list[MemoryRecord]:
        del query_embedding, memory_type, session_id, top_k
        return [r for r in self.existing_records if r.owner_id == owner_id]

    async def list_active_records(
        self,
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        del memory_type, session_id, limit
        return [r for r in self.existing_records if r.owner_id == owner_id]

    async def update_lifecycle_state(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError

    async def get_preference(
        self, *, user_id: uuid.UUID, key: str
    ) -> dict[str, object] | None:
        self.calls.append(("get_preference", {"user_id": user_id, "key": key}))
        return self.preferences.get((user_id, key))

    async def set_preference(
        self, *, user_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> None:
        self.calls.append(
            ("set_preference", {"user_id": user_id, "key": key, "value": value})
        )
        self.preferences[(user_id, key)] = value

    async def delete_preference(self, *, user_id: uuid.UUID, key: str) -> None:
        self.calls.append(("delete_preference", {"user_id": user_id, "key": key}))
        self.preferences.pop((user_id, key), None)


class _FakeEmbeddingProvider:
    dimensions = DIMENSIONS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1) / 10.0] * DIMENSIONS for index, _ in enumerate(texts)]


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[MemoryEvent] = []
        self.completed = asyncio.Event()

    async def publish(self, event: MemoryEvent) -> None:
        self.events.append(event)
        self.completed.set()


async def _await_extraction(event: asyncio.Event, *, timeout: float = 2.0) -> None:
    await asyncio.wait_for(event.wait(), timeout=timeout)


def _record(owner_id: uuid.UUID) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=owner_id,
        content="Remember this.",
        created_at=_NOW,
        updated_at=_NOW,
        source="api",
    )


def _manager(
    provider: FakeMemoryProvider,
    *,
    settings: Settings | None = None,
    publisher: _RecordingPublisher | None = None,
) -> MemoryManager:
    return MemoryManager(
        provider=provider,  # type: ignore[arg-type]
        settings=settings
        or Settings(
            openai_api_key="test-key",
            memory_enabled=True,
            memory_extraction_enabled=True,
            embedding_dimensions=DIMENSIONS,
        ),
        embedding_provider=_FakeEmbeddingProvider(),
        prompt_manager=create_prompt_manager(),
        event_publisher=publisher,
    )


class TestMemoryManager:
    @pytest.mark.anyio
    async def test_get_record_delegates_to_provider(self) -> None:
        provider = FakeMemoryProvider()
        owner_id = uuid.uuid4()
        provider.record = _record(owner_id)
        manager = _manager(provider)

        result = await manager.get_record(provider.record.id, owner_id=owner_id)

        assert result == provider.record
        assert provider.calls == [
            ("get_record", {"record_id": provider.record.id, "owner_id": owner_id})
        ]

    @pytest.mark.anyio
    async def test_delete_record_delegates_to_provider(self) -> None:
        provider = FakeMemoryProvider()
        owner_id = uuid.uuid4()
        record_id = uuid.uuid4()
        manager = _manager(provider)

        await manager.delete_record(record_id, owner_id=owner_id)

        assert provider.calls == [
            ("delete_record", {"record_id": record_id, "owner_id": owner_id})
        ]

    @pytest.mark.anyio
    async def test_preference_round_trip_delegates_to_provider(self) -> None:
        provider = FakeMemoryProvider()
        user_id = uuid.uuid4()
        manager = _manager(provider)

        assert (
            await manager.get_preference(user_id=user_id, key="response_tone") is None
        )

        await manager.set_preference(
            user_id=user_id, key="response_tone", value={"tone": "concise"}
        )
        assert await manager.get_preference(user_id=user_id, key="response_tone") == {
            "tone": "concise"
        }

        await manager.delete_preference(user_id=user_id, key="response_tone")
        assert (
            await manager.get_preference(user_id=user_id, key="response_tone") is None
        )


class TestMemoryManagerExtraction:
    @pytest.mark.anyio
    async def test_extract_and_persist_async_persists_approved_memories(self) -> None:
        provider = FakeMemoryProvider()
        publisher = _RecordingPublisher()
        manager = _manager(provider, publisher=publisher)
        owner_id = uuid.uuid4()
        payload = {
            "memories": [
                {
                    "memory_type": "user",
                    "content": "User prefers TypeScript.",
                    "confidence": 0.95,
                    "importance": 0.9,
                }
            ]
        }
        llm = FakeLLMProvider(response=json.dumps(payload))

        manager.extract_and_persist_async(
            owner_id=owner_id,
            session_id=None,
            messages=[ChatMessageSchema(role="user", content="I prefer TypeScript.")],
            provider=cast(LLMProvider, llm),
            provider_name="openai",
            model="gpt-4o-mini",
        )
        await _await_extraction(publisher.completed)

        assert len(provider.created_records) == 1
        assert provider.created_records[0].content == "User prefers TypeScript."
        assert provider.created_records[0].source == "extraction_v1"
        assert provider.created_records[0].embedding is not None
        assert len(publisher.events) == 1
        assert publisher.events[0].event_type is MemoryEventType.CREATED

    @pytest.mark.anyio
    async def test_extract_and_persist_async_noops_when_flag_disabled(self) -> None:
        provider = FakeMemoryProvider()
        manager = _manager(
            provider,
            settings=Settings(
                openai_api_key="test-key",
                memory_enabled=False,
                memory_extraction_enabled=True,
                embedding_dimensions=DIMENSIONS,
            ),
        )
        payload = {
            "memories": [
                {
                    "memory_type": "user",
                    "content": "Should not persist.",
                    "confidence": 0.95,
                    "importance": 0.9,
                }
            ]
        }
        llm = FakeLLMProvider(response=json.dumps(payload))

        manager.extract_and_persist_async(
            owner_id=uuid.uuid4(),
            session_id=None,
            messages=[ChatMessageSchema(role="user", content="Hello")],
            provider=cast(LLMProvider, llm),
            provider_name="openai",
            model="gpt-4o-mini",
        )
        await asyncio.sleep(0.05)

        assert provider.created_records == []

    @pytest.mark.anyio
    async def test_extract_and_persist_async_isolates_embedding_failures(self) -> None:
        provider = FakeMemoryProvider()
        embedding_complete = asyncio.Event()

        class _FailingEmbeddingProvider:
            dimensions = DIMENSIONS

            def __init__(self) -> None:
                self._attempts = 0

            async def embed_texts(self, texts: list[str]) -> list[list[float]]:
                del texts
                self._attempts += 1
                if self._attempts >= 3:
                    embedding_complete.set()
                raise RuntimeError("embedding unavailable")

        manager = MemoryManager(
            provider=provider,  # type: ignore[arg-type]
            settings=Settings(
                openai_api_key="test-key",
                memory_enabled=True,
                memory_extraction_enabled=True,
                embedding_dimensions=DIMENSIONS,
            ),
            embedding_provider=_FailingEmbeddingProvider(),
            prompt_manager=create_prompt_manager(),
        )
        payload = {
            "memories": [
                {
                    "memory_type": "user",
                    "content": "User prefers Go.",
                    "confidence": 0.95,
                    "importance": 0.9,
                }
            ]
        }
        llm = FakeLLMProvider(response=json.dumps(payload))

        manager.extract_and_persist_async(
            owner_id=uuid.uuid4(),
            session_id=None,
            messages=[ChatMessageSchema(role="user", content="I prefer Go.")],
            provider=cast(LLMProvider, llm),
            provider_name="openai",
            model="gpt-4o-mini",
        )
        await _await_extraction(embedding_complete)

        assert provider.created_records == []

    @pytest.mark.anyio
    async def test_extract_and_persist_async_retries_provider_failures(self) -> None:
        provider = FakeMemoryProvider()
        calls = {"count": 0}
        persistence_complete = asyncio.Event()

        async def flaky_create(record: MemoryRecord) -> MemoryRecord:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("temporary db error")
            provider.created_records.append(record)
            persistence_complete.set()
            return record

        provider.create_record = flaky_create  # type: ignore[method-assign]
        manager = _manager(provider)
        payload = {
            "memories": [
                {
                    "memory_type": "user",
                    "content": "User uses Neovim.",
                    "confidence": 0.95,
                    "importance": 0.9,
                }
            ]
        }
        llm = FakeLLMProvider(response=json.dumps(payload))

        manager.extract_and_persist_async(
            owner_id=uuid.uuid4(),
            session_id=None,
            messages=[ChatMessageSchema(role="user", content="I use Neovim.")],
            provider=cast(LLMProvider, llm),
            provider_name="openai",
            model="gpt-4o-mini",
        )
        await _await_extraction(persistence_complete)

        assert calls["count"] == 2
        assert len(provider.created_records) == 1

    @pytest.mark.anyio
    async def test_rollback_provider_session_rolls_back_when_available(self) -> None:
        from unittest.mock import AsyncMock

        from app.ai.memory.interfaces import MemoryProvider

        session = AsyncMock()

        class ProviderWithSession:
            _session = session

        await MemoryManager._rollback_provider_session(
            cast(MemoryProvider, ProviderWithSession())
        )

        session.rollback.assert_awaited_once()
