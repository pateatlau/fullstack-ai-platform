"""Epic 11 Phase 2: RBAC-aware ``ToolAuthorizer`` tests."""

from __future__ import annotations

import uuid

import pytest

from app.ai.security.rbac.models import Role, UserRoleAssignment
from app.ai.security.rbac.service import RbacService
from app.ai.tools.authorizer import ToolAuthorizer
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext
from app.core.caller import CallerContext
from app.core.config import Settings


class _FakeRoleStore:
    def __init__(self, *, user_roles: dict[uuid.UUID, set[str]]) -> None:
        self._user_roles = user_roles

    async def list_roles(self) -> list[Role]:
        return []

    async def get_role_by_name(self, name: str) -> Role | None:
        return None

    async def get_permission_keys_for_user(self, user_id: uuid.UUID) -> set[str]:
        return set()

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        return sorted(self._user_roles.get(user_id, set()))

    async def assign_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        raise NotImplementedError

    async def revoke_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        raise NotImplementedError

    async def bootstrap_admins(self, emails: list[str]) -> int:
        raise NotImplementedError

    async def get_user_role_assignments(
        self, user_id: uuid.UUID
    ) -> list[UserRoleAssignment]:
        raise NotImplementedError


def _tool(
    *, risk_level: str | None = None, category: str | None = None
) -> ToolDefinition:
    return ToolDefinition(
        name="delete_everything",
        description="destructive test tool",
        parameters={"type": "object", "properties": {}},
        risk_level=risk_level,
        category=category,
    )


def _context(user_id: uuid.UUID | None) -> ToolExecutionContext:
    caller = (
        CallerContext.for_user(user_id)
        if user_id is not None
        else CallerContext.anonymous(guest_id=uuid.uuid4())
    )
    return ToolExecutionContext(caller=caller)


@pytest.mark.anyio
async def test_guest_denied_regardless_of_rbac() -> None:
    authorizer = ToolAuthorizer()
    result = await authorizer.authorize(_tool(), _context(None))
    assert result is not None


@pytest.mark.anyio
async def test_no_settings_or_service_preserves_v1_authenticated_only_behaviour() -> (
    None
):
    """No RbacService/Settings wired -> byte-for-byte V1 authenticated-only check."""
    authorizer = ToolAuthorizer()
    result = await authorizer.authorize(
        _tool(risk_level="high"), _context(uuid.uuid4())
    )
    assert result is None


@pytest.mark.anyio
async def test_flag_off_preserves_v1_behaviour_even_with_service_wired() -> None:
    user_id = uuid.uuid4()
    rbac_service = RbacService(_FakeRoleStore(user_roles={}))
    authorizer = ToolAuthorizer(
        rbac_service=rbac_service,
        settings=Settings(openai_api_key="test-key"),  # security flags default off
    )
    result = await authorizer.authorize(_tool(risk_level="high"), _context(user_id))
    assert result is None


@pytest.mark.anyio
async def test_member_only_denied_destructive_tool_when_rbac_enforced() -> None:
    user_id = uuid.uuid4()
    rbac_service = RbacService(_FakeRoleStore(user_roles={}))
    authorizer = ToolAuthorizer(
        rbac_service=rbac_service,
        settings=Settings(
            openai_api_key="test-key",
            security_governance_enabled=True,
            security_rbac_enforcement_enabled=True,
        ),
    )
    result = await authorizer.authorize(_tool(risk_level="high"), _context(user_id))
    assert result is not None


@pytest.mark.anyio
async def test_member_only_allowed_non_destructive_tool_when_rbac_enforced() -> None:
    user_id = uuid.uuid4()
    rbac_service = RbacService(_FakeRoleStore(user_roles={}))
    authorizer = ToolAuthorizer(
        rbac_service=rbac_service,
        settings=Settings(
            openai_api_key="test-key",
            security_governance_enabled=True,
            security_rbac_enforcement_enabled=True,
        ),
    )
    result = await authorizer.authorize(_tool(), _context(user_id))
    assert result is None


@pytest.mark.anyio
async def test_operator_allowed_destructive_tool_when_rbac_enforced() -> None:
    user_id = uuid.uuid4()
    rbac_service = RbacService(_FakeRoleStore(user_roles={user_id: {"operator"}}))
    authorizer = ToolAuthorizer(
        rbac_service=rbac_service,
        settings=Settings(
            openai_api_key="test-key",
            security_governance_enabled=True,
            security_rbac_enforcement_enabled=True,
        ),
    )
    result = await authorizer.authorize(
        _tool(category="destructive"), _context(user_id)
    )
    assert result is None
