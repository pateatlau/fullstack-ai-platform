"""Declarative condition DSL evaluator (Phase 4)."""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Real

from app.ai.workflow.conditions.field_resolution import resolve_field
from app.ai.workflow.models import WorkflowContext


class ConditionEvaluator:
    """Evaluates edge conditions against ``WorkflowContext`` (Part I § ConditionEvaluator)."""

    def evaluate(
        self, condition: Mapping[str, object], context: WorkflowContext
    ) -> bool:
        """Return whether the condition matches the current context."""
        if "all" in condition or "any" in condition:
            return self._evaluate_composite(condition, context)
        return self._evaluate_leaf(condition, context)

    def _evaluate_composite(
        self, condition: Mapping[str, object], context: WorkflowContext
    ) -> bool:
        if "all" in condition and "any" in condition:
            return False
        if "all" in condition:
            children = condition["all"]
            if not isinstance(children, list) or not children:
                return False
            return all(
                isinstance(child, dict) and self.evaluate(child, context)
                for child in children
            )
        children = condition.get("any")
        if not isinstance(children, list) or not children:
            return False
        return any(
            isinstance(child, dict) and self.evaluate(child, context)
            for child in children
        )

    def _evaluate_leaf(
        self, condition: Mapping[str, object], context: WorkflowContext
    ) -> bool:
        field = condition.get("field")
        operator = condition.get("operator")
        if not isinstance(field, str) or not isinstance(operator, str):
            return False

        exists, field_value = resolve_field(field, context)
        if operator == "exists":
            return exists

        if not exists:
            return False

        expected = condition.get("value")
        if operator == "eq":
            return field_value == expected
        if operator == "neq":
            return field_value != expected
        if operator in {"gt", "gte", "lt", "lte"}:
            return self._compare_ordered(field_value, expected, operator)
        if operator == "in":
            if not isinstance(expected, (list, tuple, set, frozenset)):
                return False
            return field_value in expected
        if operator == "contains":
            if isinstance(field_value, str) and isinstance(expected, str):
                return expected in field_value
            if isinstance(field_value, (list, tuple, set, frozenset)):
                return expected in field_value
            if isinstance(field_value, dict):
                return expected in field_value
            return False
        return False

    @staticmethod
    def _compare_ordered(left: object, right: object, operator: str) -> bool:
        if not isinstance(left, Real) or isinstance(left, bool):
            return False
        if not isinstance(right, Real) or isinstance(right, bool):
            return False
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "lt":
            return left < right
        return left <= right
