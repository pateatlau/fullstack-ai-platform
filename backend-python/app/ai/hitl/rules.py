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
from typing import Any

from pydantic import BaseModel, Field

from app.ai.security.rules_engine import RuleCondition, RuleEvaluator, RuleOperator

__all__ = [
    "ApprovalRule",
    "PolicyContext",
    "PolicyDecision",
    "RuleCondition",
    "RuleEvaluator",
    "RuleOperator",
    "RuleOutcome",
    "RulePolicyEngine",
    "load_rules_from_config",
]


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


class ApprovalRule(BaseModel):
    """One ordered rule: if ``condition`` matches, produce ``outcome``."""

    name: str
    description: str | None = None
    priority: int = 100
    condition: RuleCondition
    outcome: RuleOutcome
    # Sequential approval-stage scaffold (recommendation #5). Recorded and
    # enforced in order by ``AgentApprovalService.decide``/``record_stage_approval``
    # when non-empty. Each stage string is interpreted as an RBAC permission
    # key: the deciding user must hold it (or ``approvals:decide_all``) when
    # Security & Governance RBAC enforcement is enabled (Epic 11 Phase 2);
    # otherwise this remains an unenforced, auditable checklist as before.
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
