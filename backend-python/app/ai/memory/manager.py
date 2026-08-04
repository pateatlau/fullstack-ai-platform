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

from app.ai.memory.exceptions import MemoryAccessDeniedError
from app.ai.memory.background_tasks import (
    schedule_extraction_task,
    schedule_lifecycle_task,
)
from app.ai.memory.context_builder import MemoryContextBuilder
from app.ai.memory.semantic_retriever import SemanticRetriever
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
from app.ai.memory.lifecycle_manager import LifecycleManager
from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.preferences import (
    normalize_preferences,
    validate_preference_key,
    validate_preference_value,
)
from app.ai.memory.project import (
    SessionOwnershipChecker,
    assert_project_record_scope,
    map_project_id_to_session_id,
    normalize_project_memories,
    validate_project_id,
)
from app.ai.memory.quality import MemoryQualityEvaluator
from app.core.logging import get_logger
from app.schemas.chat import ChatMessageSchema, ProviderName

if TYPE_CHECKING:
    from app.ai.interfaces.embedding_provider import EmbeddingProvider
    from app.ai.prompts.manager import PromptManager
    from app.ai.memory.summarizer import ConversationSummaryService
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
        lifecycle_manager: LifecycleManager | None = None,
        background_provider_factory: BackgroundProviderFactory | None = None,
        session_ownership_checker: SessionOwnershipChecker | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._embedding_provider = embedding_provider
        self._prompt_manager = prompt_manager
        self._event_publisher = event_publisher or LoggingMemoryEventPublisher()
        self._lifecycle_manager = lifecycle_manager
        self._background_provider_factory = background_provider_factory
        self._session_ownership_checker = session_ownership_checker

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        """Return an owned memory record, or ``None`` if it does not exist."""
        return await self._provider.get_record(record_id, owner_id=owner_id)

    async def delete_record(self, record_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        """Soft-delete an owned memory record via lifecycle management."""
        manager = self._require_lifecycle_manager()
        await manager.delete_record(record_id, owner_id=owner_id)

    async def list_records(
        self,
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType,
        session_id: uuid.UUID | None = None,
    ) -> list[MemoryRecord]:
        """Return caller-owned records for management APIs (excludes deleted)."""
        if memory_type is MemoryType.PROJECT:
            validated_session = validate_project_id(session_id) if session_id else None
            if validated_session is None:
                raise ValueError("session_id is required for project memory.")
            await self._assert_session_owned(
                owner_id=owner_id, project_id=validated_session
            )
            list_records = getattr(self._provider, "list_records", None)
            if list_records is None:
                return []
            records = await list_records(
                owner_id=owner_id,
                memory_type=MemoryType.PROJECT,
                session_id=map_project_id_to_session_id(validated_session),
                include_deleted=False,
            )
            return normalize_project_memories(records)

        list_records = getattr(self._provider, "list_records", None)
        if list_records is None:
            return []
        return await list_records(
            owner_id=owner_id,
            memory_type=MemoryType.USER,
            include_deleted=False,
        )

    async def clear_session_summary(
        self,
        *,
        session_id: uuid.UUID,
        owner_id: uuid.UUID,
        summary_service: ConversationSummaryService,
    ) -> None:
        """Clear rolling summaries for an owned chat session."""
        await summary_service.clear_summary(session_id=session_id, owner_id=owner_id)

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
        builder = MemoryContextBuilder(self._provider, settings=self._settings)
        return await builder.with_preferences(user_id, context=context)

    async def retrieve_context(
        self,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
        messages: list[ChatMessageSchema],
        conversation_summary: str | None = None,
    ) -> MemoryContext:
        """Retrieve ranked semantic memories and build a canonical ``MemoryContext``."""
        if not self._retrieval_enabled():
            return MemoryContext()

        assert self._settings is not None
        assert self._embedding_provider is not None

        retriever = SemanticRetriever(
            self._provider,
            self._embedding_provider,
            self._settings,
            session_ownership_checker=self._session_ownership_checker,
        )
        builder = MemoryContextBuilder(self._provider, settings=self._settings)

        try:
            retrieval = await retriever.retrieve(
                owner_id=owner_id,
                messages=messages,
                conversation_summary=conversation_summary,
                project_id=session_id,
            )
            context = builder.build_from_retrieval(
                retrieval,
                conversation_summary=conversation_summary,
            )
            return await builder.with_preferences(owner_id, context=context)
        except Exception:  # noqa: BLE001 - retrieval must not block callers
            logger.warning(
                "Semantic memory context retrieval failed",
                owner_id=str(owner_id),
                session_id=str(session_id) if session_id is not None else None,
                exc_info=True,
            )
            return MemoryContext(conversation_summary=conversation_summary)

    async def list_project_memories(
        self, *, owner_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[MemoryRecord]:
        """Return active project memories scoped to an owned chat session."""
        validated_project_id = validate_project_id(project_id)
        await self._assert_session_owned(
            owner_id=owner_id, project_id=validated_project_id
        )
        try:
            records = await self._list_active_records(
                owner_id=owner_id,
                memory_type=MemoryType.PROJECT,
                session_id=map_project_id_to_session_id(validated_project_id),
            )
            return normalize_project_memories(records)
        except MemoryAccessDeniedError:
            raise
        except Exception:  # noqa: BLE001 - retrieval must not block callers
            logger.warning(
                "Project memory list retrieval failed",
                owner_id=str(owner_id),
                project_id=str(validated_project_id),
                exc_info=True,
            )
            return []

    async def search_project_memories(
        self,
        query_embedding: list[float],
        *,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
        top_k: int,
    ) -> list[MemoryRecord]:
        """Return provider-ranked project memories for an owned session."""
        validated_project_id = validate_project_id(project_id)
        await self._assert_session_owned(
            owner_id=owner_id, project_id=validated_project_id
        )
        try:
            results = await self._provider.search_records(
                query_embedding,
                owner_id=owner_id,
                memory_type=MemoryType.PROJECT,
                session_id=map_project_id_to_session_id(validated_project_id),
                top_k=top_k,
            )
            return normalize_project_memories(results)
        except MemoryAccessDeniedError:
            raise
        except Exception:  # noqa: BLE001 - retrieval must not block callers
            logger.warning(
                "Project memory search failed",
                owner_id=str(owner_id),
                project_id=str(validated_project_id),
                exc_info=True,
            )
            return []

    async def get_project_record(
        self,
        record_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> MemoryRecord | None:
        """Return a project memory when it belongs to the owned session."""
        validated_project_id = validate_project_id(project_id)
        await self._assert_session_owned(
            owner_id=owner_id, project_id=validated_project_id
        )
        record = await self._provider.get_record(record_id, owner_id=owner_id)
        if record is None:
            return None
        try:
            assert_project_record_scope(record, project_id=validated_project_id)
        except MemoryAccessDeniedError:
            return None
        return record

    async def update_project_record(
        self,
        record: MemoryRecord,
        *,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> MemoryRecord:
        """Update a project memory after session ownership and scope checks."""
        validated_project_id = validate_project_id(project_id)
        await self._assert_session_owned(
            owner_id=owner_id, project_id=validated_project_id
        )
        if record.owner_id != owner_id:
            raise MemoryAccessDeniedError("Project memory owner mismatch.")
        assert_project_record_scope(record, project_id=validated_project_id)
        return await self._provider.update_record(record)

    async def retrieve_project_context(
        self,
        *,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
        context: MemoryContext | None = None,
    ) -> MemoryContext:
        """Load normalized project memories into a ``MemoryContext``."""
        validated_project_id = validate_project_id(project_id)
        try:
            await self._assert_session_owned(
                owner_id=owner_id, project_id=validated_project_id
            )
        except MemoryAccessDeniedError:
            logger.warning(
                "Project memory context skipped — session ownership denied",
                owner_id=str(owner_id),
                project_id=str(validated_project_id),
            )
            return context or MemoryContext()

        builder = MemoryContextBuilder(self._provider, settings=self._settings)
        return await builder.with_project_memories(
            owner_id,
            validated_project_id,
            context=context,
        )

    async def _assert_session_owned(
        self, *, owner_id: uuid.UUID, project_id: uuid.UUID
    ) -> None:
        if self._session_ownership_checker is None:
            return
        session_id = map_project_id_to_session_id(project_id)
        owns_session = await self._session_ownership_checker.user_owns_session(
            user_id=owner_id,
            session_id=session_id,
        )
        if not owns_session:
            raise MemoryAccessDeniedError(
                "Access to project memory for this session is denied."
            )

    async def _list_active_records(
        self,
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
    ) -> list[MemoryRecord]:
        list_active = getattr(self._provider, "list_active_records", None)
        if list_active is None:
            return []
        loader = cast(
            Callable[..., Awaitable[list[MemoryRecord]]],
            list_active,
        )
        return await loader(
            owner_id=owner_id,
            memory_type=memory_type,
            session_id=session_id,
        )

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
                    await self._schedule_lifecycle_processing(
                        provider=bg_provider,
                        owner_id=owner_id,
                        session_id=session_id,
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
        await self._schedule_lifecycle_processing(
            provider=self._provider,
            owner_id=owner_id,
            session_id=session_id,
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
            activated = await self._activate_persisted_record(provider, persisted)
            logger.info(
                "Durable memory persisted",
                record_id=str(activated.id),
                owner_id=str(activated.owner_id),
                memory_type=activated.memory_type.value,
            )
            return

    async def _activate_persisted_record(
        self,
        provider: MemoryProvider,
        record: MemoryRecord,
    ) -> MemoryRecord:
        manager = self._lifecycle_manager
        if manager is None and self._settings is not None:
            manager = LifecycleManager(
                provider,
                settings=self._settings,
                event_publisher=self._event_publisher,
            )
        if manager is None:
            return record
        return await manager.activate_record(record)

    async def _schedule_lifecycle_processing(
        self,
        *,
        provider: MemoryProvider,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
    ) -> None:
        if self._settings is None or not self._settings.memory_enabled:
            return
        settings = self._settings

        async def _run() -> None:
            if self._background_provider_factory is not None:
                from app.db.engine import get_sessionmaker

                sessionmaker = get_sessionmaker()
                async with sessionmaker() as session:
                    try:
                        bg_provider = self._background_provider_factory(session)
                        manager = LifecycleManager(
                            bg_provider,
                            settings=settings,
                            event_publisher=self._event_publisher,
                        )
                        await manager.process_owner_memories(
                            owner_id=owner_id,
                            session_id=session_id,
                        )
                        await session.commit()
                    except Exception:
                        await session.rollback()
                        raise
                return

            manager = self._lifecycle_manager or LifecycleManager(
                provider,
                settings=settings,
                event_publisher=self._event_publisher,
            )
            await manager.process_owner_memories(
                owner_id=owner_id,
                session_id=session_id,
            )

        schedule_lifecycle_task(_run())

    def _require_lifecycle_manager(self) -> LifecycleManager:
        if self._lifecycle_manager is not None:
            return self._lifecycle_manager
        if self._settings is None:
            raise RuntimeError("Memory lifecycle manager is not configured.")
        self._lifecycle_manager = LifecycleManager(
            self._provider,
            settings=self._settings,
            event_publisher=self._event_publisher,
        )
        return self._lifecycle_manager

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

    def _retrieval_enabled(self) -> bool:
        if self._settings is None:
            return False
        if not self._settings.memory_enabled:
            return False
        return self._embedding_provider is not None
