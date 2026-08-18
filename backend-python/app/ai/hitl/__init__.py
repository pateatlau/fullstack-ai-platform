"""Human-in-the-loop (HITL) — public SDK surface (stable after Phase 1)."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ai.hitl.exceptions import (
        ApprovalDecisionConflictError,
        ApprovalExpiredError,
        ApprovalNotFoundError,
        ApprovalValidationError,
        AgentApprovalPauseError,
        HitlError,
        ToolCallRejectedByPolicyError,
    )
    from app.ai.hitl.models import (
        AgentToolApproval,
        ApprovalAuditEntry,
        ApprovalKind,
        ApprovalResult,
        ApprovalRevision,
        ApprovalStatus,
        ProposedToolCall,
        RequestMetadata,
        StageDecision,
    )
    from app.ai.hitl.notifications import (
        ApprovalNotificationEvent,
        ApprovalNotificationEventType,
        NotificationDispatcher,
        NotificationProvider,
    )
    from app.ai.hitl.policy import ApprovalPolicy
    from app.ai.hitl.rules import (
        ApprovalRule,
        PolicyContext,
        PolicyDecision,
        RuleCondition,
        RuleOperator,
        RuleOutcome,
        RulePolicyEngine,
        load_rules_from_config,
    )
    from app.ai.hitl.service import AgentApprovalService
    from app.ai.hitl.store import AgentToolApprovalStore, ApprovalsStore

__all__ = [
    "AgentApprovalPauseError",
    "AgentApprovalService",
    "AgentToolApproval",
    "AgentToolApprovalStore",
    "ApprovalsStore",
    "ApprovalAuditEntry",
    "ApprovalDecisionConflictError",
    "ApprovalExpiredError",
    "ApprovalKind",
    "ApprovalNotFoundError",
    "ApprovalNotificationEvent",
    "ApprovalNotificationEventType",
    "ApprovalPolicy",
    "ApprovalResult",
    "ApprovalRevision",
    "ApprovalRule",
    "ApprovalStatus",
    "ApprovalValidationError",
    "HitlError",
    "NotificationDispatcher",
    "NotificationProvider",
    "PolicyContext",
    "PolicyDecision",
    "ProposedToolCall",
    "RequestMetadata",
    "RuleCondition",
    "RuleOperator",
    "RuleOutcome",
    "RulePolicyEngine",
    "StageDecision",
    "ToolCallRejectedByPolicyError",
    "load_rules_from_config",
]

_EXPORT_MODULES = {
    "AgentApprovalPauseError": "app.ai.hitl.exceptions",
    "AgentApprovalService": "app.ai.hitl.service",
    "AgentToolApproval": "app.ai.hitl.models",
    "AgentToolApprovalStore": "app.ai.hitl.store",
    "ApprovalsStore": "app.ai.hitl.store",
    "ApprovalAuditEntry": "app.ai.hitl.models",
    "ApprovalDecisionConflictError": "app.ai.hitl.exceptions",
    "ApprovalExpiredError": "app.ai.hitl.exceptions",
    "ApprovalKind": "app.ai.hitl.models",
    "ApprovalNotFoundError": "app.ai.hitl.exceptions",
    "ApprovalNotificationEvent": "app.ai.hitl.notifications",
    "ApprovalNotificationEventType": "app.ai.hitl.notifications",
    "ApprovalPolicy": "app.ai.hitl.policy",
    "ApprovalResult": "app.ai.hitl.models",
    "ApprovalRevision": "app.ai.hitl.models",
    "ApprovalRule": "app.ai.hitl.rules",
    "ApprovalStatus": "app.ai.hitl.models",
    "ApprovalValidationError": "app.ai.hitl.exceptions",
    "HitlError": "app.ai.hitl.exceptions",
    "NotificationDispatcher": "app.ai.hitl.notifications",
    "NotificationProvider": "app.ai.hitl.notifications",
    "PolicyContext": "app.ai.hitl.rules",
    "PolicyDecision": "app.ai.hitl.rules",
    "ProposedToolCall": "app.ai.hitl.models",
    "RequestMetadata": "app.ai.hitl.models",
    "RuleCondition": "app.ai.hitl.rules",
    "RuleOperator": "app.ai.hitl.rules",
    "RuleOutcome": "app.ai.hitl.rules",
    "RulePolicyEngine": "app.ai.hitl.rules",
    "StageDecision": "app.ai.hitl.models",
    "ToolCallRejectedByPolicyError": "app.ai.hitl.exceptions",
    "load_rules_from_config": "app.ai.hitl.rules",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
