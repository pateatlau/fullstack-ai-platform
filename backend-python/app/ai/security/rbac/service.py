from __future__ import annotations

import time
import uuid

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
from app.ai.security.rbac.store import RoleStore


class RbacService:
    def __init__(
        self,
        store: RoleStore | None = None,
        *,
        cache_ttl_seconds: int = 60,
    ) -> None:
        self.store = store
        self.cache_ttl_seconds = max(cache_ttl_seconds, 0)
        self._permission_cache: dict[uuid.UUID, tuple[float, set[str]]] = {}
        self._cache_generation: dict[uuid.UUID, int] = {}

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
        permission_key = str(permission)
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

    async def assign_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        role = await self.store.get_role_by_name(role_name)
        if role is None:
            raise RoleNotFoundError(role_name)
        assigned = await self.store.assign_role(user_id, role_name)
        if assigned:
            self._cache_generation[user_id] = self._cache_generation.get(user_id, 0) + 1
            self._permission_cache.pop(user_id, None)
        return assigned

    async def revoke_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        if role_name.lower() == "member":
            raise PermissionDeniedError("member")
        revoked = await self.store.revoke_role(user_id, role_name)
        if revoked:
            self._cache_generation[user_id] = self._cache_generation.get(user_id, 0) + 1
            self._permission_cache.pop(user_id, None)
        return revoked

    async def list_roles(self) -> list[Role]:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        return await self.store.list_roles()

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        if self.store is None:
            raise RuntimeError("RbacService requires a RoleStore")
        return await self.store.get_user_roles(user_id)

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
