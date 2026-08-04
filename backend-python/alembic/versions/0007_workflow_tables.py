"""0007 workflow tables

Revision ID: 0007_workflow_tables
Revises: 0006_memory_tables
Create Date: 2026-08-04

Epic 06 Phase 1: creates ``workflow_definitions``, ``workflow_runs``, and
``workflow_node_executions``. Independent of ``memory_records``,
``document_chunks``, and all other epic tables (Part I § Persistence Schema).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_workflow_tables"
down_revision: Union[str, None] = "0006_memory_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "workflow_definitions",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("owner_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("entry_node_id", sa.Text(), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_definitions"),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_workflow_definitions_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="workflow_definition_status_valid",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "name",
            "version",
            name="uq_workflow_definitions_owner_name_version",
        ),
    )
    op.create_index(
        "ix_workflow_definitions_owner_status",
        "workflow_definitions",
        ["owner_id", "status"],
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("workflow_definition_id", _UUID, nullable=False),
        sa.Column("owner_id", _UUID, nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("session_id", _UUID, nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "context",
            postgresql.JSONB(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "current_node_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "checkpoint_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        sa.ForeignKeyConstraint(
            ["workflow_definition_id"],
            ["workflow_definitions.id"],
            name="fk_workflow_runs_workflow_definition_id_workflow_definitions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_workflow_runs_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_workflow_runs_session_id_chat_sessions",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'completed', "
            "'failed', 'cancelled')",
            name="workflow_run_status_valid",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "workflow_definition_id",
            "idempotency_key",
            name="uq_workflow_runs_owner_definition_idempotency",
        ),
    )
    op.create_index(
        "ix_workflow_runs_owner_status",
        "workflow_runs",
        ["owner_id", "status"],
    )
    op.create_index(
        "ix_workflow_runs_workflow_definition_id",
        "workflow_runs",
        ["workflow_definition_id"],
    )

    op.create_table(
        "workflow_node_executions",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("run_id", _UUID, nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "input",
            postgresql.JSONB(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("decided_by", _UUID, nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_node_executions"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
            name="fk_workflow_node_executions_run_id_workflow_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name="fk_workflow_node_executions_decided_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "node_type IN ('task', 'llm', 'agent', 'router', 'fork', 'join', "
            "'approval', 'terminal')",
            name="workflow_node_execution_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'succeeded', "
            "'failed', 'skipped', 'cancelled')",
            name="workflow_node_execution_status_valid",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('approved', 'rejected')",
            name="workflow_node_execution_decision_valid",
        ),
        sa.UniqueConstraint(
            "run_id",
            "node_id",
            "attempt",
            name="uq_workflow_node_executions_run_node_attempt",
        ),
    )
    op.create_index(
        "ix_workflow_node_executions_run_status",
        "workflow_node_executions",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_node_executions_run_status",
        table_name="workflow_node_executions",
    )
    op.drop_table("workflow_node_executions")
    op.drop_index("ix_workflow_runs_workflow_definition_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_owner_status", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index(
        "ix_workflow_definitions_owner_status",
        table_name="workflow_definitions",
    )
    op.drop_table("workflow_definitions")
