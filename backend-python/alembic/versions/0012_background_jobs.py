"""0012 background jobs

Revision ID: 0012_background_jobs
Revises: 0011_hitl_lifecycle_audit
Create Date: 2026-08-12

Epic 10 Phase 1: ``background_jobs`` queue table (schedules land in Phase 2).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0012_background_jobs"
down_revision: Union[str, None] = "0011_hitl_lifecycle_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")
_JOB_STATUS_CHECK = (
    "status IN ('queued', 'running', 'succeeded', 'failed', "
    "'dead_letter', 'cancelled')"
)


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "run_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("schedule_id", _UUID, nullable=True),
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
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_background_jobs"),
        sa.CheckConstraint(_JOB_STATUS_CHECK, name="background_job_status_valid"),
    )
    op.create_index(
        "ix_background_jobs_status_run_at",
        "background_jobs",
        ["status", "run_at"],
    )
    op.create_index(
        "uq_background_jobs_idempotency_key",
        "background_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_background_jobs_idempotency_key", table_name="background_jobs")
    op.drop_index("ix_background_jobs_status_run_at", table_name="background_jobs")
    op.drop_table("background_jobs")
