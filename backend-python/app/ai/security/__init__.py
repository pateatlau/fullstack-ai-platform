"""Security and governance primitives for the platform."""

from app.ai.security.errors import SecurityErrorCode
from app.ai.security.exceptions import PermissionDeniedError, RoleNotFoundError
from app.ai.security.rbac.models import (
    AuthorizationDecision,
    Permission,
    Role,
    UserRoleAssignment,
)
from app.ai.security.rbac.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_REGISTRY,
    PermissionKey,
)
from app.ai.security.rbac.service import RbacService
from app.ai.security.rbac.store import PostgresRoleStore

__all__ = [
    "DEFAULT_ROLE_PERMISSIONS",
    "PERMISSION_REGISTRY",
    "AuthorizationDecision",
    "Permission",
    "PermissionDeniedError",
    "PermissionKey",
    "PostgresRoleStore",
    "RbacService",
    "Role",
    "RoleNotFoundError",
    "SecurityErrorCode",
    "UserRoleAssignment",
]
