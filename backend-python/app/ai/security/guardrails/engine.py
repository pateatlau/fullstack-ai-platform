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
    """Evaluate content against ordered rules; the first match wins."""

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
        for rule in self._rules:
            if self._evaluator.evaluate(rule.condition, context):
                evidence = redact_secret_patterns(context.content_text)
                action = rule.action
                if action is GuardrailAction.FLAG:
                    action = self._default_mode
                return GuardrailVerdict(
                    action=action,
                    matched_rule_id=rule.id,
                    matched_rule_version=rule.version,
                    evidence_snippet=evidence[:_EVIDENCE_MAX_CHARS],
                )
        return GuardrailVerdict(action=GuardrailAction.ALLOW)
