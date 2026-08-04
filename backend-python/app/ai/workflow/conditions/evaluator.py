"""Declarative condition DSL evaluator (Phase 4)."""

from __future__ import annotations

from app.ai.workflow.models import WorkflowContext


class ConditionEvaluator:
    """Evaluates edge conditions against ``WorkflowContext`` (Part I § ConditionEvaluator)."""

    def evaluate(self, condition: dict[str, object], context: WorkflowContext) -> bool:
        """Return whether the condition matches the current context."""
        del condition, context
        raise NotImplementedError(
            "ConditionEvaluator.evaluate() is implemented in Phase 4."
        )
