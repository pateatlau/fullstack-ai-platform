"""Epic 11 Phase 2: RBAC-enforced HITL stage decisions."""

from __future__ import annotations

import uuid

import pytest

from app.ai.hitl.exceptions import StagePermissionInvalidError
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.security.rbac.models import Role, UserRoleAssignment
from app.ai.security.rbac.permissions import PermissionKey
from app.ai.security.rbac.service import RbacService
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.core.config import Settings
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


class _Handler:
    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True, data={})


class _FakeRoleStore:
    """Minimal in-memory ``RoleStore`` — assigns fixed system roles per user."""

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


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_file",
            description="delete",
            parameters={"type": "object", "properties": {}},
            requires_approval=True,
        ),
        _Handler(),
    )
    return registry


def _service(
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    *,
    user_roles: dict[uuid.UUID, set[str]],
    rbac_enforcement_enabled: bool = True,
) -> AgentApprovalService:
    registry = _registry()
    rbac_service = RbacService(_FakeRoleStore(user_roles=user_roles))
    return AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
        rbac_service=rbac_service,
        rbac_enforcement_enabled=rbac_enforcement_enabled,
    )


# ``jobs:retry`` is a real seeded permission (operator/admin/owner) reused here
# as a stand-in "stage permission" the deciding user must hold.
_STAGE = PermissionKey.JOBS_RETRY.value


async def _seed_pending(
    *,
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    owner_id: uuid.UUID,
) -> uuid.UUID:
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-stage-rbac",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-stage-rbac", "status": "waiting_approval"},
        required_stages=[_STAGE],
    )
    return approval.id


@pytest.mark.anyio
async def test_owner_without_stage_permission_is_denied() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    approval_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    # Owner holds no roles at all -> no RBAC permissions -> stage denied.
    service = _service(store, chat_store, user_roles={})

    with pytest.raises(StagePermissionInvalidError):
        await service.decide(approval_id, decider_id=owner_id, decision="approved")


@pytest.mark.anyio
async def test_non_owner_holding_stage_permission_can_decide() -> None:
    owner_id = uuid.uuid4()
    reviewer_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    approval_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    service = _service(
        store,
        chat_store,
        user_roles={reviewer_id: {"operator"}},
    )

    result = await service.decide(
        approval_id, decider_id=reviewer_id, decision="approved"
    )

    assert result.status is ApprovalStatus.APPROVED
    stored = await store.get(approval_id)
    assert stored is not None
    assert stored.stage_decisions[-1].decided_by == reviewer_id
    assert stored.decided_by == reviewer_id
    assert stored.owner_id == owner_id


@pytest.mark.anyio
async def test_non_owner_without_permission_cannot_see_approval() -> None:
    owner_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    approval_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    service = _service(store, chat_store, user_roles={stranger_id: {"member"}})

    from app.ai.hitl.exceptions import ApprovalNotFoundError

    with pytest.raises(ApprovalNotFoundError):
        await service.decide(approval_id, decider_id=stranger_id, decision="approved")


@pytest.mark.anyio
async def test_flag_off_preserves_owner_only_checklist_behaviour() -> None:
    """RBAC disabled: any owner may satisfy any stage (pre-Epic-11 behaviour)."""
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    approval_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    service = _service(
        store,
        chat_store,
        user_roles={},
        rbac_enforcement_enabled=False,
    )

    result = await service.decide(approval_id, decider_id=owner_id, decision="approved")
    assert result.status is ApprovalStatus.APPROVED
