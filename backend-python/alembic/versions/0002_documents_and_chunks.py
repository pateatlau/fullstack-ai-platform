"""0002 documents and chunks

Revision ID: 0002_documents_and_chunks
Revises: 0001_init_chat_persistence
Create Date: 2026-07-20

Adds auth-owned documents and text chunks for Phase 5 ingestion.

Embedding column strategy:
- Phase 5 stores ``embedding`` as nullable ``REAL[]`` (no pgvector extension).
- Phase 7 will ``CREATE EXTENSION vector`` and migrate this column to
  ``vector(N)`` with an HNSW index. No vector index is created here.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_documents_and_chunks"
down_revision: Union[str, None] = "0001_init_chat_persistence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_documents_user_id_users"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="status_valid",
        ),
    )
    op.create_index(
        "ix_documents_user_created",
        "documents",
        ["user_id", "created_at"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("document_id", _UUID, nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        # Nullable placeholder until Phase 7 pgvector migration (see module docstring).
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
    )
    op.create_index(
        "ix_document_chunks_document_id",
        "document_chunks",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_user_created", table_name="documents")
    op.drop_table("documents")
