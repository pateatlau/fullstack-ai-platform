"""0015 document upload staging

Revision ID: 0015_document_upload_staging
Revises: 0014_hitl_expired_status_checks
Create Date: 2026-08-12

Epic 10 Phase 5: durable upload byte staging for queue-backed RAG indexing.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_document_upload_staging"
down_revision: Union[str, None] = "0014_hitl_expired_status_checks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "document_upload_staging",
        sa.Column("document_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("file_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="document_upload_staging_document_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="document_upload_staging_user_id_fkey",
        ),
        sa.PrimaryKeyConstraint("document_id"),
    )


def downgrade() -> None:
    op.drop_table("document_upload_staging")
