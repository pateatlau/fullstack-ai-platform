"""0008 observability usage cost

Revision ID: 0008_observability_usage_cost
Revises: 0007_workflow_tables
Create Date: 2026-08-08

Epic 07 Phase 5: additive ``cost_usd`` / ``pricing_version`` on ``usage_events``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_observability_usage_cost"
down_revision: Union[str, None] = "0007_workflow_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "usage_events",
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.add_column(
        "usage_events",
        sa.Column("pricing_version", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_usage_events_provider_model_created",
        "usage_events",
        ["provider", "model", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_usage_events_provider_model_created",
        table_name="usage_events",
    )
    op.drop_column("usage_events", "pricing_version")
    op.drop_column("usage_events", "cost_usd")
