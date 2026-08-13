from __future__ import annotations

from enum import Enum

from app.ai.security.rbac.models import PermissionDefinition


class PermissionKey(str, Enum):
    """Colon-namespaced permission vocabulary for the platform."""

    ALL = "*"
    RBAC_MANAGE = "rbac:manage"
    AUDIT_VIEW = "audit:view"
    POLICY_VIEW = "policy:view"
    JOBS_VIEW_ALL = "jobs:view_all"
    JOBS_RETRY = "jobs:retry"
    APPROVALS_DECIDE_ALL = "approvals:decide_all"
    TOOLS_EXECUTE = "tools:execute"
    TOOLS_EXECUTE_DESTRUCTIVE = "tools:execute:destructive"
    PLUGINS_MANAGE = "plugins:manage"
    WORKFLOW_VIEW_ALL = "workflow:view_all"
    MCP_MANAGE = "mcp:manage"

    def __str__(self) -> str:
        return self.value


PERMISSION_REGISTRY: dict[PermissionKey, PermissionDefinition] = {
    PermissionKey.ALL: PermissionDefinition(
        key="*",
        display_name="All Permissions",
        description="Wildcard permission for owners",
        category="rbac",
        risk_level="high",
        reserved=False,
    ),
    PermissionKey.RBAC_MANAGE: PermissionDefinition(
        key="rbac:manage",
        display_name="Manage Roles",
        description="Manage RBAC assignments",
        category="rbac",
        risk_level="high",
        reserved=False,
    ),
    PermissionKey.AUDIT_VIEW: PermissionDefinition(
        key="audit:view",
        display_name="View Audit Log",
        description="View audit events",
        category="audit",
        risk_level="medium",
        reserved=False,
    ),
    PermissionKey.POLICY_VIEW: PermissionDefinition(
        key="policy:view",
        display_name="View Policy Summary",
        description="Read policy summary",
        category="policy",
        risk_level="low",
        reserved=False,
    ),
    PermissionKey.JOBS_VIEW_ALL: PermissionDefinition(
        key="jobs:view_all",
        display_name="View Background Jobs",
        description="List and inspect all jobs",
        category="jobs",
        risk_level="medium",
        reserved=False,
    ),
    PermissionKey.JOBS_RETRY: PermissionDefinition(
        key="jobs:retry",
        display_name="Retry Dead-Letter Jobs",
        description="Retry failed background jobs",
        category="jobs",
        risk_level="high",
        reserved=False,
    ),
    PermissionKey.APPROVALS_DECIDE_ALL: PermissionDefinition(
        key="approvals:decide_all",
        display_name="Decide Any Approval",
        description="Act on all approval stages",
        category="approvals",
        risk_level="high",
        reserved=False,
    ),
    PermissionKey.TOOLS_EXECUTE: PermissionDefinition(
        key="tools:execute",
        display_name="Execute Tools",
        description="Execute non-destructive tools",
        category="tools",
        risk_level="low",
        reserved=False,
    ),
    PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE: PermissionDefinition(
        key="tools:execute:destructive",
        display_name="Execute Destructive Tools",
        description="Execute high-risk destructive tool calls",
        category="tools",
        risk_level="high",
        reserved=False,
    ),
    PermissionKey.PLUGINS_MANAGE: PermissionDefinition(
        key="plugins:manage",
        display_name="Manage Plugins",
        description="Manage plugins",
        category="plugins",
        risk_level="high",
        reserved=True,
    ),
    PermissionKey.WORKFLOW_VIEW_ALL: PermissionDefinition(
        key="workflow:view_all",
        display_name="View All Workflows",
        description="Inspect all workflows",
        category="workflow",
        risk_level="medium",
        reserved=True,
    ),
    PermissionKey.MCP_MANAGE: PermissionDefinition(
        key="mcp:manage",
        display_name="Manage MCP Servers",
        description="Configure MCP servers",
        category="mcp",
        risk_level="high",
        reserved=True,
    ),
}

DEFAULT_ROLE_PERMISSIONS: dict[str, set[PermissionKey]] = {
    "member": {
        PermissionKey.TOOLS_EXECUTE,
    },
    "operator": {
        PermissionKey.TOOLS_EXECUTE,
        PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE,
        PermissionKey.JOBS_VIEW_ALL,
        PermissionKey.JOBS_RETRY,
        PermissionKey.POLICY_VIEW,
        PermissionKey.AUDIT_VIEW,
    },
    "admin": {
        PermissionKey.TOOLS_EXECUTE,
        PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE,
        PermissionKey.JOBS_VIEW_ALL,
        PermissionKey.JOBS_RETRY,
        PermissionKey.POLICY_VIEW,
        PermissionKey.AUDIT_VIEW,
        PermissionKey.RBAC_MANAGE,
        PermissionKey.APPROVALS_DECIDE_ALL,
    },
    "owner": {
        PermissionKey.ALL,
        PermissionKey.RBAC_MANAGE,
        PermissionKey.AUDIT_VIEW,
        PermissionKey.POLICY_VIEW,
        PermissionKey.JOBS_VIEW_ALL,
        PermissionKey.JOBS_RETRY,
        PermissionKey.APPROVALS_DECIDE_ALL,
        PermissionKey.TOOLS_EXECUTE,
        PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE,
        PermissionKey.PLUGINS_MANAGE,
        PermissionKey.WORKFLOW_VIEW_ALL,
        PermissionKey.MCP_MANAGE,
    },
}
