"""Approval policy — single decision point for tool-call gating."""

from __future__ import annotations

from app.ai.hitl.rules import (
    PolicyContext,
    PolicyDecision,
    RuleOutcome,
    RulePolicyEngine,
)
from app.ai.tools.schemas import ToolDefinition


class ApprovalPolicy:
    """Stateless decision function for whether a tool requires human approval.

    ``evaluate()`` is the primary entry point: it consults the optional
    :class:`RulePolicyEngine` first (recommendation #1) and falls back to the
    legacy ``required_tool_names``/``ToolDefinition.requires_approval`` check
    when no rule matches (or no engine is configured), so existing
    deployments keep their exact current behavior with an empty rule set.
    """

    def __init__(
        self,
        *,
        required_tool_names: frozenset[str],
        rule_engine: RulePolicyEngine | None = None,
        environment: str | None = None,
    ) -> None:
        self._required_tool_names = required_tool_names
        self._rule_engine = rule_engine
        self._environment = environment

    def evaluate(
        self,
        tool: ToolDefinition,
        *,
        arguments: dict[str, object] | None = None,
        caller_role: str | None = None,
        workspace: str | None = None,
        tenant: str | None = None,
        estimated_cost: float | None = None,
    ) -> PolicyDecision:
        """Return the full policy decision (outcome + rule/stage metadata)."""
        if self._rule_engine is not None:
            context = PolicyContext(
                tool_name=tool.name,
                tool_category=tool.category,
                risk_level=tool.risk_level,
                data_sensitivity=tool.data_sensitivity,
                caller_role=caller_role,
                workspace=workspace,
                tenant=tenant,
                environment=self._environment,
                tool_arguments=dict(arguments or {}),
                estimated_cost=estimated_cost,
            )
            decision = self._rule_engine.decide(context)
            if decision is not None:
                return decision

        legacy_requires_approval = (
            tool.requires_approval or tool.name in self._required_tool_names
        )
        return PolicyDecision(
            outcome=(
                RuleOutcome.REQUIRE_APPROVAL
                if legacy_requires_approval
                else RuleOutcome.AUTO_APPROVE
            ),
            matched_rule=None,
        )

    def requires_approval(self, tool: ToolDefinition) -> bool:
        """Legacy boolean gate, kept for backward compatibility.

        Equivalent to ``evaluate(tool).outcome != RuleOutcome.AUTO_APPROVE``.
        Prefer :meth:`evaluate` for new call sites so ``reject`` outcomes are
        distinguishable from ``require_approval``.
        """
        return self.evaluate(tool).outcome != RuleOutcome.AUTO_APPROVE
