from __future__ import annotations

from app.ai.security.errors import SecurityErrorCode


class PermissionDeniedError(RuntimeError):
    def __init__(
        self,
        permission_key: str,
        *,
        code: SecurityErrorCode = SecurityErrorCode.PERMISSION_DENIED,
    ) -> None:
        super().__init__(f"Permission denied: {permission_key}")
        self.permission_key = permission_key
        self.code = code


class RoleNotFoundError(RuntimeError):
    def __init__(self, role_name: str) -> None:
        super().__init__(f"Role not found: {role_name}")
        self.role_name = role_name
        self.code = SecurityErrorCode.ROLE_NOT_FOUND
