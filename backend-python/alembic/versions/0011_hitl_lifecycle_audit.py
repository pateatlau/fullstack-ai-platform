"""0011 hitl lifecycle audit

Revision ID: 0011_hitl_lifecycle_audit
Revises: 0010_hitl_tables
Create Date: 2026-08-12

Epic 09 improvements: additive ``agent_tool_approvals`` columns for lazy
expiration, richer audit metadata, a multi-stage approval checklist
scaffold, and an optimistic-locking version counter.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011_hitl_lifecycle_audit"
down_revision: Union[str, None] = "0010_hitl_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_tool_approvals",
        sa.Column("comments", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_tool_approvals",
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_tool_approvals",
        sa.Column("request_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_tool_approvals",
        sa.Column("source_ip", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_tool_approvals",
        sa.Column(
            "client_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "agent_tool_approvals",
        sa.Column(
            "required_stages",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "agent_tool_approvals",
        sa.Column(
            "stage_decisions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "agent_tool_approvals",
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_tool_approvals", "version")
    op.drop_column("agent_tool_approvals", "stage_decisions")
    op.drop_column("agent_tool_approvals", "required_stages")
    op.drop_column("agent_tool_approvals", "client_metadata")
    op.drop_column("agent_tool_approvals", "source_ip")
    op.drop_column("agent_tool_approvals", "request_id")
    op.drop_column("agent_tool_approvals", "expires_at")
    op.drop_column("agent_tool_approvals", "comments")
