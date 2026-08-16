"""Epic 11 Phase 5 generic usage quota counters."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_usage_quota_counters"
down_revision: Union[str, None] = "0017_security_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_quota_counters",
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("quota_type", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("subject_id", "quota_type", "day", name="pk_usage_quota_counters"),
    )


def downgrade() -> None:
    op.drop_table("usage_quota_counters")
