"""Tests for Memory lifecycle state definitions and transition validation."""

from __future__ import annotations

import pytest

from app.ai.memory.exceptions import MemoryError
from app.ai.memory.lifecycle import (
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    LifecycleState,
    validate_transition,
)


class TestLifecycleState:
    def test_canonical_states(self) -> None:
        assert {state.value for state in LifecycleState} == {
            "created",
            "active",
            "consolidated",
            "archived",
            "deleted",
        }

    def test_deleted_is_the_only_terminal_state(self) -> None:
        assert TERMINAL_STATES == frozenset({LifecycleState.DELETED})


class TestValidTransitions:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (LifecycleState.CREATED, LifecycleState.ACTIVE),
            (LifecycleState.CREATED, LifecycleState.DELETED),
            (LifecycleState.ACTIVE, LifecycleState.CONSOLIDATED),
            (LifecycleState.ACTIVE, LifecycleState.ARCHIVED),
            (LifecycleState.ACTIVE, LifecycleState.DELETED),
            (LifecycleState.CONSOLIDATED, LifecycleState.ARCHIVED),
            (LifecycleState.CONSOLIDATED, LifecycleState.DELETED),
            (LifecycleState.ARCHIVED, LifecycleState.DELETED),
        ],
    )
    def test_legal_transitions_do_not_raise(
        self, current: LifecycleState, target: LifecycleState
    ) -> None:
        validate_transition(current, target)  # should not raise

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (LifecycleState.CREATED, LifecycleState.CONSOLIDATED),
            (LifecycleState.CREATED, LifecycleState.ARCHIVED),
            (LifecycleState.ACTIVE, LifecycleState.CREATED),
            (LifecycleState.CONSOLIDATED, LifecycleState.CREATED),
            (LifecycleState.CONSOLIDATED, LifecycleState.ACTIVE),
            (LifecycleState.ARCHIVED, LifecycleState.ACTIVE),
            (LifecycleState.ARCHIVED, LifecycleState.CREATED),
            (LifecycleState.DELETED, LifecycleState.ACTIVE),
            (LifecycleState.DELETED, LifecycleState.CREATED),
        ],
    )
    def test_illegal_transitions_raise(
        self, current: LifecycleState, target: LifecycleState
    ) -> None:
        with pytest.raises(MemoryError, match="Illegal memory lifecycle transition"):
            validate_transition(current, target)

    def test_illegal_transition_error_code(self) -> None:
        try:
            validate_transition(LifecycleState.DELETED, LifecycleState.ACTIVE)
        except MemoryError as exc:
            assert exc.code == "invalid_lifecycle_transition"
        else:
            pytest.fail("Expected MemoryError to be raised")

    def test_deleted_state_has_no_outgoing_transitions(self) -> None:
        assert VALID_TRANSITIONS[LifecycleState.DELETED] == frozenset()

    def test_every_state_has_a_transition_table_entry(self) -> None:
        assert set(VALID_TRANSITIONS.keys()) == set(LifecycleState)
