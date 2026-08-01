"""Memory lifecycle state definitions (storage-independent; Part I § Lifecycle).

Phase 1 defines only the canonical states and legal transitions so
``MemoryRecord`` and later lifecycle processing share one source of truth.
The full ``LifecycleManager`` (transition execution, retention/archival
policies, event publication) is implemented in Phase 7.
"""

from __future__ import annotations

from enum import StrEnum

from app.ai.memory.exceptions import MemoryError


class LifecycleState(StrEnum):
    """Canonical Memory lifecycle states (Part I § Locked Architectural Decisions)."""

    CREATED = "created"
    ACTIVE = "active"
    CONSOLIDATED = "consolidated"
    ARCHIVED = "archived"
    DELETED = "deleted"


TERMINAL_STATES: frozenset[LifecycleState] = frozenset({LifecycleState.DELETED})

VALID_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.CREATED: frozenset({LifecycleState.ACTIVE, LifecycleState.DELETED}),
    LifecycleState.ACTIVE: frozenset(
        {LifecycleState.CONSOLIDATED, LifecycleState.ARCHIVED, LifecycleState.DELETED}
    ),
    LifecycleState.CONSOLIDATED: frozenset(
        {LifecycleState.ARCHIVED, LifecycleState.DELETED}
    ),
    LifecycleState.ARCHIVED: frozenset({LifecycleState.DELETED}),
    LifecycleState.DELETED: frozenset(),
}


def validate_transition(current: LifecycleState, target: LifecycleState) -> None:
    """Raise ``MemoryError`` if ``current -> target`` is not a legal transition."""
    if target not in VALID_TRANSITIONS[current]:
        raise MemoryError(
            f"Illegal memory lifecycle transition: {current.value} -> {target.value}.",
            code="invalid_lifecycle_transition",
        )
