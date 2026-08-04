"""``MemoryManager`` — single orchestration entry point for the Memory subsystem.

Public API (stable after Phase 1). Phase 3 adds async durable memory extraction
via ``extract_and_persist_async``; retrieval (``retrieve_context``) lands in
Phase 6; full chat wiring in Phase 8.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory.background_tasks import schedule_extraction_task
from app.ai.memory.context_builder import MemoryContextBuilder
from app.ai.memory.events import (
    LoggingMemoryEventPublisher,
    MemoryEvent,
    MemoryEventMetadata,
    MemoryEventPublisher,
    MemoryEventType,
)
from app.ai.memory.extraction import CandidateMemory, MemoryExtractor
from app.ai.memory.interfaces.memory_provider import MemoryProvider
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.preferences import (
    normalize_preferences,
    validate_preference_key,
    validate_preference_value,
)
from app.ai.memory.quality import MemoryQualityEvaluator
from app.core.logging import get_logger
from app.schemas.chat import ChatMessageSchema, ProviderName

if TYPE_CHECKING:
    from app.ai.interfaces.embedding_provider import EmbeddingProvider
    from app.ai.prompts.manager import PromptManager
    from app.core.config import Settings
    from app.providers.base import LLMProvider

BackgroundProviderFactory = Callable[[AsyncSession], MemoryProvider]

logger = get_logger(__name__)

_MAX_EMBED_ATTEMPTS = 3
_MAX_PERSIST_ATTEMPTS = 2


class MemoryManager:
    """Coordinates retrieval, persistence, and lifecycle via a ``MemoryProvider``."""

    def __init__(
        self,
        provider: MemoryProvider,
        *,
        settings: Settings | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        prompt_manager: PromptManager | None = None,
        event_publisher: MemoryEventPublisher | None = None,
        background_provider_factory: BackgroundProviderFactory | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._embedding_provider = embedding_provider
        self._prompt_manager = prompt_manager
        self._event_publisher = event_publisher or LoggingMemoryEventPublisher()
        self._background_provider_factory = background_provider_factory

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        """Return an owned memory record, or ``None`` if it does not exist."""
        return await self._provider.get_record(record_id, owner_id=owner_id)

    async def delete_record(self, record_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        """Delete an owned memory record via the configured provider."""
        await self._provider.delete_record(record_id, owner_id=owner_id)

    async def get_preference(
        self, *, user_id: uuid.UUID, key: str
    ) -> dict[str, object] | None:
        """Return a caller's structured preference value, if set."""
        validated_key = validate_preference_key(key)
        try:
            return await self._provider.get_preference(
                user_id=user_id, key=validated_key
            )
        except Exception:  # noqa: BLE001 - retrieval must not block callers
            logger.warning(
                "User preference retrieval failed",
                user_id=str(user_id),
                exc_info=True,
            )
            return None

    async def list_preferences(self, *, user_id: uuid.UUID) -> dict[str, object]:
        """Return normalized preferences for the active user."""
        try:
            raw = await self._provider.list_preferences(user_id=user_id)
            return normalize_preferences(raw)
        except Exception:  # noqa: BLE001 - retrieval must not block callers
            logger.warning(
                "User preference list retrieval failed",
                user_id=str(user_id),
                exc_info=True,
            )
            return {}

    async def set_preference(
        self, *, user_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> None:
        """Upsert a caller's structured preference value."""
        validated_key = validate_preference_key(key)
        validated_value = validate_preference_value(value)
        await self._provider.set_preference(
            user_id=user_id, key=validated_key, value=validated_value
        )

    async def delete_preference(self, *, user_id: uuid.UUID, key: str) -> None:
        """Remove a caller's structured preference value."""
        validated_key = validate_preference_key(key)
        await self._provider.delete_preference(user_id=user_id, key=validated_key)

    async def retrieve_preferences_context(
        self, *, user_id: uuid.UUID, context: MemoryContext | None = None
    ) -> MemoryContext:
        """Load normalized preferences into a ``MemoryContext``."""
        builder = MemoryContextBuilder(self._provider)
        return await builder.with_preferences(user_id, context=context)

    def extract_and_persist_async(
        self,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
        messages: list[ChatMessageSchema],
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
    ) -> None:
        """Schedule durable memory extraction without blocking the caller."""
        if not self._extraction_enabled():
            return

        schedule_extraction_task(
            self._run_extraction_pipeline(
                owner_id=owner_id,
                session_id=session_id,
                messages=messages,
                provider=provider,
                provider_name=provider_name,
                model=model,
            )
        )

    async def _run_extraction_pipeline(
        self,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
        messages: list[ChatMessageSchema],
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
    ) -> None:
        assert self._settings is not None
        assert self._prompt_manager is not None
        assert self._embedding_provider is not None

        try:
            extractor = MemoryExtractor(
                prompt_manager=self._prompt_manager,
                settings=self._settings,
            )
            candidates = await extractor.extract_candidates(
                messages=messages,
                provider=provider,
                provider_name=provider_name,
                model=model,
                session_id=session_id,
            )
            if not candidates:
                return

            evaluator = MemoryQualityEvaluator(self._settings)
            filtered = evaluator.filter_preliminary(candidates)
            if not filtered:
                return

            embeddings = await self._embed_with_retry([c.content for c in filtered])
            extraction_model = self._settings.memory_extraction_model.strip() or model
            await self._persist_approved_candidates(
                owner_id=owner_id,
                session_id=session_id,
                filtered=filtered,
                embeddings=embeddings,
                provider_name=provider_name,
                extraction_model=extraction_model,
            )
        except Exception:  # noqa: BLE001 - extraction must never affect chat callers
            logger.warning(
                "Durable memory extraction pipeline failed",
                owner_id=str(owner_id),
                session_id=str(session_id) if session_id is not None else None,
                exc_info=True,
            )

    async def _persist_approved_candidates(
        self,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
        filtered: list[CandidateMemory],
        embeddings: list[list[float] | None],
        provider_name: ProviderName,
        extraction_model: str,
    ) -> None:
        assert self._settings is not None

        evaluator = MemoryQualityEvaluator(self._settings)
        if self._background_provider_factory is not None:
            from app.db.engine import get_sessionmaker

            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                try:
                    bg_provider = self._background_provider_factory(session)
                    existing_records = await self._load_existing_records(
                        bg_provider,
                        owner_id=owner_id,
                        session_id=session_id,
                    )
                    approved = evaluator.dedupe_by_embedding(
                        filtered,
                        embeddings,
                        existing_records,
                    )
                    if not approved:
                        return

                    for candidate, embedding in approved:
                        await self._persist_candidate(
                            bg_provider,
                            candidate=candidate,
                            embedding=embedding,
                            owner_id=owner_id,
                            session_id=session_id,
                            provider_name=provider_name,
                            extraction_model=extraction_model,
                        )
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
            return

        existing_records = await self._load_existing_records(
            self._provider,
            owner_id=owner_id,
            session_id=session_id,
        )
        approved = evaluator.dedupe_by_embedding(
            filtered,
            embeddings,
            existing_records,
        )
        for candidate, embedding in approved:
            await self._persist_candidate(
                self._provider,
                candidate=candidate,
                embedding=embedding,
                owner_id=owner_id,
                session_id=session_id,
                provider_name=provider_name,
                extraction_model=extraction_model,
            )

    async def _persist_candidate(
        self,
        provider: MemoryProvider,
        *,
        candidate: CandidateMemory,
        embedding: list[float],
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
        provider_name: ProviderName,
        extraction_model: str,
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        project_id = session_id if candidate.memory_type is MemoryType.PROJECT else None
        if candidate.memory_type is MemoryType.PROJECT and project_id is None:
            logger.warning(
                "Skipping project memory without session scope",
                owner_id=str(owner_id),
            )
            return

        record = MemoryRecord(
            id=uuid.uuid4(),
            memory_type=candidate.memory_type,
            scope=(
                MemoryScope.PROJECT
                if candidate.memory_type is MemoryType.PROJECT
                else MemoryScope.USER
            ),
            owner_id=owner_id,
            project_id=project_id,
            title=candidate.title,
            content=candidate.content,
            embedding=embedding,
            metadata={
                "provider": provider_name,
                "extraction_model": extraction_model,
                "session_id": str(session_id) if session_id is not None else None,
            },
            importance=candidate.importance,
            confidence=candidate.confidence,
            quality_score=candidate.quality_score,
            created_at=now,
            updated_at=now,
            lifecycle_state=LifecycleState.CREATED,
            source="extraction_v1",
        )

        for attempt in range(_MAX_PERSIST_ATTEMPTS):
            try:
                persisted = await provider.create_record(record)
            except Exception:  # noqa: BLE001 - retryable provider failures
                await self._rollback_provider_session(provider)
                if attempt + 1 >= _MAX_PERSIST_ATTEMPTS:
                    logger.warning(
                        "Memory persistence failed",
                        record_id=str(record.id),
                        owner_id=str(owner_id),
                        exc_info=True,
                    )
                    return
                await asyncio.sleep(0.25 * (attempt + 1))
                continue

            await self._event_publisher.publish(
                MemoryEvent(
                    event_type=MemoryEventType.CREATED,
                    record_id=persisted.id,
                    owner_id=persisted.owner_id,
                    memory_type=persisted.memory_type.value,
                    lifecycle_state=persisted.lifecycle_state,
                    metadata=MemoryEventMetadata(source=persisted.source),
                )
            )
            logger.info(
                "Durable memory persisted",
                record_id=str(persisted.id),
                owner_id=str(persisted.owner_id),
                memory_type=persisted.memory_type.value,
            )
            return

    @staticmethod
    async def _rollback_provider_session(provider: MemoryProvider) -> None:
        session = getattr(provider, "_session", None)
        if session is not None:
            await session.rollback()

    async def _embed_with_retry(self, texts: list[str]) -> list[list[float] | None]:
        assert self._embedding_provider is not None

        for attempt in range(_MAX_EMBED_ATTEMPTS):
            try:
                vectors = await self._embedding_provider.embed_texts(texts)
                return cast(list[list[float] | None], vectors)
            except Exception:  # noqa: BLE001 - embedding failures are recoverable
                if attempt + 1 >= _MAX_EMBED_ATTEMPTS:
                    logger.warning(
                        "Memory embedding generation failed",
                        candidate_count=len(texts),
                        exc_info=True,
                    )
                    return [None] * len(texts)
                await asyncio.sleep(0.25 * (attempt + 1))
        return [None] * len(texts)

    async def _load_existing_records(
        self,
        provider: MemoryProvider,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
    ) -> list[MemoryRecord]:
        list_active = getattr(provider, "list_active_records", None)
        if list_active is not None:
            loader = cast(
                Callable[..., Awaitable[list[MemoryRecord]]],
                list_active,
            )
            return await loader(owner_id=owner_id, session_id=session_id)

        # Fakes without the package-internal helper skip cross-record dedupe.
        return []

    def _extraction_enabled(self) -> bool:
        if self._settings is None:
            return False
        if not self._settings.memory_enabled:
            return False
        if not self._settings.memory_extraction_enabled:
            return False
        return self._embedding_provider is not None and self._prompt_manager is not None
