"""Authorization policy for tool invocation."""

from __future__ import annotations

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
        if context.caller.kind != "user":
            return "Tool invocation requires an authenticated user"

        if not self._rbac_enforced():
            return None

        assert self._rbac_service is not None
        decision = await self._rbac_service.authorize(
            context.caller.user_id, PermissionKey.TOOLS_EXECUTE
        )
        if not decision.allowed:
            return "Tool invocation requires the 'tools:execute' permission"

        if _is_destructive(tool):
            destructive = await self._rbac_service.authorize(
                context.caller.user_id, PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE
            )
            if not destructive.allowed:
                return (
                    "Destructive tool invocation requires the "
                    "'tools:execute:destructive' permission"
                )
        return None
