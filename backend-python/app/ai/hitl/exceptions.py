"""Human-in-the-loop (HITL) exceptions."""

from __future__ import annotations

from app.ai.hitl.models import AgentToolApproval


class HitlError(Exception):
    """Base class for HITL-related errors."""


class ApprovalNotFoundError(HitlError):
    """Raised when an approval record cannot be found or is not owned by the caller."""


class ApprovalDecisionConflictError(HitlError):
    """Raised when a decision or revise conflicts with the current approval state."""


class ApprovalValidationError(HitlError):
    """Raised when edited tool-call arguments fail schema validation."""


class AgentApprovalPauseError(HitlError):
    """Raised when an agent tool step pauses for human approval."""

    def __init__(self, approval: AgentToolApproval) -> None:
        self.approval = approval
        super().__init__(f"Agent execution paused awaiting approval {approval.id}.")
