"""0006 memory tables

Revision ID: 0006_memory_tables
Revises: 0005_document_chunks_fts
Create Date: 2026-08-01

Epic 05 Phase 1: creates ``memory_records`` (durable user/project-scoped
semantic memories) and ``user_preferences`` (structured settings, no
embeddings). Independent of ``document_chunks`` (Part I § RAG boundary — no
shared storage tables). Assumes the ``vector`` extension is already enabled
(migration 0003_pgvector_embeddings).
"""

from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_memory_tables"
down_revision: Union[str, None] = "0005_document_chunks_fts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")

# Locked to Settings.embedding_dimensions default (1536); same as document_chunks.
_EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.create_table(
        "memory_records",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("owner_id", _UUID, nullable=False),
        sa.Column("session_id", _UUID, nullable=True),
        sa.Column("memory_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(_EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "importance", sa.Float(), server_default=sa.text("0.5"), nullable=False
        ),
        sa.Column(
            "confidence", sa.Float(), server_default=sa.text("0.5"), nullable=False
        ),
        sa.Column(
            "quality_score", sa.Float(), server_default=sa.text("0.5"), nullable=False
        ),
        sa.Column(
            "lifecycle_state",
            sa.Text(),
            server_default=sa.text("'created'"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
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
        sa.Column("last_accessed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_memory_records"),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_memory_records_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_memory_records_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "memory_type IN ('user', 'project')", name="memory_type_valid"
        ),
        sa.CheckConstraint(
            "lifecycle_state IN "
            "('created', 'active', 'consolidated', 'archived', 'deleted')",
            name="lifecycle_state_valid",
        ),
    )
    op.create_index(
        "ix_memory_records_owner_type_lifecycle",
        "memory_records",
        ["owner_id", "memory_type", "lifecycle_state"],
    )
    op.create_index(
        "ix_memory_records_session_id",
        "memory_records",
        ["session_id"],
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE INDEX ix_memory_records_embedding_hnsw
        ON memory_records
        USING hnsw (embedding vector_cosine_ops)
        """
    )

    op.create_table(
        "user_preferences",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_user_preferences"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_preferences_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "key", name="uq_user_preferences_user_key"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.execute("DROP INDEX IF EXISTS ix_memory_records_embedding_hnsw")
    op.drop_index("ix_memory_records_session_id", table_name="memory_records")
    op.drop_index(
        "ix_memory_records_owner_type_lifecycle", table_name="memory_records"
    )
    op.drop_table("memory_records")
