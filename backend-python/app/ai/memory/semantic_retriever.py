"""Semantic retrieval pipeline across user and project memory domains (Phase 6).

Transforms the current conversation into a query embedding, retrieves memories
via ``MemoryProvider``, ranks/filters/deduplicates provider-independently, and
returns domain-split results for ``MemoryContextBuilder``.
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai.memory.exceptions import MemoryAccessDeniedError
from app.ai.memory.interfaces.memory_provider import MemoryProvider
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryType
from app.ai.memory.project import (
    SessionOwnershipChecker,
    map_project_id_to_session_id,
    validate_project_id,
)
from app.ai.memory.quality import cosine_similarity
from app.core.logging import get_logger
from app.schemas.chat import ChatMessageSchema

if TYPE_CHECKING:
    from app.ai.interfaces.embedding_provider import EmbeddingProvider
    from app.core.config import Settings

logger = get_logger(__name__)

_RETRIEVABLE_LIFECYCLE_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.CREATED,
        LifecycleState.ACTIVE,
        LifecycleState.CONSOLIDATED,
    }
)

_MAX_EMBED_ATTEMPTS = 3


@dataclass(frozen=True)
class ScoredMemory:
    """A memory record with provider-independent ranking inputs."""

    record: MemoryRecord
    similarity: float
    rank_score: float


@dataclass(frozen=True)
class RetrievalResult:
    """Ranked, filtered memories split by domain for context construction."""

    user_memories: list[MemoryRecord]
    project_memories: list[MemoryRecord]
    metadata: dict[str, object]


def build_retrieval_query(
    messages: list[ChatMessageSchema],
    conversation_summary: str | None = None,
) -> str:
    """Build a normalized semantic query from conversation turns and summary."""
    parts: list[str] = []
    if conversation_summary is not None:
        summary = conversation_summary.strip()
        if summary:
            parts.append(summary)
    for message in messages:
        if message.role in ("user", "assistant"):
            content = message.content.strip()
            if content:
                parts.append(content)
    return normalize_retrieval_query("\n".join(parts))


def normalize_retrieval_query(query: str) -> str:
    """Collapse whitespace for stable embedding input."""
    return " ".join(query.split())


def compute_rank_score(record: MemoryRecord, similarity: float) -> float:
    """Provider-independent ranking: similarity plus quality signals."""
    return (
        similarity * 0.5
        + record.quality_score * 0.2
        + record.confidence * 0.15
        + record.importance * 0.15
    )


def rank_scored_memories(scored: list[ScoredMemory]) -> list[ScoredMemory]:
    """Sort by rank score descending with deterministic tie-breakers."""
    return sorted(
        scored,
        key=lambda item: (
            -item.rank_score,
            -item.similarity,
            -item.record.created_at.timestamp(),
            str(item.record.id),
        ),
    )


class SemanticRetriever:
    """Retrieve and rank relevant memories using semantic similarity search."""

    def __init__(
        self,
        provider: MemoryProvider,
        embedding_provider: EmbeddingProvider,
        settings: Settings,
        *,
        session_ownership_checker: SessionOwnershipChecker | None = None,
    ) -> None:
        self._provider = provider
        self._embedding_provider = embedding_provider
        self._settings = settings
        self._session_ownership_checker = session_ownership_checker

    async def retrieve(
        self,
        *,
        owner_id: uuid.UUID,
        messages: list[ChatMessageSchema],
        conversation_summary: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> RetrievalResult:
        """Retrieve ranked user and project memories for the active conversation."""
        started = time.perf_counter()
        query_text = build_retrieval_query(messages, conversation_summary)
        if not query_text:
            return RetrievalResult(
                user_memories=[],
                project_memories=[],
                metadata={"retrieval_skipped": "empty_query"},
            )

        query_embedding = await self._embed_query(query_text)
        if query_embedding is None:
            return RetrievalResult(
                user_memories=[],
                project_memories=[],
                metadata={"retrieval_skipped": "embedding_failed"},
            )

        top_k = self._settings.memory_retrieval_top_k
        user_task = self._search_domain(
            query_embedding,
            owner_id=owner_id,
            memory_type=MemoryType.USER,
            session_id=None,
            top_k=top_k,
        )

        project_task: asyncio.Task[list[MemoryRecord]] | None = None
        validated_project_id: uuid.UUID | None = None
        if project_id is not None:
            try:
                validated_project_id = validate_project_id(project_id)
                await self._assert_session_owned(
                    owner_id=owner_id, project_id=validated_project_id
                )
                project_task = asyncio.create_task(
                    self._search_domain(
                        query_embedding,
                        owner_id=owner_id,
                        memory_type=MemoryType.PROJECT,
                        session_id=map_project_id_to_session_id(validated_project_id),
                        top_k=top_k,
                    )
                )
            except MemoryAccessDeniedError:
                logger.warning(
                    "Project memory retrieval skipped — session ownership denied",
                    owner_id=str(owner_id),
                    project_id=str(project_id),
                )

        user_raw = await user_task
        project_raw = await project_task if project_task is not None else []

        user_scored = self._deduplicate(
            rank_scored_memories(self._score_and_filter(query_embedding, user_raw))
        )
        project_scored = self._deduplicate(
            rank_scored_memories(self._score_and_filter(query_embedding, project_raw))
        )

        user_memories = [item.record for item in user_scored]
        project_memories = [item.record for item in project_scored]
        scored = user_scored + project_scored

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        metadata: dict[str, object] = {
            "retrieval_latency_ms": elapsed_ms,
            "memories_retrieved": len(user_raw) + len(project_raw),
            "memories_ranked": len(scored),
            "user_memories_count": len(user_memories),
            "project_memories_count": len(project_memories),
            "top_k": top_k,
        }
        if validated_project_id is not None:
            metadata["project_id"] = str(validated_project_id)

        logger.info(
            "Semantic memory retrieval completed",
            owner_id=str(owner_id),
            retrieval_latency_ms=elapsed_ms,
            memories_ranked=len(scored),
        )
        return RetrievalResult(
            user_memories=user_memories,
            project_memories=project_memories,
            metadata=metadata,
        )

    async def _embed_query(self, query_text: str) -> list[float] | None:
        for attempt in range(_MAX_EMBED_ATTEMPTS):
            try:
                vectors = await self._embedding_provider.embed_texts([query_text])
                if not vectors or vectors[0] is None:
                    return None
                return list(vectors[0])
            except Exception:  # noqa: BLE001 - embedding failures must not block chat
                if attempt + 1 >= _MAX_EMBED_ATTEMPTS:
                    logger.warning(
                        "Semantic retrieval query embedding failed",
                        exc_info=True,
                    )
                    return None
                await asyncio.sleep(0.25 * (attempt + 1))
        return None

    async def _search_domain(
        self,
        query_embedding: list[float],
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType,
        session_id: uuid.UUID | None,
        top_k: int,
    ) -> list[MemoryRecord]:
        try:
            return await self._provider.search_records(
                query_embedding,
                owner_id=owner_id,
                memory_type=memory_type,
                session_id=session_id,
                top_k=top_k,
            )
        except Exception:  # noqa: BLE001 - retrieval must not block callers
            logger.warning(
                "Semantic memory search failed",
                owner_id=str(owner_id),
                memory_type=memory_type.value,
                exc_info=True,
            )
            return []

    def _score_and_filter(
        self,
        query_embedding: list[float],
        records: list[MemoryRecord],
    ) -> list[ScoredMemory]:
        now = datetime.datetime.now(datetime.timezone.utc)
        scored: list[ScoredMemory] = []

        for record in records:
            if record.lifecycle_state not in _RETRIEVABLE_LIFECYCLE_STATES:
                continue
            if record.expires_at is not None and record.expires_at <= now:
                continue
            if record.confidence < self._settings.memory_min_confidence:
                continue
            if record.quality_score < self._settings.memory_min_quality_score:
                continue
            if record.embedding is None:
                continue

            similarity = cosine_similarity(query_embedding, record.embedding)
            scored.append(
                ScoredMemory(
                    record=record,
                    similarity=similarity,
                    rank_score=compute_rank_score(record, similarity),
                )
            )

        return scored

    def _deduplicate(self, ranked: list[ScoredMemory]) -> list[ScoredMemory]:
        threshold = self._settings.memory_dedupe_similarity_threshold
        kept: list[ScoredMemory] = []
        seen_ids: set[uuid.UUID] = set()

        for item in ranked:
            if item.record.id in seen_ids:
                continue

            duplicate = False
            for existing in kept:
                if existing.record.embedding is None or item.record.embedding is None:
                    continue
                if (
                    cosine_similarity(existing.record.embedding, item.record.embedding)
                    >= threshold
                ):
                    duplicate = True
                    break

            if duplicate:
                continue

            kept.append(item)
            seen_ids.add(item.record.id)

        return kept

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
