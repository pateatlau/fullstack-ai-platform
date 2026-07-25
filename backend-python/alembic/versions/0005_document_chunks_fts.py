"""0005 document_chunks full-text search

Revision ID: 0005_document_chunks_fts
Revises: 0004_upload_quota_counters
Create Date: 2026-07-25

Adds a generated stored ``tsvector`` column on ``document_chunks.content``
(``english`` config) plus a GIN index for hybrid lexical retrieval (Epic 02 Phase 4).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005_document_chunks_fts"
down_revision: Union[str, None] = "0004_upload_quota_counters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX ix_document_chunks_content_tsv
        ON document_chunks
        USING gin (content_tsv)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN content_tsv")
