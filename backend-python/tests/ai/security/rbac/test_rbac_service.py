from __future__ import annotations

import asyncio
import uuid

import pytest

from app.ai.security.errors import SecurityErrorCode
from app.ai.security.exceptions import PermissionDeniedError, RoleNotFoundError
from app.ai.security.rbac.models import Role, UserRoleAssignment
from app.ai.security.rbac.permissions import DEFAULT_ROLE_PERMISSIONS, PermissionKey
from app.ai.security.rbac.service import RbacService, resolve_caller_role
from app.core.caller import CallerContext


class FakeRoleStore:
    def __init__(self) -> None:
        self.roles = {
            "member": Role(
                id=uuid.uuid4(), name="member", description="member", is_system=True
            ),
            "admin": Role(
                id=uuid.uuid4(), name="admin", description="admin", is_system=True
            ),
            "owner": Role(
                id=uuid.uuid4(), name="owner", description="owner", is_system=True
            ),
            "operator": Role(
                id=uuid.uuid4(), name="operator", description="operator", is_system=True
            ),
        }
        self._user_assignments: dict[uuid.UUID, set[str]] = {}

    async def list_roles(self) -> list[Role]:
        return list(self.roles.values())

    async def get_role_by_name(self, name: str) -> Role | None:
        return self.roles.get(name.lower())

    async def get_permission_keys_for_user(self, user_id: uuid.UUID) -> set[str]:
        return set(self._user_assignments.get(user_id, set()))

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        return sorted(self._user_assignments.get(user_id, set()))

    async def assign_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        target = role_name.lower()
        if target not in self.roles:
            return False
        roles = self._user_assignments.setdefault(user_id, set())
        already = target in roles
        roles.add(target)
        return not already

    async def revoke_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        roles = self._user_assignments.get(user_id, set())
        if role_name.lower() not in roles:
            return False
        roles.remove(role_name.lower())
        return True

    async def bootstrap_admins(self, emails: list[str]) -> int:
        granted = 0
        for email in emails:
            if email.lower() == "admin@example.com":
                if "owner" not in self._user_assignments.setdefault(
                    uuid.UUID("00000000-0000-0000-0000-000000000001"), set()
                ):
                    self._user_assignments[
                        uuid.UUID("00000000-0000-0000-0000-000000000001")
                    ].add("owner")
                    granted += 1
        return granted

    async def get_user_role_assignments(
        self, user_id: uuid.UUID
    ) -> list[UserRoleAssignment]:
        return [
            UserRoleAssignment(user_id=user_id, role_name=role_name)
            for role_name in sorted(self._user_assignments.get(user_id, set()))
        ]


@pytest.mark.anyio
async def test_resolve_caller_role_preserves_flag_off_identity() -> None:
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    assert await resolve_caller_role(caller, None, enforcement_enabled=False) == "guest"


@pytest.mark.anyio
async def test_resolve_caller_role_uses_rbac_baseline_and_priority() -> None:
    member_id = uuid.uuid4()
    operator_id = uuid.uuid4()
    store = FakeRoleStore()
    store._user_assignments[operator_id] = {"operator", "admin"}
    service = RbacService(store)

    assert (
        await resolve_caller_role(
            CallerContext.anonymous(guest_id=uuid.uuid4()),
            service,
            enforcement_enabled=True,
        )
        is None
    )
    assert (
        await resolve_caller_role(
            CallerContext.for_user(member_id),
            service,
            enforcement_enabled=True,
        )
        == "member"
    )
    assert (
        await resolve_caller_role(
            CallerContext.for_user(operator_id),
            service,
            enforcement_enabled=True,
        )
        == "admin"
    )


@pytest.mark.anyio
async def test_permission_registry_has_expected_keys() -> None:
    assert PermissionKey.TOOLS_EXECUTE in PermissionKey
    assert PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE in PermissionKey
    assert PermissionKey.JOBS_VIEW_ALL in PermissionKey
    assert "tools:execute" == PermissionKey.TOOLS_EXECUTE
    assert PermissionKey.TOOLS_EXECUTE in DEFAULT_ROLE_PERMISSIONS["member"]
    assert (
        PermissionKey.TOOLS_EXECUTE_DESTRUCTIVE in DEFAULT_ROLE_PERMISSIONS["operator"]
    )


