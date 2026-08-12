"""Human-in-the-loop (HITL) — public SDK surface (stable after Phase 1)."""

from __future__ import annotations

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
