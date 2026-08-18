"""Deterministic, priority-ordered guardrail rule evaluation."""

from __future__ import annotations

from app.ai.security.guardrails.models import (
    GuardrailAction,
    GuardrailContext,
    GuardrailRule,
    GuardrailVerdict,
)
from app.ai.security.redaction import redact_secret_patterns
from app.ai.security.rules_engine import RuleEvaluator

_EVIDENCE_MAX_CHARS = 160


class GuardrailEngine:
    """Evaluate content against ordered rules; the strongest match wins."""

    def __init__(
        self,
        rules: list[GuardrailRule] | tuple[GuardrailRule, ...],
        *,
        default_mode: GuardrailAction,
    ) -> None:
        self._rules = tuple(sorted(rules, key=lambda rule: rule.priority))
        self._default_mode = default_mode
        self._evaluator = RuleEvaluator()

    @property
    def rules(self) -> tuple[GuardrailRule, ...]:
        return self._rules

    @property
    def default_mode(self) -> GuardrailAction:
        return self._default_mode

    def evaluate(self, context: GuardrailContext) -> GuardrailVerdict:
        selected: tuple[GuardrailRule, GuardrailAction] | None = None
        action_strength = {
            GuardrailAction.ALLOW: 0,
            GuardrailAction.FLAG: 1,
            GuardrailAction.BLOCK: 2,
        }

        for rule in self._rules:
            if not self._evaluator.evaluate(rule.condition, context):
                continue
            action = (
                self._default_mode
                if rule.action is GuardrailAction.FLAG
                else rule.action
            )
            if (
                selected is None
                or action_strength[action] > action_strength[selected[1]]
            ):
                selected = (rule, action)

        if selected is None:
            return GuardrailVerdict(action=GuardrailAction.ALLOW)

        rule, action = selected
        evidence = redact_secret_patterns(context.content_text[:_EVIDENCE_MAX_CHARS])
        return GuardrailVerdict(
            action=action,
            matched_rule_id=rule.id,
            matched_rule_version=rule.version,
            evidence_snippet=evidence,
        )
