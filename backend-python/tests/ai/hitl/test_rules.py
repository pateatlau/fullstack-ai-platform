"""Rule-based approval policy engine tests (Epic 09 recommendation #1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.hitl.policy import ApprovalPolicy
from app.ai.hitl.rules import (
    ApprovalRule,
    PolicyContext,
    RuleCondition,
    RuleEvaluator,
    RuleOperator,
    RuleOutcome,
    RulePolicyEngine,
    load_rules_from_config,
)
from app.ai.tools.schemas import ToolDefinition


def _tool(
    *,
    name: str = "delete_file",
    category: str | None = None,
    risk_level: str | None = None,
    data_sensitivity: str | None = None,
    requires_approval: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="test tool",
        parameters={"type": "object", "properties": {}},
        requires_approval=requires_approval,
        category=category,
        risk_level=risk_level,
        data_sensitivity=data_sensitivity,
    )


class TestRuleCondition:
    def test_leaf_requires_operator(self) -> None:
        with pytest.raises(ValidationError):
            RuleCondition(field="risk_level", value="low")

    def test_exactly_one_mode_required(self) -> None:
        with pytest.raises(ValidationError):
            RuleCondition(
                all_of=[
                    RuleCondition(
                        field="risk_level", operator=RuleOperator.EQ, value="low"
                    )
                ],
                field="risk_level",
                operator=RuleOperator.EQ,
                value="low",
            )

    def test_not_alias_accepted(self) -> None:
        condition = RuleCondition.model_validate(
            {"not": {"field": "risk_level", "operator": "eq", "value": "low"}}
        )
        assert condition.not_ is not None


class TestRuleEvaluator:
    def setup_method(self) -> None:
        self.evaluator = RuleEvaluator()

    def test_eq_matches(self) -> None:
        context = PolicyContext(tool_name="delete_file", risk_level="high")
        condition = RuleCondition(
            field="risk_level", operator=RuleOperator.EQ, value="high"
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_ne_matches(self) -> None:
        context = PolicyContext(tool_name="delete_file", risk_level="low")
        condition = RuleCondition(
            field="risk_level", operator=RuleOperator.NE, value="high"
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_in_matches(self) -> None:
        context = PolicyContext(tool_name="delete_file", tool_category="destructive")
        condition = RuleCondition(
            field="tool_category",
            operator=RuleOperator.IN,
            value=["destructive", "admin"],
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_not_in_when_field_missing_treats_none_as_not_in(self) -> None:
        context = PolicyContext(tool_name="delete_file")
        condition = RuleCondition(
            field="tool_category", operator=RuleOperator.NOT_IN, value=["destructive"]
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_gt_gte_lt_lte_on_estimated_cost(self) -> None:
        context = PolicyContext(tool_name="pay", estimated_cost=100.0)
        assert self.evaluator.evaluate(
            RuleCondition(field="estimated_cost", operator=RuleOperator.GT, value=50),
            context,
        )
        assert self.evaluator.evaluate(
            RuleCondition(field="estimated_cost", operator=RuleOperator.GTE, value=100),
            context,
        )
        assert self.evaluator.evaluate(
            RuleCondition(field="estimated_cost", operator=RuleOperator.LT, value=200),
            context,
        )
        assert self.evaluator.evaluate(
            RuleCondition(field="estimated_cost", operator=RuleOperator.LTE, value=100),
            context,
        )

    def test_numeric_comparison_against_none_never_matches(self) -> None:
        context = PolicyContext(tool_name="pay", estimated_cost=None)
        condition = RuleCondition(
            field="estimated_cost", operator=RuleOperator.GT, value=50
        )
        assert self.evaluator.evaluate(condition, context) is False

    def test_contains_matches(self) -> None:
        context = PolicyContext(
            tool_name="delete_file", tool_arguments={"path": "/prod/x"}
        )
        condition = RuleCondition(
            field="tool_arguments.path", operator=RuleOperator.CONTAINS, value="/prod/"
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_regex_matches(self) -> None:
        context = PolicyContext(
            tool_name="delete_file", tool_arguments={"path": "/prod/x.log"}
        )
        condition = RuleCondition(
            field="tool_arguments.path", operator=RuleOperator.REGEX, value=r"\.log$"
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_invalid_regex_pattern_rejected_at_load(self) -> None:
        with pytest.raises(ValidationError, match="Invalid REGEX pattern"):
            RuleCondition(field="tool_name", operator=RuleOperator.REGEX, value="(")

    def test_regex_pattern_length_limit_enforced(self) -> None:
        with pytest.raises(
            ValidationError, match="REGEX pattern exceeds maximum length"
        ):
            RuleCondition(
                field="tool_name",
                operator=RuleOperator.REGEX,
                value="a" * 257,
            )

    def test_regex_scans_long_input_in_bounded_windows(self) -> None:
        context = PolicyContext(
            tool_name="delete_file",
            tool_arguments={"path": "a" * 5000 + "blocked-token"},
        )
        condition = RuleCondition(
            field="tool_arguments.path",
            operator=RuleOperator.REGEX,
            value="blocked-token$",
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_all_of_requires_every_child(self) -> None:
        context = PolicyContext(
            tool_name="delete_file", environment="production", risk_level="low"
        )
        condition = RuleCondition(
            all_of=[
                RuleCondition(
                    field="environment", operator=RuleOperator.EQ, value="production"
                ),
                RuleCondition(
                    field="risk_level", operator=RuleOperator.EQ, value="high"
                ),
            ]
        )
        assert self.evaluator.evaluate(condition, context) is False

    def test_any_of_matches_one_child(self) -> None:
        context = PolicyContext(tool_name="delete_file", environment="production")
        condition = RuleCondition(
            any_of=[
                RuleCondition(
                    field="environment", operator=RuleOperator.EQ, value="staging"
                ),
                RuleCondition(
                    field="environment", operator=RuleOperator.EQ, value="production"
                ),
            ]
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_not_negates_child(self) -> None:
        context = PolicyContext(tool_name="delete_file", risk_level="low")
        condition = RuleCondition.model_validate(
            {"not": {"field": "risk_level", "operator": "eq", "value": "high"}}
        )
        assert self.evaluator.evaluate(condition, context) is True

    def test_type_mismatch_never_matches(self) -> None:
        context = PolicyContext(tool_name="delete_file", estimated_cost=None)
        condition = RuleCondition(
            field="estimated_cost", operator=RuleOperator.GT, value="oops"
        )
        assert self.evaluator.evaluate(condition, context) is False


class TestRulePolicyEngine:
    def test_first_matching_rule_by_priority_wins(self) -> None:
        rules = [
            ApprovalRule(
                name="low-priority-catch-all",
                priority=100,
                outcome=RuleOutcome.REQUIRE_APPROVAL,
                condition=RuleCondition(
                    field="tool_name", operator=RuleOperator.EQ, value="x"
                ),
            ),
            ApprovalRule(
                name="high-priority-reject",
                priority=1,
                outcome=RuleOutcome.REJECT,
                condition=RuleCondition(
                    field="tool_name", operator=RuleOperator.EQ, value="x"
                ),
            ),
        ]
        engine = RulePolicyEngine(rules)
        decision = engine.decide(PolicyContext(tool_name="x"))
        assert decision is not None
        assert decision.outcome is RuleOutcome.REJECT
        assert decision.matched_rule == "high-priority-reject"

    def test_no_match_returns_none(self) -> None:
        engine = RulePolicyEngine(
            [
                ApprovalRule(
                    name="only-x",
                    outcome=RuleOutcome.REJECT,
                    condition=RuleCondition(
                        field="tool_name", operator=RuleOperator.EQ, value="x"
                    ),
                )
            ]
        )
        assert engine.decide(PolicyContext(tool_name="y")) is None

    def test_required_stages_carried_into_decision(self) -> None:
        engine = RulePolicyEngine(
            [
                ApprovalRule(
                    name="multi-stage",
                    outcome=RuleOutcome.REQUIRE_APPROVAL,
                    required_stages=["manager", "security"],
                    condition=RuleCondition(
                        field="tool_name", operator=RuleOperator.EQ, value="delete_file"
                    ),
                )
            ]
        )
        decision = engine.decide(PolicyContext(tool_name="delete_file"))
        assert decision is not None
        assert decision.required_stages == ["manager", "security"]

    def test_empty_rule_set_never_matches(self) -> None:
        engine = RulePolicyEngine([])
        assert engine.decide(PolicyContext(tool_name="anything")) is None


class TestLoadRulesFromConfig:
    def test_parses_valid_rules(self) -> None:
        raw = [
            {
                "name": "reject-prod-deletes",
                "outcome": "reject",
                "priority": 10,
                "condition": {
                    "all_of": [
                        {
                            "field": "environment",
                            "operator": "eq",
                            "value": "production",
                        },
                        {
                            "field": "tool_category",
                            "operator": "eq",
                            "value": "destructive",
                        },
                    ]
                },
            }
        ]
        rules = load_rules_from_config(raw)
        assert len(rules) == 1
        assert rules[0].name == "reject-prod-deletes"
        assert rules[0].outcome is RuleOutcome.REJECT

    def test_invalid_rule_raises(self) -> None:
        with pytest.raises(ValidationError):
            load_rules_from_config([{"name": "bad", "outcome": "reject"}])


class TestApprovalPolicyEvaluate:
    def test_no_rule_engine_falls_back_to_legacy_gate(self) -> None:
        policy = ApprovalPolicy(required_tool_names=frozenset({"delete_file"}))
        decision = policy.evaluate(_tool(name="delete_file"))
        assert decision.outcome is RuleOutcome.REQUIRE_APPROVAL
        assert decision.matched_rule is None

    def test_legacy_auto_approve_when_no_flag(self) -> None:
        policy = ApprovalPolicy(required_tool_names=frozenset())
        decision = policy.evaluate(_tool(name="echo"))
        assert decision.outcome is RuleOutcome.AUTO_APPROVE

    def test_rule_engine_reject_overrides_legacy_flag(self) -> None:
        engine = RulePolicyEngine(
            [
                ApprovalRule(
                    name="reject-destructive-in-prod",
                    outcome=RuleOutcome.REJECT,
                    condition=RuleCondition(
                        all_of=[
                            RuleCondition(
                                field="environment",
                                operator=RuleOperator.EQ,
                                value="production",
                            ),
                            RuleCondition(
                                field="tool_category",
                                operator=RuleOperator.EQ,
                                value="destructive",
                            ),
                        ]
                    ),
                )
            ]
        )
        policy = ApprovalPolicy(
            required_tool_names=frozenset(),
            rule_engine=engine,
            environment="production",
        )
        decision = policy.evaluate(_tool(name="delete_file", category="destructive"))
        assert decision.outcome is RuleOutcome.REJECT
        assert decision.matched_rule == "reject-destructive-in-prod"

    def test_rule_engine_no_match_falls_back_to_legacy(self) -> None:
        engine = RulePolicyEngine(
            [
                ApprovalRule(
                    name="only-payments",
                    outcome=RuleOutcome.REJECT,
                    condition=RuleCondition(
                        field="tool_category", operator=RuleOperator.EQ, value="payment"
                    ),
                )
            ]
        )
        policy = ApprovalPolicy(
            required_tool_names=frozenset({"delete_file"}),
            rule_engine=engine,
        )
        decision = policy.evaluate(_tool(name="delete_file", category="destructive"))
        assert decision.outcome is RuleOutcome.REQUIRE_APPROVAL
        assert decision.matched_rule is None

    def test_requires_approval_treats_reject_as_true(self) -> None:
        engine = RulePolicyEngine(
            [
                ApprovalRule(
                    name="reject-all",
                    outcome=RuleOutcome.REJECT,
                    condition=RuleCondition(
                        field="tool_name", operator=RuleOperator.EQ, value="delete_file"
                    ),
                )
            ]
        )
        policy = ApprovalPolicy(required_tool_names=frozenset(), rule_engine=engine)
        assert policy.requires_approval(_tool(name="delete_file")) is True

    def test_caller_role_and_arguments_available_to_conditions(self) -> None:
        engine = RulePolicyEngine(
            [
                ApprovalRule(
                    name="guest-cannot-delete",
                    outcome=RuleOutcome.REJECT,
                    condition=RuleCondition(
                        all_of=[
                            RuleCondition(
                                field="caller_role",
                                operator=RuleOperator.EQ,
                                value="guest",
                            ),
                            RuleCondition(
                                field="tool_arguments.path",
                                operator=RuleOperator.CONTAINS,
                                value="/prod/",
                            ),
                        ]
                    ),
                )
            ]
        )
        policy = ApprovalPolicy(required_tool_names=frozenset(), rule_engine=engine)
        decision = policy.evaluate(
            _tool(name="delete_file"),
            arguments={"path": "/prod/data"},
            caller_role="guest",
        )
        assert decision.outcome is RuleOutcome.REJECT
