"""0010 hitl tables

Revision ID: 0010_hitl_tables
Revises: 0009_workflow_plugin_node_type
Create Date: 2026-08-11

Epic 09 Phase 1: ``agent_tool_approvals``, ``approval_revisions``, and additive
extensions to ``chat_messages`` / ``workflow_node_executions``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010_hitl_tables"
down_revision: Union[str, None] = "0009_workflow_plugin_node_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")

_LEGACY_CHAT_STATUS_CHECK = (
    "status IN ('complete', 'stopped', 'error', 'interrupted')"
)
_HITL_CHAT_STATUS_CHECK = (
    "status IN ('complete', 'stopped', 'error', 'interrupted', "
    "'waiting_approval', 'rejected')"
)


def upgrade() -> None:
    op.create_table(
        "agent_tool_approvals",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("owner_id", _UUID, nullable=False),
        sa.Column("execution_id", sa.Text(), nullable=False),
        sa.Column("approval_correlation_id", _UUID, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("proposed_calls", postgresql.JSONB(), nullable=False),
        sa.Column("edited_calls", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("paused_scratchpad", postgresql.JSONB(), nullable=False),
        sa.Column("paused_state", postgresql.JSONB(), nullable=False),
        sa.Column("pending_message_id", _UUID, nullable=True),
        sa.Column(
            "requested_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("decided_by", _UUID, nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_agent_tool_approvals"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_agent_tool_approvals_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_agent_tool_approvals_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name="fk_agent_tool_approvals_decided_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled')",
            name="agent_tool_approval_status_valid",
        ),
    )
    op.create_index(
        "ix_agent_tool_approvals_owner_status",
        "agent_tool_approvals",
        ["owner_id", "status"],
    )
    op.create_index(
        "ix_agent_tool_approvals_session_id",
        "agent_tool_approvals",
        ["session_id"],
    )

    op.create_table(
        "approval_revisions",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("approval_id", _UUID, nullable=False),
        sa.Column("approval_kind", sa.Text(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("edited_by", _UUID, nullable=False),
        sa.Column(
            "edited_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column("edited_payload", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_approval_revisions"),
        sa.ForeignKeyConstraint(
            ["edited_by"],
            ["users.id"],
            name="fk_approval_revisions_edited_by_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "approval_kind IN ('agent_tool', 'workflow_node')",
            name="approval_revision_kind_valid",
        ),
        sa.UniqueConstraint(
            "approval_id",
            "approval_kind",
            "revision_number",
            name="uq_approval_revisions_approval_kind_number",
        ),
    )
    op.create_index(
        "ix_approval_revisions_approval_id_kind",
        "approval_revisions",
        ["approval_id", "approval_kind"],
    )

    op.drop_constraint("status_valid", "chat_messages", type_="check")
    op.create_check_constraint(
        "status_valid",
        "chat_messages",
        _HITL_CHAT_STATUS_CHECK,
    )
    op.add_column(
        "chat_messages",
        sa.Column("pending_approval_id", _UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_messages_pending_approval_id_agent_tool_approvals",
        "chat_messages",
        "agent_tool_approvals",
        ["pending_approval_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "fk_agent_tool_approvals_pending_message_id_chat_messages",
        "agent_tool_approvals",
        "chat_messages",
        ["pending_message_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "workflow_node_executions",
        sa.Column("edited_arguments", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "workflow_node_executions",
        sa.Column("reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_node_executions", "reason")
    op.drop_column("workflow_node_executions", "edited_arguments")

    op.drop_constraint(
        "fk_agent_tool_approvals_pending_message_id_chat_messages",
        "agent_tool_approvals",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_chat_messages_pending_approval_id_agent_tool_approvals",
        "chat_messages",
        type_="foreignkey",
    )
    op.drop_column("chat_messages", "pending_approval_id")
    op.drop_constraint("status_valid", "chat_messages", type_="check")
    op.create_check_constraint(
        "status_valid",
        "chat_messages",
        _LEGACY_CHAT_STATUS_CHECK,
    )

    op.drop_index(
        "ix_approval_revisions_approval_id_kind",
        table_name="approval_revisions",
    )
    op.drop_table("approval_revisions")

    op.drop_index(
        "ix_agent_tool_approvals_session_id",
        table_name="agent_tool_approvals",
    )
    op.drop_index(
        "ix_agent_tool_approvals_owner_status",
        table_name="agent_tool_approvals",
    )
    op.drop_table("agent_tool_approvals")
