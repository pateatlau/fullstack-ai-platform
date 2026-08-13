from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.security.rbac.models import Role, UserRoleAssignment


class RoleStore(Protocol):
    async def list_roles(self) -> list[Role]: ...

    async def get_role_by_name(self, name: str) -> Role | None: ...

    async def get_permission_keys_for_user(self, user_id: uuid.UUID) -> set[str]: ...

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]: ...

    async def assign_role(self, user_id: uuid.UUID, role_name: str) -> bool: ...

    async def revoke_role(self, user_id: uuid.UUID, role_name: str) -> bool: ...

    async def bootstrap_admins(self, emails: list[str]) -> int: ...

    async def get_user_role_assignments(
        self, user_id: uuid.UUID
    ) -> list[UserRoleAssignment]: ...


class PostgresRoleStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _coerce_uuid(value: object) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

    @staticmethod
    def _coerce_datetime(value: object) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        return None

    @staticmethod
    def _role_row_to_model(row: dict[str, object]) -> Role:
        return Role(
            id=PostgresRoleStore._coerce_uuid(row["id"]),
            name=str(row["name"]),
            description=str(row.get("description") or ""),
            is_system=bool(row.get("is_system", True)),
            created_at=PostgresRoleStore._coerce_datetime(row.get("created_at")),
            updated_at=PostgresRoleStore._coerce_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _assignment_row_to_model(row: dict[str, object]) -> UserRoleAssignment:
        return UserRoleAssignment(
            user_id=PostgresRoleStore._coerce_uuid(row["user_id"]),
            role_name=str(row["role_name"]),
            created_at=PostgresRoleStore._coerce_datetime(row.get("created_at")),
        )

    async def list_roles(self) -> list[Role]:
        table = sa.table(
            "roles",
            sa.column("id", sa.types.Uuid()),
            sa.column("name", sa.Text()),
            sa.column("description", sa.Text()),
            sa.column("is_system", sa.Boolean()),
            sa.column("created_at", sa.DateTime()),
            sa.column("updated_at", sa.DateTime()),
        )
        rows = (await self.session.execute(select(table))).mappings().all()
        return [self._role_row_to_model(dict(row)) for row in rows]

    async def get_role_by_name(self, name: str) -> Role | None:
        table = sa.table(
            "roles",
            sa.column("id", sa.types.Uuid()),
            sa.column("name", sa.Text()),
            sa.column("description", sa.Text()),
            sa.column("is_system", sa.Boolean()),
            sa.column("created_at", sa.DateTime()),
            sa.column("updated_at", sa.DateTime()),
        )
        row = (
            (
                await self.session.execute(
                    select(table).where(table.c.name == name.lower())
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self._role_row_to_model(dict(row))

    async def get_permission_keys_for_user(self, user_id: uuid.UUID) -> set[str]:
        assignments = sa.table(
            "user_role_assignments",
            sa.column("user_id", sa.types.Uuid()),
            sa.column("role_id", sa.types.Uuid()),
        )
        role_permissions = sa.table(
            "role_permissions",
            sa.column("role_id", sa.types.Uuid()),
            sa.column("permission_id", sa.types.Uuid()),
        )
        permissions = sa.table(
            "permissions",
            sa.column("id", sa.types.Uuid()),
            sa.column("key", sa.Text()),
        )
        rows = (
            (
                await self.session.execute(
                    select(permissions.c.key)
                    .select_from(
                        assignments.join(
                            role_permissions,
                            assignments.c.role_id == role_permissions.c.role_id,
                        ).join(
                            permissions,
                            role_permissions.c.permission_id == permissions.c.id,
                        )
                    )
                    .where(assignments.c.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        return {str(key) for key in rows}

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        table = sa.table(
            "user_role_assignments",
            sa.column("user_id", sa.types.Uuid()),
            sa.column("role_id", sa.types.Uuid()),
        )
        role_table = sa.table(
            "roles",
            sa.column("id", sa.types.Uuid()),
            sa.column("name", sa.Text()),
        )
        rows = (
            (
                await self.session.execute(
                    select(role_table.c.name)
                    .select_from(
                        table.join(role_table, table.c.role_id == role_table.c.id)
                    )
                    .where(table.c.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        return sorted({str(name) for name in rows})

    async def assign_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        normalized = role_name.lower()
        role = await self.get_role_by_name(normalized)
        if role is None:
            return False
        result = await self.session.execute(
            sa.text(
                "INSERT INTO user_role_assignments (user_id, role_id, created_at) "
                "VALUES (:user_id, :role_id, NOW()) "
                "ON CONFLICT (user_id, role_id) DO NOTHING"
            ),
            {"user_id": user_id, "role_id": role.id},
        )
        rowcount = getattr(result, "rowcount", None)
        return isinstance(rowcount, int) and rowcount > 0

    async def revoke_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        role = await self.get_role_by_name(role_name.lower())
        if role is None:
            return False
        result = await self.session.execute(
            sa.text(
                "DELETE FROM user_role_assignments WHERE user_id = :user_id AND role_id = :role_id"
            ),
            {"user_id": user_id, "role_id": role.id},
        )
        rowcount = getattr(result, "rowcount", None)
        return isinstance(rowcount, int) and rowcount > 0

    async def bootstrap_admins(self, emails: list[str]) -> int:
        if not emails:
            return 0
        granted = 0
        owner_role = await self.get_role_by_name("owner")
        if owner_role is None:
            return 0

        for email in emails:
            normalized = email.strip().lower()
            user_row = (
                (
                    await self.session.execute(
                        sa.text("SELECT id FROM users WHERE lower(email) = :email"),
                        {"email": normalized},
                    )
                )
                .mappings()
                .first()
            )
            if user_row is None:
                continue
            result = await self.session.execute(
                sa.text(
                    "INSERT INTO user_role_assignments (user_id, role_id, created_at) "
                    "VALUES (:user_id, :role_id, NOW()) "
                    "ON CONFLICT (user_id, role_id) DO NOTHING"
                ),
                {"user_id": user_row["id"], "role_id": owner_role.id},
            )
            rowcount = getattr(result, "rowcount", None)
            if isinstance(rowcount, int) and rowcount > 0:
                granted += 1
        return granted

    async def get_user_role_assignments(
        self, user_id: uuid.UUID
    ) -> list[UserRoleAssignment]:
        table = sa.table(
            "user_role_assignments",
            sa.column("user_id", sa.types.Uuid()),
            sa.column("role_id", sa.types.Uuid()),
            sa.column("created_at", sa.DateTime()),
        )
        role_table = sa.table(
            "roles",
            sa.column("id", sa.types.Uuid()),
            sa.column("name", sa.Text()),
        )
        rows = (
            (
                await self.session.execute(
                    select(
                        table.c.user_id,
                        role_table.c.name.label("role_name"),
                        table.c.created_at,
                    )
                    .select_from(
                        table.join(role_table, table.c.role_id == role_table.c.id)
                    )
                    .where(table.c.user_id == user_id)
                )
            )
            .mappings()
            .all()
        )
        return [self._assignment_row_to_model(dict(row)) for row in rows]
