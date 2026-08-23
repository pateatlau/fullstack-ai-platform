from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from app.ai.security.audit.actions import AuditAction
from app.ai.security.audit.models import AuditOutcome
from app.ai.security.errors import SecurityErrorCode
from app.ai.security.exceptions import PermissionDeniedError, RoleNotFoundError
from app.ai.security.rbac.models import (
    AuthorizationDecision,
    PermissionDefinition,
    Role,
)
from app.ai.security.rbac.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_REGISTRY,
    PermissionKey,
)
from app.ai.security.rbac.store import BulkRoleStore, FinalOwnerRoleStore, RoleStore

if TYPE_CHECKING:
    from app.ai.security.audit.logger import AuditLogger
    from app.core.caller import CallerContext


class RbacService:
    _ROLE_PRIORITY = {"member": 0, "operator": 1, "admin": 2, "owner": 3}

    def __init__(
        self,
        store: RoleStore | None = None,
        *,
        cache_ttl_seconds: int = 60,
        audit_logger: "AuditLogger | None" = None,
    ) -> None:
        self.store = store
        self.cache_ttl_seconds = max(cache_ttl_seconds, 0)
        self._audit_logger = audit_logger
        self._permission_cache: dict[uuid.UUID, tuple[float, set[str]]] = {}
        self._cache_generation: dict[uuid.UUID, int] = {}

    async def _list_role_names_for_user(self, user_id: uuid.UUID) -> set[str]:
        if self.store is None:
            return set()
        return {
            role_name.lower() for role_name in await self.store.get_user_roles(user_id)
        }

    async def _is_final_owner(self, user_id: uuid.UUID) -> bool:
        if self.store is None:
            return False
        if not isinstance(self.store, FinalOwnerRoleStore):
            return False
        assignments = await self.store.list_all_user_role_assignments()
        owners = {
            assignment.user_id
            for assignment in assignments
            if assignment.role_name.lower() == "owner"
        }
        return user_id in owners and len(owners) == 1

    async def _ensure_role_management_is_allowed(
        self,
        *,
        actor: "CallerContext | None",
        target_user_id: uuid.UUID,
        role_name: str,
        operation: str,
    ) -> None:
        if actor is None or actor.user_id is None:
            return

        normalized = role_name.lower()
        actor_roles = await self._list_role_names_for_user(actor.user_id)
        target_roles = await self._list_role_names_for_user(target_user_id)
        actor_level = max(
            (self._ROLE_PRIORITY.get(role_name, 0) for role_name in actor_roles),
            default=0,
        )

        if actor.user_id == target_user_id:
            if (
                operation == "assign"
                and self._ROLE_PRIORITY.get(normalized, 0) > actor_level
            ):
                raise PermissionDeniedError(f"Cannot elevate self to '{role_name}'.")
            if operation == "revoke" and actor_level > 0:
                raise PermissionDeniedError(
                    f"Cannot revoke your own '{role_name}' role."
                )

        if "owner" in actor_roles:
            if (
                operation == "revoke"
                and normalized == "owner"
                and await self._is_final_owner(target_user_id)
            ):
                raise PermissionDeniedError("owner")
            return

        if "admin" in actor_roles:
            if normalized == "owner" or "owner" in target_roles:
                raise PermissionDeniedError("owner")
            return

        raise PermissionDeniedError("rbac:manage")

    async def get_permissions(self, user_id: uuid.UUID | None) -> set[str]:
        if user_id is None:
            return set()
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")

        cache_key = user_id
        if self.cache_ttl_seconds > 0 and cache_key in self._permission_cache:
            cached_at, cached_permissions = self._permission_cache[cache_key]
            if time.monotonic() - cached_at < self.cache_ttl_seconds:
                return set(cached_permissions)

        generation = self._cache_generation.get(cache_key, 0)
        roles = set(await self.store.get_user_roles(user_id))
        roles.add("member")

        permissions: set[str] = set()
        for role_name in sorted(roles):
            permissions |= {
                str(permission_key)
                for permission_key in DEFAULT_ROLE_PERMISSIONS.get(role_name, set())
            }
        if "owner" in roles:
            permissions.add("*")

        # Skip caching if a role mutation invalidated this user while get_user_roles was in flight.
        if (
            self.cache_ttl_seconds > 0
            and self._cache_generation.get(cache_key, 0) == generation
        ):
            self._permission_cache[cache_key] = (time.monotonic(), set(permissions))
        return set(permissions)

    async def has_permission(
        self,
        user_id: uuid.UUID | None,
        permission: str | PermissionKey,
    ) -> bool:
        return (await self.authorize(user_id, permission)).allowed

    async def authorize(
        self,
        user_id: uuid.UUID | None,
        permission: str | PermissionKey,
    ) -> AuthorizationDecision:
        permission_key = (
            permission.value if isinstance(permission, PermissionKey) else permission
        )
        if user_id is None:
            return AuthorizationDecision(
                allowed=False,
                permission_key=permission_key,
                denial_reason=SecurityErrorCode.PERMISSION_DENIED,
            )
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")

        permissions = await self.get_permissions(user_id)
        roles = set(await self.store.get_user_roles(user_id))
        if "*" in permissions:
            return AuthorizationDecision(
                allowed=True,
                permission_key=permission_key,
                matched_role="owner" if "owner" in roles else "member",
                matched_permission="*",
            )

        if permission_key in permissions:
            matched_role = "member"
            for role_name in sorted(roles):
                if permission_key in {
                    str(permission_key_name)
                    for permission_key_name in DEFAULT_ROLE_PERMISSIONS.get(
                        role_name,
                        set(),
                    )
                }:
                    matched_role = role_name
                    break
            return AuthorizationDecision(
                allowed=True,
                permission_key=permission_key,
                matched_role=matched_role,
                matched_permission=permission_key,
            )

        return AuthorizationDecision(
            allowed=False,
            permission_key=permission_key,
            denial_reason=SecurityErrorCode.PERMISSION_DENIED,
        )

    async def assign_role(
        self,
        user_id: uuid.UUID,
        role_name: str,
        *,
        actor: "CallerContext | None" = None,
    ) -> bool:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        normalized = role_name.strip().lower()
        await self._ensure_role_management_is_allowed(
            actor=actor,
            target_user_id=user_id,
            role_name=normalized,
            operation="assign",
        )
        role = await self.store.get_role_by_name(normalized)
        if role is None:
            raise RoleNotFoundError(role_name)
        assigned = await self.store.assign_role(user_id, normalized)
        if assigned:
            self._cache_generation[user_id] = self._cache_generation.get(user_id, 0) + 1
            self._permission_cache.pop(user_id, None)
        if assigned and self._audit_logger is not None:
            await self._audit_logger.record(
                actor=actor,
                action=AuditAction.ROLE_ASSIGNED.value,
                outcome=AuditOutcome.SUCCESS,
                resource_type="role",
                resource_id=str(user_id),
                metadata={"role": role.name},
            )
        return assigned

    async def revoke_role(
        self,
        user_id: uuid.UUID,
        role_name: str,
        *,
        actor: "CallerContext | None" = None,
    ) -> bool:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        normalized = role_name.strip().lower()
        if normalized == "member":
            raise PermissionDeniedError("member")
        await self._ensure_role_management_is_allowed(
            actor=actor,
            target_user_id=user_id,
            role_name=normalized,
            operation="revoke",
        )
        role = await self.store.get_role_by_name(normalized)
        if role is None:
            raise RoleNotFoundError(role_name)
        if normalized == "owner" and await self._is_final_owner(user_id):
            raise PermissionDeniedError("owner")
        revoked = await self.store.revoke_role(user_id, normalized)
        if revoked:
            self._cache_generation[user_id] = self._cache_generation.get(user_id, 0) + 1
            self._permission_cache.pop(user_id, None)
        if revoked and self._audit_logger is not None:
            await self._audit_logger.record(
                actor=actor,
                action=AuditAction.ROLE_REVOKED.value,
                outcome=AuditOutcome.SUCCESS,
                resource_type="role",
                resource_id=str(user_id),
                metadata={"role": role.name},
            )
        return revoked

    async def list_roles(self) -> list[Role]:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        return await self.store.list_roles()

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        return await self.store.get_user_roles(user_id)

    async def get_user_roles_bulk(
        self, user_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        if isinstance(self.store, BulkRoleStore):
            return await self.store.get_user_roles_bulk(user_ids)
        return {
            user_id: await self.store.get_user_roles(user_id) for user_id in user_ids
        }

    async def get_highest_priority_role(self, user_id: uuid.UUID) -> str:
        roles = set(await self.get_user_roles(user_id))
        roles.add("member")
        for role in ("owner", "admin", "operator", "member"):
            if role in roles:
                return role
        return "member"

    async def get_permission_registry(self) -> dict[str, PermissionDefinition]:
        return {str(key): definition for key, definition in PERMISSION_REGISTRY.items()}

    async def bootstrap_admins(self, emails: list[str]) -> int:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        normalized = [
            email.strip().lower() for email in emails if email and email.strip()
        ]
        if not normalized:
            return 0
        return await self.store.bootstrap_admins(normalized)


async def resolve_caller_role(
    caller: "CallerContext",
    rbac: RbacService | None,
    *,
    enforcement_enabled: bool,
) -> str | None:
    """Resolve the policy role while preserving the legacy flag-off value.

    RBAC-enabled policy evaluation uses the implicit ``member`` baseline for
    authenticated callers and no role for guests. Disabled enforcement keeps
    the existing ``CallerContext.kind`` value unchanged.
    """
    if not enforcement_enabled:
        return caller.kind
    if not caller.is_authenticated or caller.user_id is None:
        return None
    if rbac is None:
        return "member"
    return await rbac.get_highest_priority_role(caller.user_id)
