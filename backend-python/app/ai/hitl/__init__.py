"""Human-in-the-loop (HITL) — public SDK surface (stable after Phase 1)."""

from __future__ import annotations

from app.ai.hitl.exceptions import (
    ApprovalDecisionConflictError,
    ApprovalNotFoundError,
    ApprovalValidationError,
    HitlError,
)
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalAuditEntry,
    ApprovalKind,
    ApprovalResult,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
)
from app.ai.hitl.policy import ApprovalPolicy

__all__ = [
    "AgentToolApproval",
    "ApprovalAuditEntry",
    "ApprovalDecisionConflictError",
    "ApprovalKind",
    "ApprovalNotFoundError",
    "ApprovalPolicy",
    "ApprovalResult",
    "ApprovalRevision",
    "ApprovalStatus",
    "ApprovalValidationError",
    "HitlError",
    "ProposedToolCall",
]
