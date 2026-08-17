"""Security and governance primitives for the platform."""

from importlib import import_module
from typing import Any

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
]

_EXPORT_MODULES = {
    "AuditAction": "app.ai.security.audit.actions",
    "AuditEvent": "app.ai.security.audit.models",
    "AuditLogger": "app.ai.security.audit.logger",
    "AuditOutcome": "app.ai.security.audit.models",
    "AuditStore": "app.ai.security.audit.store",
    "AuthorizationDecision": "app.ai.security.rbac.models",
    "DEFAULT_ROLE_PERMISSIONS": "app.ai.security.rbac.permissions",
    "EnvSecretResolver": "app.ai.security.secrets.resolver",
    "GuardrailAction": "app.ai.security.guardrails.models",
    "GuardrailContext": "app.ai.security.guardrails.models",
    "GuardrailEngine": "app.ai.security.guardrails.engine",
    "GuardrailRule": "app.ai.security.guardrails.models",
    "GuardrailVerdict": "app.ai.security.guardrails.models",
    "PERMISSION_REGISTRY": "app.ai.security.rbac.permissions",
    "Permission": "app.ai.security.rbac.models",
    "PermissionDeniedError": "app.ai.security.exceptions",
    "PermissionKey": "app.ai.security.rbac.permissions",
    "PostgresAuditStore": "app.ai.security.audit.store",
    "PostgresRoleStore": "app.ai.security.rbac.store",
    "RbacService": "app.ai.security.rbac.service",
    "Role": "app.ai.security.rbac.models",
    "RoleNotFoundError": "app.ai.security.exceptions",
    "RuleCondition": "app.ai.security.rules_engine",
    "RuleEvaluator": "app.ai.security.rules_engine",
    "RuleOperator": "app.ai.security.rules_engine",
    "SecretResolver": "app.ai.security.secrets.resolver",
    "SecurityErrorCode": "app.ai.security.errors",
    "UserRoleAssignment": "app.ai.security.rbac.models",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
