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


class ApprovalExpiredError(HitlError):
    """Raised when a pending approval is touched after its expiration deadline."""


class ToolCallRejectedByPolicyError(HitlError):
    """Raised when the rule-based policy rejects a proposed tool call outright.

    Unlike a pause, a rejected call never reaches a human reviewer — the
    policy has already decided the call must not run.
    """

    def __init__(self, tool_name: str, *, matched_rule: str | None) -> None:
        self.tool_name = tool_name
        self.matched_rule = matched_rule
        suffix = f" (rule: {matched_rule})" if matched_rule else ""
        super().__init__(f"Tool call '{tool_name}' was rejected by policy{suffix}.")


class AgentApprovalPauseError(HitlError):
    """Raised when an agent tool step pauses for human approval."""

    def __init__(self, approval: AgentToolApproval) -> None:
        self.approval = approval
        super().__init__(f"Agent execution paused awaiting approval {approval.id}.")
