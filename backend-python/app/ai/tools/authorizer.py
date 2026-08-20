"""Authorization policy for tool invocation."""

from __future__ import annotations

from app.ai.security.observability.wrappers import (
    authz_span_context,
    record_authz_allowed,
    record_authz_denial,
)
from app.ai.security.rbac.permissions import PermissionKey
from app.ai.security.rbac.service import RbacService
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext
from app.core.config import Settings


def _is_destructive(tool: ToolDefinition) -> bool:
    return tool.risk_level == "high" or tool.category == "destructive"


class ToolAuthorizer:
    """Authenticated users may invoke tools; guests are denied (V1 baseline).

    Epic 11 Phase 2: when ``security_governance_enabled`` and
    ``security_rbac_enforcement_enabled`` are both true, authenticated
    callers must additionally hold ``tools:execute`` (baseline) and, for
    destructive tools (``risk_level="high"`` or ``category="destructive"``),
    ``tools:execute:destructive``. With RBAC disabled (or no service wired),
    behaviour is byte-for-byte the V1 authenticated-only check.
    """

    def __init__(
        self,
        *,
        rbac_service: RbacService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._rbac_service = rbac_service
        self._settings = settings

    def _rbac_enforced(self) -> bool:
        return bool(
            self._rbac_service is not None
            and self._settings is not None
            and self._settings.security_governance_enabled
            and self._settings.security_rbac_enforcement_enabled
        )

    async def authorize(
        self,
        tool: ToolDefinition,
        context: ToolExecutionContext,
    ) -> str | None:
        actor_user_id = str(context.caller.user_id) if context.caller.user_id else None
        permission_key = str(PermissionKey.TOOLS_EXECUTE)

        with authz_span_context(
            actor_user_id=actor_user_id,
            permission_key=permission_key,
        ) as span:
            if context.caller.kind != "user":
                record_authz_denial(
                    span,
                    actor_user_id=actor_user_id,
                    permission_key=permission_key,
                    resource_type="tool",
                )
                return "Tool invocation requires an authenticated user"

            if not self._rbac_enforced():
                record_authz_allowed(
                    span,
                    actor_user_id=actor_user_id,
                    permission_key=permission_key,
                    resource_type="tool",
                )
                return None

            assert self._rbac_service is not None
            decision = await self._rbac_service.authorize(
                context.caller.user_id, PermissionKey.TOOLS_EXECUTE
            )
            if not decision.allowed:
                record_authz_denial(
                    span,
                    actor_user_id=actor_user_id,
                    permission_key=permission_key,
                    resource_type="tool",
                )
                return "Tool invocation requires the 'tools:execute' permission"

            if _is_destructive(tool):
                destructive_permission = str(PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE)
                destructive = await self._rbac_service.authorize(
                    context.caller.user_id, PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE
                )
                if not destructive.allowed:
                    record_authz_denial(
                        span,
                        actor_user_id=actor_user_id,
                        permission_key=destructive_permission,
                        resource_type="tool",
                    )
                    return (
                        "Destructive tool invocation requires the "
                        "'tools:execute:destructive' permission"
                    )

            record_authz_allowed(
                span,
                actor_user_id=actor_user_id,
                permission_key=permission_key,
                resource_type="tool",
            )
            return None