@pytest.mark.anyio
async def test_service_implicit_member_and_owner_wildcard() -> None:
    store = FakeRoleStore()
    service = RbacService(store)
    user_id = uuid.uuid4()

    decision = await service.authorize(user_id, PermissionKey.TOOLS_EXECUTE)
    assert decision.allowed is True
    assert decision.matched_role == "member"
    assert PermissionKey.TOOLS_EXECUTE in await service.get_permissions(user_id)

    await service.assign_role(user_id, "admin")
    assert await service.has_permission(user_id, PermissionKey.RBAC_MANAGE)
    assert await service.has_permission(user_id, PermissionKey.JOBS_VIEW_ALL) is True

    owner_id = uuid.uuid4()
    await service.assign_role(owner_id, "owner")
    wildcard = await service.authorize(owner_id, "rbac:manage")
    assert wildcard.allowed is True
    assert wildcard.matched_role == "owner"


@pytest.mark.anyio
async def test_assign_role_is_idempotent_and_member_revocation_raises() -> None:
    store = FakeRoleStore()
    service = RbacService(store)
    user_id = uuid.uuid4()

    first = await service.assign_role(user_id, "admin")
    second = await service.assign_role(user_id, "admin")
    assert first is True
    assert second is False

    with pytest.raises(PermissionDeniedError):
        await service.revoke_role(user_id, "member")


@pytest.mark.anyio
async def test_guest_permission_and_bootstrap_admins_are_case_insensitive_and_idempotent() -> (
    None
):
    store = FakeRoleStore()
    service = RbacService(store)
    guest_permissions = await service.get_permissions(None)
    assert guest_permissions == set()

    granted = await service.bootstrap_admins(["Admin@Example.com", "admin@example.com"])
    assert granted == 1
    assert await service.has_permission(
        uuid.UUID("00000000-0000-0000-0000-000000000001"), PermissionKey.RBAC_MANAGE
    )

    with pytest.raises(RoleNotFoundError):
        await service.assign_role(uuid.uuid4(), "not_a_real_role")


@pytest.mark.anyio
async def test_permission_cache_invalidates_on_assign_and_revoke() -> None:
    store = FakeRoleStore()
    service = RbacService(store, cache_ttl_seconds=60)
    user_id = uuid.uuid4()

    initial = await service.get_permissions(user_id)
    assert PermissionKey.TOOLS_EXECUTE in initial

    await service.assign_role(user_id, "admin")
    updated = await service.get_permissions(user_id)
    assert PermissionKey.RBAC_MANAGE in updated

    await service.revoke_role(user_id, "admin")
    after_revoke = await service.get_permissions(user_id)
    assert PermissionKey.RBAC_MANAGE not in after_revoke


@pytest.mark.anyio
async def test_permission_resolution_race_does_not_cache_stale_after_revoke() -> None:
    store = FakeRoleStore()
    service = RbacService(store, cache_ttl_seconds=60)
    user_id = uuid.uuid4()
    await service.assign_role(user_id, "admin")
    service._permission_cache.pop(user_id, None)

    real_get_user_roles = store.get_user_roles
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def paused_get_user_roles(uid: uuid.UUID) -> list[str]:
        entered.set()
        # Snapshot roles now (query already in flight before the concurrent revoke lands).
        snapshot = await real_get_user_roles(uid)
        await resume.wait()
        return snapshot

    store.get_user_roles = paused_get_user_roles  # type: ignore[method-assign]

    resolve_task = asyncio.create_task(service.get_permissions(user_id))
    await entered.wait()
    assert await service.revoke_role(user_id, "admin") is True
    resume.set()
    stale_result = await resolve_task
    assert PermissionKey.RBAC_MANAGE in stale_result

    after_revoke = await service.get_permissions(user_id)
    assert PermissionKey.RBAC_MANAGE not in after_revoke


@pytest.mark.anyio
async def test_authorize_denied_reports_reason_and_security_code() -> None:
    store = FakeRoleStore()
    service = RbacService(store)
    user_id = uuid.uuid4()

    decision = await service.authorize(user_id, PermissionKey.RBAC_MANAGE)
    assert decision.allowed is False
    assert decision.denial_reason is not None
    assert decision.denial_reason == SecurityErrorCode.PERMISSION_DENIED
