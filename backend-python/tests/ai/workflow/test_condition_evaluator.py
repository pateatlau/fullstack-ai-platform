"""Tests for ``ConditionEvaluator`` (Epic 06 Phase 4)."""

from __future__ import annotations

import pytest

from app.ai.workflow.conditions.evaluator import ConditionEvaluator
from app.ai.workflow.models import WorkflowContext

_EVALUATOR = ConditionEvaluator()


def _context(**kwargs: object) -> WorkflowContext:
    trigger_input = kwargs.pop("trigger_input", {})
    variables = kwargs.pop("variables", {})
    metadata = kwargs.pop("metadata", {})
    assert not kwargs
    return WorkflowContext(
        trigger_input=trigger_input if isinstance(trigger_input, dict) else {},
        variables=variables if isinstance(variables, dict) else {},
        metadata=metadata if isinstance(metadata, dict) else {},
    )


class TestLeafOperators:
    @pytest.mark.parametrize(
        ("operator", "field_value", "expected_value", "result"),
        [
            ("eq", "a", "a", True),
            ("eq", "a", "b", False),
            ("neq", "a", "b", True),
            ("neq", "a", "a", False),
            ("gt", 5, 3, True),
            ("gt", 3, 5, False),
            ("gte", 5, 5, True),
            ("lt", 3, 5, True),
            ("lte", 5, 5, True),
            ("in", "b", ["a", "b"], True),
            ("in", "c", ["a", "b"], False),
            ("contains", "hello world", "world", True),
            ("contains", ["a", "b"], "b", True),
            ("contains", {"key": 1}, "key", True),
        ],
    )
    def test_operator(
        self,
        operator: str,
        field_value: object,
        expected_value: object,
        result: bool,
    ) -> None:
        context = _context(variables={"entry": {"score": field_value}})
        condition = {
            "field": "entry.score",
            "operator": operator,
            "value": expected_value,
        }
        assert _EVALUATOR.evaluate(condition, context) is result

    def test_exists_true_when_field_present(self) -> None:
        context = _context(variables={"entry": {"score": 10}})
        assert (
            _EVALUATOR.evaluate({"field": "entry.score", "operator": "exists"}, context)
            is True
        )

    def test_exists_false_when_field_missing(self) -> None:
        context = _context(variables={"entry": {}})
        assert (
            _EVALUATOR.evaluate({"field": "entry.score", "operator": "exists"}, context)
            is False
        )

    def test_missing_field_non_exists_is_non_matching(self) -> None:
        context = _context(variables={"entry": {}})
        assert (
            _EVALUATOR.evaluate(
                {"field": "entry.score", "operator": "eq", "value": 1}, context
            )
            is False
        )

    def test_trigger_input_dot_path(self) -> None:
        context = _context(trigger_input={"topic": "billing"})
        condition = {
            "field": "trigger_input.topic",
            "operator": "eq",
            "value": "billing",
        }
        assert _EVALUATOR.evaluate(condition, context) is True

    def test_ordered_comparison_rejects_non_numeric(self) -> None:
        context = _context(variables={"entry": {"score": "high"}})
        condition = {"field": "entry.score", "operator": "gt", "value": 1}
        assert _EVALUATOR.evaluate(condition, context) is False


class TestComposition:
    def test_all_requires_every_child(self) -> None:
        context = _context(variables={"entry": {"score": 10, "tier": "pro"}})
        condition = {
            "all": [
                {"field": "entry.score", "operator": "gte", "value": 10},
                {"field": "entry.tier", "operator": "eq", "value": "pro"},
            ]
        }
        assert _EVALUATOR.evaluate(condition, context) is True

    def test_all_fails_when_one_child_fails(self) -> None:
        context = _context(variables={"entry": {"score": 10, "tier": "free"}})
        condition = {
            "all": [
                {"field": "entry.score", "operator": "gte", "value": 10},
                {"field": "entry.tier", "operator": "eq", "value": "pro"},
            ]
        }
        assert _EVALUATOR.evaluate(condition, context) is False

    def test_any_matches_when_one_child_matches(self) -> None:
        context = _context(variables={"entry": {"score": 3}})
        condition = {
            "any": [
                {"field": "entry.score", "operator": "gte", "value": 10},
                {"field": "entry.score", "operator": "lt", "value": 5},
            ]
        }
        assert _EVALUATOR.evaluate(condition, context) is True

    def test_any_fails_when_no_child_matches(self) -> None:
        context = _context(variables={"entry": {"score": 7}})
        condition = {
            "any": [
                {"field": "entry.score", "operator": "gte", "value": 10},
                {"field": "entry.score", "operator": "lt", "value": 5},
            ]
        }
        assert _EVALUATOR.evaluate(condition, context) is False
