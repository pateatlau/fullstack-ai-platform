"""0016 security rbac

Revision ID: 0016_security_rbac
Revises: 0015_document_upload_staging
Create Date: 2026-08-13

Epic 11 Phase 1: RBAC domain model, system role seeds, permissions, and role assignments.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_security_rbac"
down_revision: Union[str, None] = "0015_document_upload_staging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "roles",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    op.create_table(
        "permissions",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("reserved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_permissions"),
        sa.UniqueConstraint("key", name="uq_permissions_key"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", _UUID, nullable=False),
        sa.Column("permission_id", _UUID, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_role_permissions_role_id_roles", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], name="fk_role_permissions_permission_id_permissions", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name="pk_role_permissions"),
    )

    op.create_table(
        "user_role_assignments",
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("role_id", _UUID, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_role_assignments_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_user_role_assignments_role_id_roles", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_role_assignments"),
    )

    op.create_index(
        "ix_user_role_assignments_user_id",
        "user_role_assignments",
        ["user_id"],
    )

    roles = sa.table(
        "roles",
        sa.column("id", _UUID),
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("is_system", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    permissions = sa.table(
        "permissions",
        sa.column("id", _UUID),
        sa.column("key", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("category", sa.Text()),
        sa.column("risk_level", sa.Text()),
        sa.column("reserved", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", _UUID),
        sa.column("permission_id", _UUID),
    )

    role_rows = [
        {"id": sa.text("gen_random_uuid()"), "name": "member", "description": "Default authenticated user role", "is_system": True},
        {"id": sa.text("gen_random_uuid()"), "name": "operator", "description": "Operational power user", "is_system": True},
        {"id": sa.text("gen_random_uuid()"), "name": "admin", "description": "Administrative operator", "is_system": True},
        {"id": sa.text("gen_random_uuid()"), "name": "owner", "description": "Platform owner", "is_system": True},
    ]
    op.execute(
        roles.insert().values(role_rows)
    )

    permission_rows = [
        {"key": "*", "display_name": "All Permissions", "description": "Wildcard permission for owners", "category": "rbac", "risk_level": "high", "reserved": False},
        {"key": "rbac:manage", "display_name": "Manage Roles", "description": "Manage RBAC assignments", "category": "rbac", "risk_level": "high", "reserved": False},
        {"key": "audit:view", "display_name": "View Audit Log", "description": "View audit events", "category": "audit", "risk_level": "medium", "reserved": False},
        {"key": "policy:view", "display_name": "View Policy Summary", "description": "Read policy summary", "category": "policy", "risk_level": "low", "reserved": False},
        {"key": "jobs:view_all", "display_name": "View Background Jobs", "description": "List and inspect all jobs", "category": "jobs", "risk_level": "medium", "reserved": False},
        {"key": "jobs:retry", "display_name": "Retry Dead-Letter Jobs", "description": "Retry failed background jobs", "category": "jobs", "risk_level": "high", "reserved": False},
        {"key": "approvals:decide_all", "display_name": "Decide Any Approval", "description": "Act on all approval stages", "category": "approvals", "risk_level": "high", "reserved": False},
        {"key": "tools:execute", "display_name": "Execute Tools", "description": "Execute non-destructive tools", "category": "tools", "risk_level": "low", "reserved": False},
        {"key": "tools:execute:destructive", "display_name": "Execute Destructive Tools", "description": "Execute high-risk destructive tool calls", "category": "tools", "risk_level": "high", "reserved": False},
        {"key": "plugins:manage", "display_name": "Manage Plugins", "description": "Manage plugins", "category": "plugins", "risk_level": "high", "reserved": True},
        {"key": "workflow:view_all", "display_name": "View All Workflows", "description": "Inspect all workflows", "category": "workflow", "risk_level": "medium", "reserved": True},
        {"key": "mcp:manage", "display_name": "Manage MCP Servers", "description": "Configure MCP servers", "category": "mcp", "risk_level": "high", "reserved": True},
    ]
    op.execute(permissions.insert().values(permission_rows))

    # Seed role-permission links for the four system roles.
    role_name_to_id = {
        name: row[1]
        for name, row in (
            op.get_bind()
            .execute(sa.text("SELECT name, id FROM roles"))
            .all()
        )
    }
    permission_key_to_id = {
        key: row[1]
        for key, row in (
            op.get_bind()
            .execute(sa.text("SELECT key, id FROM permissions"))
            .all()
        )
    }

    mapping = {
        "member": ["tools:execute"],
        "operator": ["tools:execute", "tools:execute:destructive", "jobs:view_all", "jobs:retry", "policy:view", "audit:view"],
        "admin": ["tools:execute", "tools:execute:destructive", "jobs:view_all", "jobs:retry", "policy:view", "audit:view", "rbac:manage", "approvals:decide_all"],
        "owner": ["*", "rbac:manage", "audit:view", "policy:view", "jobs:view_all", "jobs:retry", "approvals:decide_all", "tools:execute", "tools:execute:destructive", "plugins:manage", "workflow:view_all", "mcp:manage"],
    }

    values = []
    for role_name, permission_keys in mapping.items():
        role_id = role_name_to_id[role_name]
        for permission_key in permission_keys:
            values.append({"role_id": role_id, "permission_id": permission_key_to_id[permission_key]})

    if values:
        op.execute(role_permissions.insert().values(values))


def downgrade() -> None:
    op.drop_table("user_role_assignments")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
