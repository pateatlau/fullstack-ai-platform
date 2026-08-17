"""Generic condition-tree models and evaluation shared by governance policies."""

from __future__ import annotations

import enum
import re
from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator

_REGEX_MAX_PATTERN_LEN = 256
_REGEX_MAX_INPUT_LEN = 4096


class RuleContext(Protocol):
    """Structural context contract consumed by the generic rule evaluator."""

    def resolve_field(self, field: str) -> Any: ...


class RuleOperator(str, enum.Enum):
    """Comparison applied between a resolved context field and a rule value."""

    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    REGEX = "regex"


class RuleCondition(BaseModel):
    """A leaf comparison or a boolean composition of nested conditions.

    Exactly one of the composite operators (``all_of``/``any_of``/``not_``)
    or the leaf trio (``field``/``operator``/``value``) must be set.
    """

    all_of: list["RuleCondition"] | None = None
    any_of: list["RuleCondition"] | None = None
    not_: "RuleCondition | None" = Field(default=None, alias="not")
    field: str | None = None
    operator: RuleOperator | None = None
    value: Any = None
    compiled_regex: re.Pattern[str] | None = Field(
        default=None, exclude=True, repr=False
    )

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate_exactly_one_mode(self) -> "RuleCondition":
        composite_modes = [
            self.all_of is not None,
            self.any_of is not None,
            self.not_ is not None,
        ]
        is_leaf = self.field is not None
        modes_set = sum(composite_modes) + (1 if is_leaf else 0)
        if modes_set != 1:
            raise ValueError(
                "RuleCondition must set exactly one of all_of/any_of/not/"
                "(field+operator+value)."
            )
        if is_leaf and self.operator is None:
            raise ValueError("Leaf RuleCondition requires an operator.")
        return self

    @model_validator(mode="after")
    def _compile_regex_pattern(self) -> "RuleCondition":
        if self.field is None or self.operator is not RuleOperator.REGEX:
            return self
        if not isinstance(self.value, str):
            raise ValueError("REGEX rule value must be a string pattern.")
        if len(self.value) > _REGEX_MAX_PATTERN_LEN:
            raise ValueError(
                f"REGEX pattern exceeds maximum length of {_REGEX_MAX_PATTERN_LEN}."
            )
        try:
            self.compiled_regex = re.compile(self.value)
        except re.error as exc:
            raise ValueError(f"Invalid REGEX pattern {self.value!r}: {exc}") from exc
        return self


def _compare(operator: RuleOperator, actual: Any, expected: Any) -> bool:
    if operator is RuleOperator.EQ:
        return actual == expected
    if operator is RuleOperator.NE:
        return actual != expected
    if operator is RuleOperator.IN:
        return actual in expected if expected is not None else False
    if operator is RuleOperator.NOT_IN:
        return actual not in expected if expected is not None else True
    if actual is None:
        return False
    if operator is RuleOperator.GT:
        return actual > expected
    if operator is RuleOperator.GTE:
        return actual >= expected
    if operator is RuleOperator.LT:
        return actual < expected
    if operator is RuleOperator.LTE:
        return actual <= expected
    if operator is RuleOperator.CONTAINS:
        return expected in actual
    raise ValueError(f"Unsupported rule operator: {operator}")  # pragma: no cover


def _regex_matches(actual: Any, pattern: re.Pattern[str]) -> bool:
    """Return whether ``actual`` matches a precompiled, bounded regex pattern."""
    if actual is None:
        return False
    text = str(actual)
    if len(text) > _REGEX_MAX_INPUT_LEN:
        return False
    return bool(pattern.search(text))


class RuleEvaluator:
    """Stateless recursive evaluator for :class:`RuleCondition` trees."""

    def evaluate(self, condition: RuleCondition, context: RuleContext) -> bool:
        if condition.all_of is not None:
            return all(self.evaluate(child, context) for child in condition.all_of)
        if condition.any_of is not None:
            return any(self.evaluate(child, context) for child in condition.any_of)
        if condition.not_ is not None:
            return not self.evaluate(condition.not_, context)

        assert condition.field is not None and condition.operator is not None
        actual = context.resolve_field(condition.field)
        if condition.operator is RuleOperator.REGEX:
            if condition.compiled_regex is None:
                return False
            return _regex_matches(actual, condition.compiled_regex)
        try:
            return _compare(condition.operator, actual, condition.value)
        except TypeError:
            return False
