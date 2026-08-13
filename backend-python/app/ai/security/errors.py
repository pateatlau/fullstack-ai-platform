from __future__ import annotations

from enum import Enum


class SecurityErrorCode(str, Enum):
    """Closed set of security and governance denial codes."""

    PERMISSION_DENIED = "permission_denied"
    ROLE_NOT_FOUND = "role_not_found"
    ROLE_ASSIGNMENT_INVALID = "role_assignment_invalid"
    STAGE_PERMISSION_INVALID = "stage_permission_invalid"
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    RATE_LIMITED = "rate_limited"
