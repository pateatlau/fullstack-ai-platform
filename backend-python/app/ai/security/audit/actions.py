"""Canonical audit action taxonomy (Part I § Audit Event Taxonomy).

New actions must be added here before use — no ad-hoc action strings in
application code. ``AuditLogger.record()`` validates membership at call time.
"""

from __future__ import annotations

from enum import Enum


class AuditAction(str, Enum):
    """``{resource}.{verb}`` (or ``{resource}.{subresource}.{verb}``) action names."""

    # RBAC
    ROLE_ASSIGNED = "role.assigned"
    ROLE_REVOKED = "role.revoked"

    # Auth
    LOGIN_SUCCEEDED = "login.succeeded"

    # Tools
    TOOL_EXECUTION_DENIED = "tool.execution.denied"
    TOOL_EXECUTION_SUCCEEDED = "tool.execution.succeeded"

    # HITL
    APPROVAL_DECIDED = "approval.decided"
    APPROVAL_STAGE_COMPLETED = "approval.stage.completed"
    APPROVAL_STAGE_DENIED = "approval.stage.denied"

    # Jobs
    JOB_RETRIED = "job.retried"

    # MCP
    MCP_PERMISSION_DENIED = "mcp.permission.denied"

    # Guardrails
    GUARDRAIL_FLAGGED = "guardrail.flagged"
    GUARDRAIL_BLOCKED = "guardrail.blocked"

    # Secrets
    SECRET_RESOLUTION_MISSING = "secret.resolution.missing"

    # Rate limits
    RATE_LIMIT_EXCEEDED = "rate_limit.exceeded"

    def __str__(self) -> str:
        return self.value
