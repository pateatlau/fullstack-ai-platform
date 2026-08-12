"""Rule-based approval policy engine (Epic 09 recommendation #1).

Evolves ``ApprovalPolicy`` from a static tool-metadata check into a
configurable, ordered rule system. Rules are pure config (constructor input)
and evaluation is a stateless pure function — no persistence, HTTP, or
notifications live here, matching the existing ``ApprovalPolicy`` contract.

Rules are typically sourced from ``Settings.hitl_policy_rules`` (a list of
plain dicts, matching the existing ``mcp_permission_policy``/``mcp_servers``
config convention) via :func:`load_rules_from_config`. Example::

    hitl_policy_rules:
      - name: "reject-deletes-in-production"
        outcome: reject
        priority: 10
        condition:
          all_of:
            - field: environment
              operator: eq
              value: production
            - field: tool_category
              operator: eq
              value: destructive
      - name: "auto-approve-read-only"
        outcome: auto_approve
        priority: 50
        condition:
          field: risk_level
          operator: eq
          value: low

Caller "role" is intentionally limited to the caller kind (``user``/``guest``)
until Epic 11 introduces real RBAC — see ``PolicyContext.caller_role``.
"""

from __future__ import annotations

import enum
import re
from typing import Any

from pydantic import BaseModel, Field, model_validator


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


class RuleOutcome(str, enum.Enum):
    """Terminal decision a matching rule (or the policy default) produces."""

    AUTO_APPROVE = "auto_approve"
    REQUIRE_APPROVAL = "require_approval"
    REJECT = "reject"


class PolicyContext(BaseModel):
    """Inputs available to rule conditions for one proposed tool call."""

    tool_name: str
    tool_category: str | None = None
    risk_level: str | None = None
    data_sensitivity: str | None = None
    caller_role: str | None = None
    workspace: str | None = None
    tenant: str | None = None
    environment: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    estimated_cost: float | None = None

    def resolve_field(self, field: str) -> Any:
        """Resolve a dotted field path (for example ``tool_arguments.amount``)."""
        if field == "tool_arguments" or not field.startswith("tool_arguments."):
            return getattr(self, field, None) if hasattr(self, field) else None
        _, _, key = field.partition(".")
        return self.tool_arguments.get(key)


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
    if operator is RuleOperator.REGEX:
        return bool(re.search(str(expected), str(actual)))
    raise ValueError(f"Unsupported rule operator: {operator}")  # pragma: no cover


class RuleEvaluator:
    """Stateless recursive evaluator for :class:`RuleCondition` trees."""

    def evaluate(self, condition: RuleCondition, context: PolicyContext) -> bool:
        if condition.all_of is not None:
            return all(self.evaluate(child, context) for child in condition.all_of)
        if condition.any_of is not None:
            return any(self.evaluate(child, context) for child in condition.any_of)
        if condition.not_ is not None:
            return not self.evaluate(condition.not_, context)

        assert condition.field is not None and condition.operator is not None
        actual = context.resolve_field(condition.field)
        try:
            return _compare(condition.operator, actual, condition.value)
        except TypeError:
            # Mismatched types (e.g. comparing None against a number) never match.
            return False


class ApprovalRule(BaseModel):
    """One ordered rule: if ``condition`` matches, produce ``outcome``."""

    name: str
    description: str | None = None
    priority: int = 100
    condition: RuleCondition
    outcome: RuleOutcome
    # Sequential approval-stage scaffold (recommendation #5). Recorded and
    # enforced in order by ``AgentApprovalService.decide`` when non-empty.
    # Real per-role *identity* enforcement requires Epic 11 RBAC; today any
    # owner can satisfy any stage, so this behaves as an auditable checklist.
    required_stages: list[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    """Result of evaluating a :class:`PolicyContext` against a rule set."""

    outcome: RuleOutcome
    matched_rule: str | None = None
    required_stages: list[str] = Field(default_factory=list)


class RulePolicyEngine:
    """Stateless, ordered rule matcher. First matching rule (by priority) wins."""

    def __init__(
        self,
        rules: list[ApprovalRule] | tuple[ApprovalRule, ...],
        *,
        evaluator: RuleEvaluator | None = None,
    ) -> None:
        self._rules: tuple[ApprovalRule, ...] = tuple(
            sorted(rules, key=lambda rule: rule.priority)
        )
        self._evaluator = evaluator or RuleEvaluator()

    @property
    def rules(self) -> tuple[ApprovalRule, ...]:
        return self._rules

    def decide(self, context: PolicyContext) -> PolicyDecision | None:
        """Return the first matching rule's decision, or ``None`` if no rule matches."""
        for rule in self._rules:
            if self._evaluator.evaluate(rule.condition, context):
                return PolicyDecision(
                    outcome=rule.outcome,
                    matched_rule=rule.name,
                    required_stages=list(rule.required_stages),
                )
        return None


def load_rules_from_config(raw_rules: list[dict[str, Any]]) -> tuple[ApprovalRule, ...]:
    """Parse ``Settings.hitl_policy_rules`` into validated, ordered rules."""
    return tuple(ApprovalRule.model_validate(entry) for entry in raw_rules)
