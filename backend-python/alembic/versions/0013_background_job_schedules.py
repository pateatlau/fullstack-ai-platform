"""0013 background job schedules

Revision ID: 0013_background_job_schedules
Revises: 0012_background_jobs
Create Date: 2026-08-12

Epic 10 Phase 2: ``background_job_schedules`` table, FK, seeded defaults.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013_background_job_schedules"
down_revision: Union[str, None] = "0012_background_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")
_SCHEDULE_STATUS_CHECK = "status IN ('enabled', 'disabled')"

_DEFAULT_SCHEDULES: tuple[tuple[str, str, int, str], ...] = (
    ("hitl-approval-expiry-sweep", "hitl_approval_expiry_sweep", 300, "enabled"),
    ("hitl-orphaned-snapshot-sweep", "hitl_orphaned_snapshot_sweep", 900, "enabled"),
    ("workflow-run-retention-cleanup", "workflow_run_retention_cleanup", 86400, "enabled"),
    ("scheduled-evaluation-run", "scheduled_evaluation_run", 86400, "disabled"),
)


def upgrade() -> None:
    op.create_table(
        "background_job_schedules",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{\"version\": 1}'::jsonb"),
        ),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "next_run_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_background_job_schedules"),
        sa.UniqueConstraint("name", name="uq_background_job_schedules_name"),
        sa.CheckConstraint(
            _SCHEDULE_STATUS_CHECK,
            name="background_job_schedule_status_valid",
        ),
    )
    op.create_index(
        "ix_background_job_schedules_status_next_run_at",
        "background_job_schedules",
        ["status", "next_run_at"],
    )

    for name, job_type, interval_seconds, status in _DEFAULT_SCHEDULES:
        op.execute(
            sa.text(
                """
                INSERT INTO background_job_schedules (
                    name, job_type, payload, interval_seconds,
                    next_run_at, version, status, created_at, updated_at
                )
                SELECT
                    :name,
                    :job_type,
                    '{"version": 1}'::jsonb,
                    :interval_seconds,
                    now(),
                    1,
                    :status,
                    now(),
                    now()
                WHERE NOT EXISTS (
                    SELECT 1 FROM background_job_schedules WHERE name = :name
                )
                """
            ).bindparams(
                name=name,
                job_type=job_type,
                interval_seconds=interval_seconds,
                status=status,
            )
        )

    op.create_foreign_key(
        "fk_background_jobs_schedule_id",
        "background_jobs",
        "background_job_schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_background_jobs_schedule_id",
        "background_jobs",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_background_job_schedules_status_next_run_at",
        table_name="background_job_schedules",
    )
    op.drop_table("background_job_schedules")
