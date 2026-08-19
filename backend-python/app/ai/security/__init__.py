"""Security and governance primitives for the platform."""

from app.ai.security.audit.actions import AuditAction
from app.ai.security.audit.logger import AuditLogger
from app.ai.security.audit.models import AuditEvent, AuditOutcome
from app.ai.security.audit.store import AuditStore, PostgresAuditStore
from app.ai.security.exceptions import PermissionDeniedError, RoleNotFoundError
from app.ai.security.guardrails.engine import GuardrailEngine
from app.ai.security.guardrails.models import (
    GuardrailAction,
    GuardrailContext,
    GuardrailRule,
    GuardrailVerdict,
)
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
from app.ai.security.rbac.store import PostgresRoleStore, RoleStore
from app.ai.security.rules_engine import RuleCondition, RuleEvaluator, RuleOperator
from app.ai.security.secrets.resolver import EnvSecretResolver, SecretResolver
from app.ai.security.errors import SecurityErrorCode

__all__ = [
    "DEFAULT_ROLE_PERMISSIONS",
    "PERMISSION_REGISTRY",
    "AuditAction",
    "AuditEvent",
    "AuditLogger",
    "AuditOutcome",
    "AuditStore",
    "AuthorizationDecision",
    "EnvSecretResolver",
    "GuardrailAction",
    "GuardrailContext",
    "GuardrailEngine",
    "GuardrailRule",
    "GuardrailVerdict",
    "Permission",
    "PermissionDeniedError",
    "PermissionKey",
    "PostgresAuditStore",
    "PostgresRoleStore",
    "RbacService",
    "Role",
    "RoleNotFoundError",
    "RuleCondition",
    "RuleEvaluator",
    "RuleOperator",
    "SecretResolver",
    "SecurityErrorCode",
    "UserRoleAssignment",
    "RoleStore",
]
