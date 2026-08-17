"""Shared guardrail evaluation and audit emission for runtime surfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.security.audit.actions import AuditAction
from app.ai.security.audit.models import AuditOutcome
from app.ai.security.guardrails.models import (
    GuardrailAction,
    GuardrailContext,
    GuardrailVerdict,
)

if TYPE_CHECKING:
    from app.ai.security.audit.logger import AuditLogger
    from app.ai.security.guardrails.engine import GuardrailEngine
    from app.core.caller import CallerContext


async def evaluate_guardrail(
    engine: GuardrailEngine,
    context: GuardrailContext,
    *,
    audit_logger: AuditLogger | None = None,
    actor: CallerContext | None = None,
) -> GuardrailVerdict:
    verdict = engine.evaluate(context)
    if verdict.action is GuardrailAction.ALLOW or audit_logger is None:
        return verdict

    action = (
        AuditAction.GUARDRAIL_BLOCKED
        if verdict.action is GuardrailAction.BLOCK
        else AuditAction.GUARDRAIL_FLAGGED
    )
    outcome = (
        AuditOutcome.DENIED
        if verdict.action is GuardrailAction.BLOCK
        else AuditOutcome.SUCCESS
    )
    await audit_logger.record(
        actor=actor,
        action=action.value,
        outcome=outcome,
        resource_type="guardrail",
        resource_id=verdict.matched_rule_id,
        metadata={
            "rule_id": verdict.matched_rule_id,
            "rule_version": verdict.matched_rule_version,
            "source": context.source,
        },
    )
    return verdict
