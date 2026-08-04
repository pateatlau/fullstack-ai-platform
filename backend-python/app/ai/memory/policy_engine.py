"""Deterministic lifecycle policy evaluation (Epic 05 Phase 7).

Consolidation thresholds and retention rules are implementation policies
defined here — not part of the frozen Part I architecture.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum

from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord
from app.ai.memory.quality import cosine_similarity
from app.core.config import Settings

_CONSOLIDATION_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.CREATED,
        LifecycleState.ACTIVE,
        LifecycleState.CONSOLIDATED,
    }
)


class LifecycleDecision(StrEnum):
    """Policy outcome for a single memory record."""

    ACTIVATE = "activate"
    CONSOLIDATE = "consolidate"
    ARCHIVE = "archive"
    DELETE = "delete"
    NONE = "none"


@dataclass(frozen=True)
class PolicyOutcome:
    """Deterministic lifecycle action for one record."""

    record_id: str
    decision: LifecycleDecision
    target_state: LifecycleState | None = None


@dataclass(frozen=True)
class ConsolidationGroup:
    """A duplicate cluster with one canonical winner and redundant members."""

    winner: MemoryRecord
    redundant: tuple[MemoryRecord, ...]


class MemoryPolicyEngine:
    """Evaluate lifecycle eligibility without touching storage."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def should_activate(self, record: MemoryRecord) -> bool:
        """Newly persisted memories transition from ``created`` to ``active``."""
        return record.lifecycle_state is LifecycleState.CREATED

    def find_consolidation_groups(
        self, records: list[MemoryRecord]
    ) -> list[ConsolidationGroup]:
        """Group semantically similar memories and pick the highest-quality winner."""
        threshold = self._settings.memory_dedupe_similarity_threshold
        candidates = [
            record
            for record in records
            if record.lifecycle_state in _CONSOLIDATION_STATES
            and record.embedding is not None
            and record.lifecycle_state is not LifecycleState.DELETED
        ]
        ordered = sorted(
            candidates,
            key=lambda record: (
                -record.quality_score,
                -record.confidence,
                -record.created_at.timestamp(),
                str(record.id),
            ),
        )

        groups: list[ConsolidationGroup] = []
        assigned: set[str] = set()

        for winner in ordered:
            if str(winner.id) in assigned:
                continue
            cluster = [winner]
            assigned.add(str(winner.id))
            winner_embedding = winner.embedding
            assert winner_embedding is not None

            for other in ordered:
                if str(other.id) in assigned:
                    continue
                other_embedding = other.embedding
                if other_embedding is None:
                    continue
                if cosine_similarity(winner_embedding, other_embedding) >= threshold:
                    cluster.append(other)
                    assigned.add(str(other.id))

            if len(cluster) > 1:
                groups.append(
                    ConsolidationGroup(
                        winner=winner,
                        redundant=tuple(
                            record for record in cluster if record.id != winner.id
                        ),
                    )
                )

        return groups

    def consolidation_targets(self, group: ConsolidationGroup) -> list[PolicyOutcome]:
        """Mark redundant cluster members for consolidation."""
        outcomes: list[PolicyOutcome] = []
        for record in group.redundant:
            if record.lifecycle_state not in {
                LifecycleState.CREATED,
                LifecycleState.ACTIVE,
            }:
                continue
            outcomes.append(
                PolicyOutcome(
                    record_id=str(record.id),
                    decision=LifecycleDecision.CONSOLIDATE,
                    target_state=LifecycleState.CONSOLIDATED,
                )
            )
        return outcomes

    def should_archive(self, record: MemoryRecord) -> bool:
        """Consolidated memories are archived for admin retention only."""
        return record.lifecycle_state is LifecycleState.CONSOLIDATED

    def should_delete_archived(
        self, record: MemoryRecord, *, now: datetime.datetime
    ) -> bool:
        """Archived memories are permanently deleted after the retention window."""
        if record.lifecycle_state is not LifecycleState.ARCHIVED:
            return False
        archived_at = _archived_at(record, fallback=record.updated_at)
        retention = datetime.timedelta(
            days=self._settings.memory_archived_retention_days
        )
        return archived_at + retention <= now


def _archived_at(
    record: MemoryRecord, *, fallback: datetime.datetime
) -> datetime.datetime:
    raw = record.metadata.get("archived_at")
    if isinstance(raw, str):
        try:
            parsed = datetime.datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed
        except ValueError:
            pass
    return fallback
