"""0003 pgvector embeddings

Revision ID: 0003_pgvector_embeddings
Revises: 0002_documents_and_chunks
Create Date: 2026-07-21

Enables the pgvector extension and migrates ``document_chunks.embedding`` from
nullable ``REAL[]`` (Phase 5 placeholder) to ``vector(1536)`` with an HNSW
cosine index.

Recovery: if ``CREATE EXTENSION vector`` fails on an existing volume created
with ``postgres:16-alpine``, reset local data with
``docker compose --profile python down -v`` then ``up -d postgres`` before
``alembic upgrade head``.

IVFFlat fallback (not used here): lower build cost and memory than HNSW but
requires ``ANALYZE`` and lists tuning; HNSW is preferred for dev/small corpora.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_pgvector_embeddings"
down_revision: Union[str, None] = "0002_documents_and_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Locked to Settings.embedding_dimensions default (1536).
_EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Phase 5 stored NULL placeholders only; drop/recreate is migration-safe.
    op.execute("ALTER TABLE document_chunks DROP COLUMN embedding")
    op.execute(
        f"ALTER TABLE document_chunks ADD COLUMN embedding "
        f"vector({_EMBEDDING_DIMENSIONS})"
    )

    op.execute(
        """
        CREATE INDEX ix_document_chunks_embedding_hnsw
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.execute("ALTER TABLE document_chunks DROP COLUMN embedding")
    op.execute(
        "ALTER TABLE document_chunks ADD COLUMN embedding double precision[]"
    )
    # Extension left installed (shared cluster resource); harmless on downgrade.
