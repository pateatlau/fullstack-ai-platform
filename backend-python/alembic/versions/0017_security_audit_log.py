"""0017 security audit log

Revision ID: 0017_security_audit_log
Revises: 0016_security_rbac
Create Date: 2026-08-14

Epic 11 Phase 3: ``audit_events`` table and a seeded (disabled by default)
``security-audit-retention-cleanup`` Background Jobs schedule row.
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_security_audit_log"
down_revision: Union[str, None] = "0016_security_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_ACTOR_KIND_CHECK = "actor_kind IN ('user', 'guest', 'system')"
_OUTCOME_CHECK = "outcome IN ('success', 'denied', 'error')"

_SCHEDULE_NAME = "security-audit-retention-cleanup"
_SCHEDULE_JOB_TYPE = "security_audit_retention_cleanup"
_SCHEDULE_INTERVAL_SECONDS = 86400
_SCHEDULE_MIGRATION_MARKER = revision


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("actor_user_id", _UUID, nullable=True),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("source_ip_hash", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.CheckConstraint(_ACTOR_KIND_CHECK, name="audit_events_actor_kind_valid"),
        sa.CheckConstraint(_OUTCOME_CHECK, name="audit_events_outcome_valid"),
    )
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_resource_type", "audit_events", ["resource_type"])
    op.create_index("ix_audit_events_outcome", "audit_events", ["outcome"])
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])

    op.execute(
        sa.text(
            """
            INSERT INTO background_job_schedules (
                id, name, job_type, payload, interval_seconds,
                next_run_at, version, status, created_at, updated_at
            )
            SELECT
                :id,
                :name,
                :job_type,
                jsonb_build_object(
                    'version', 1,
                    '_migration_revision', :migration_marker
                ),
                :interval_seconds,
                now(),
                1,
                'disabled',
                now(),
                now()
            WHERE NOT EXISTS (
                SELECT 1 FROM background_job_schedules WHERE name = :name
            )
            """
        ).bindparams(
            id=uuid.uuid4(),
            name=_SCHEDULE_NAME,
            job_type=_SCHEDULE_JOB_TYPE,
            interval_seconds=_SCHEDULE_INTERVAL_SECONDS,
            migration_marker=_SCHEDULE_MIGRATION_MARKER,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM background_job_schedules
            WHERE name = :name
              AND payload->>'_migration_revision' = :migration_marker
            """
        ).bindparams(
            name=_SCHEDULE_NAME,
            migration_marker=_SCHEDULE_MIGRATION_MARKER,
        )
    )
    op.drop_table("audit_events")
