"""Tests for MemoryPolicyEngine (Epic 05 Phase 7)."""

from __future__ import annotations

import datetime
import uuid

from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.policy_engine import LifecycleDecision, MemoryPolicyEngine
from app.core.config import Settings

_NOW = datetime.datetime.now(datetime.timezone.utc)
_DIMENSIONS = 8


def _vector(value: float) -> list[float]:
    return [value] + [0.0] * (_DIMENSIONS - 1)


def _record(
    *,
    content: str = "Prefers concise answers.",
    quality: float = 0.8,
    confidence: float = 0.8,
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE,
    embedding: list[float] | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=uuid.uuid4(),
        content=content,
        embedding=embedding if embedding is not None else _vector(0.9),
        quality_score=quality,
        confidence=confidence,
        created_at=_NOW,
        updated_at=_NOW,
        lifecycle_state=lifecycle_state,
        source="test",
    )


def _engine() -> MemoryPolicyEngine:
    return MemoryPolicyEngine(
        Settings(
            openai_api_key="test-key",
            memory_dedupe_similarity_threshold=0.92,
            memory_archived_retention_days=90,
        )
    )


class TestMemoryPolicyEngine:
    def test_should_activate_only_for_created(self) -> None:
        engine = _engine()
        assert engine.should_activate(_record(lifecycle_state=LifecycleState.CREATED))
        assert not engine.should_activate(
            _record(lifecycle_state=LifecycleState.ACTIVE)
        )

    def test_find_consolidation_groups_picks_highest_quality_winner(self) -> None:
        engine = _engine()
        winner = _record(content="Winner", quality=0.95, embedding=_vector(1.0))
        loser = _record(content="Loser", quality=0.5, embedding=_vector(0.99))

        groups = engine.find_consolidation_groups([loser, winner])

        assert len(groups) == 1
        assert groups[0].winner.id == winner.id
        assert {record.id for record in groups[0].redundant} == {loser.id}

    def test_consolidation_targets_active_and_created_only(self) -> None:
        engine = _engine()
        winner = _record(content="Winner", quality=0.95, embedding=_vector(1.0))
        active = _record(
            content="Active duplicate",
            quality=0.5,
            lifecycle_state=LifecycleState.ACTIVE,
            embedding=_vector(0.99),
        )
        consolidated = _record(
            content="Already consolidated",
            quality=0.4,
            lifecycle_state=LifecycleState.CONSOLIDATED,
            embedding=_vector(0.98),
        )
        group = engine.find_consolidation_groups([winner, active, consolidated])[0]

        outcomes = engine.consolidation_targets(group)

        assert len(outcomes) == 1
        assert outcomes[0].record_id == str(active.id)
        assert outcomes[0].decision is LifecycleDecision.CONSOLIDATE
        assert outcomes[0].target_state is LifecycleState.CONSOLIDATED

    def test_consolidated_record_does_not_win_canonical_selection(self) -> None:
        engine = _engine()
        consolidated = _record(
            content="Already consolidated duplicate",
            quality=0.99,
            lifecycle_state=LifecycleState.CONSOLIDATED,
            embedding=_vector(1.0),
        )
        active = _record(
            content="Eligible active duplicate",
            quality=0.5,
            lifecycle_state=LifecycleState.ACTIVE,
            embedding=_vector(0.99),
        )
        created = _record(
            content="Eligible created duplicate",
            quality=0.4,
            lifecycle_state=LifecycleState.CREATED,
            embedding=_vector(0.98),
        )

        groups = engine.find_consolidation_groups([consolidated, active, created])

        assert len(groups) == 1
        assert groups[0].winner.id == active.id
        redundant_ids = {record.id for record in groups[0].redundant}
        assert consolidated.id not in redundant_ids
        assert created.id in redundant_ids

        outcomes = engine.consolidation_targets(groups[0])
        outcome_ids = {outcome.record_id for outcome in outcomes}
        assert str(active.id) not in outcome_ids
        assert str(created.id) in outcome_ids
        assert not engine.should_archive(active)

    def test_should_archive_consolidated_only(self) -> None:
        engine = _engine()
        assert engine.should_archive(
            _record(lifecycle_state=LifecycleState.CONSOLIDATED)
        )
        assert not engine.should_archive(_record(lifecycle_state=LifecycleState.ACTIVE))

    def test_should_delete_archived_after_retention_window(self) -> None:
        engine = _engine()
        archived = _record(lifecycle_state=LifecycleState.ARCHIVED)
        archived.metadata["archived_at"] = (
            _NOW - datetime.timedelta(days=91)
        ).isoformat()
        assert engine.should_delete_archived(archived, now=_NOW)
        assert not engine.should_delete_archived(
            _record(lifecycle_state=LifecycleState.ACTIVE),
            now=_NOW,
        )
