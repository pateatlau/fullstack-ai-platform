"""SQLAlchemy 2.x models for the FastAPI persistence layer.

These models are the canonical contract for the schema described in Section 2
of ``docs/plans/database-persistence-plan.md``. Alembic migrations are generated
from this metadata; there is no second schema representation.

Conventions (plan Section 2.1):
- Primary keys are native PostgreSQL ``uuid`` with ``DEFAULT gen_random_uuid()``
    (requires the ``pgcrypto`` extension).
- Timestamps are ``timestamptz`` (UTC). Every table has ``created_at``; mutable
  tables also have ``updated_at``.
- Enum-like columns are ``text`` with ``CHECK`` constraints (no native enums).
- No soft-delete columns in the MVP.
"""

from __future__ import annotations

import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import Settings
from app.db.base import Base

_EMBEDDING_DIMENSIONS = Settings().embedding_dimensions

# Server-side default that generates a UUID inside PostgreSQL (requires pgcrypto).
_UUID_DEFAULT = text("gen_random_uuid()")
# Server-side default for UTC timestamps.
_NOW = func.now()


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=_UUID_DEFAULT,
    )


class User(Base):
    """Real Google-authenticated users only (plan Section 2.2)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str | None] = mapped_column(nullable=True)
    display_name: Mapped[str | None] = mapped_column(nullable=True)
    picture_url: Mapped[str | None] = mapped_column(nullable=True)
    auth_provider: Mapped[str] = mapped_column(
        nullable=False, server_default=text("'google'")
    )
    external_auth_id: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )

    __table_args__ = (
        UniqueConstraint(
            "auth_provider", "external_auth_id", name="uq_users_google_identity"
        ),
    )


class GuestIdentity(Base):
    """Server-owned guest continuity token (plan Section 2.3)."""

    __tablename__ = "guest_identities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    token_hash: Mapped[str] = mapped_column(nullable=False, unique=True)
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    created_ip_hash: Mapped[str | None] = mapped_column(nullable=True)
    linked_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class ChatSession(Base):
    """A chat session owned by exactly one caller (plan Section 2.4)."""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    guest_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guest_identities.id"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(nullable=True)
    next_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    last_message_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )

    __table_args__ = (
        # Exactly one owner: authenticated user XOR guest (plan Section 2.4).
        CheckConstraint(
            "(user_id IS NOT NULL) <> (guest_id IS NOT NULL)",
            name="owner_xor",
        ),
    )


class ChatMessage(Base):
    """Append-only, immutable chat messages (plan Section 2.5)."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    provider: Mapped[str | None] = mapped_column(nullable=True)
    model: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        nullable=False, server_default=text("'complete'")
    )
    finish_reason: Mapped[str | None] = mapped_column(nullable=True)
    client_message_id: Mapped[str | None] = mapped_column(nullable=True)
    pending_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_tool_approvals.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_chat_messages_session_seq"),
        CheckConstraint(
            "role IN ('system', 'user', 'assistant')",
            name="role_valid",
        ),
        CheckConstraint(
            "status IN ('complete', 'stopped', 'error', 'interrupted', "
            "'waiting_approval', 'rejected')",
            name="status_valid",
        ),
        # Idempotent append when a client_message_id is supplied (plan Section 2.5).
        Index(
            "uq_chat_messages_session_client_message_id",
            "session_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id IS NOT NULL"),
        ),
        # Ordered reads by per-session sequence.
        Index("ix_chat_messages_session_seq", "session_id", "seq"),
    )


class SessionSummary(Base):
    """Deterministic summarization boundary (plan Section 2.6)."""

    __tablename__ = "session_summaries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    covers_through_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id", "version", name="uq_session_summaries_session_version"
        ),
        # Fetch the latest valid summary quickly.
        Index(
            "ix_session_summaries_session_covers",
            "session_id",
            text("covers_through_seq DESC"),
        ),
    )


class UsageEvent(Base):
    """Append-only provider usage observability (plan Section 2.7)."""

    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    guest_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guest_identities.id"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_messages.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(nullable=False, server_default=text("'chat'"))
    provider: Mapped[str] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_source: Mapped[str] = mapped_column(nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )

    __table_args__ = (
        CheckConstraint("kind IN ('chat', 'summary')", name="kind_valid"),
        CheckConstraint(
            "token_source IN ('provider_reported', 'estimated')",
            name="token_source_valid",
        ),
        # Prevent double-counting on retry when a request_id is supplied.
        Index(
            "uq_usage_events_request_id",
            "request_id",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        Index("ix_usage_events_user_created", "user_id", "created_at"),
        Index("ix_usage_events_guest_created", "guest_id", "created_at"),
        Index("ix_usage_events_session_created", "session_id", "created_at"),
        Index(
            "ix_usage_events_provider_model_created",
            "provider",
            "model",
            "created_at",
        ),
    )


class GuestQuotaCounter(Base):
    """Durable windowed guest usage for quota enforcement (plan Section 2.8)."""

    __tablename__ = "guest_quota_counters"

    guest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guest_identities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    window_start: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )


class UploadQuotaCounter(Base):
    """Durable windowed authenticated upload counts (V1.1.1 demo protection)."""

    __tablename__ = "upload_quota_counters"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    window_start: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    upload_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )


class Document(Base):
    """Auth-owned uploaded document (Post-MVP V1 Phase 5)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(nullable=False)
    mime_type: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="status_valid",
        ),
        Index("ix_documents_user_created", "user_id", "created_at"),
    )


class DocumentChunk(Base):
    """Text chunk for a document; embeddings added in Phase 7."""

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    # Epic 02 Phase 4: generated FTS vector (english); maintained by Postgres.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
    )
    # Phase 7: pgvector column (nullable until KnowledgeService ingest persists).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(_EMBEDDING_DIMENSIONS),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_index"
        ),
        Index("ix_document_chunks_document_id", "document_id"),
        Index(
            "ix_document_chunks_content_tsv",
            "content_tsv",
            postgresql_using="gin",
        ),
    )


class MemoryRecord(Base):
    """Durable Memory subsystem record — user or project scoped (Epic 05 Phase 1).

    Separate from ``document_chunks`` (Part I § RAG boundary — no shared
    storage). The HNSW index on ``embedding`` is created in the Alembic
    migration, mirroring ``document_chunks`` (not declared in ``__table_args__``).
    """

    __tablename__ = "memory_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Set for memory_type='project' (v1 project scope == chat_session_id).
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=True,
    )
    memory_type: Mapped[str] = mapped_column(nullable=False)
    title: Mapped[str | None] = mapped_column(nullable=True)
    content: Mapped[str] = mapped_column(nullable=False)
    summary: Mapped[str | None] = mapped_column(nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(_EMBEDDING_DIMENSIONS),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    importance: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.5")
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.5")
    )
    quality_score: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.5")
    )
    lifecycle_state: Mapped[str] = mapped_column(
        nullable=False, server_default=text("'created'")
    )
    source: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )
    last_accessed_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("memory_type IN ('user', 'project')", name="memory_type_valid"),
        CheckConstraint(
            "lifecycle_state IN "
            "('created', 'active', 'consolidated', 'archived', 'deleted')",
            name="lifecycle_state_valid",
        ),
        Index(
            "ix_memory_records_owner_type_lifecycle",
            "owner_id",
            "memory_type",
            "lifecycle_state",
        ),
        Index(
            "ix_memory_records_session_id",
            "session_id",
            postgresql_where=text("session_id IS NOT NULL"),
        ),
    )


class UserPreference(Base):
    """Structured user preference — no embeddings, no vector search (Epic 05 Phase 1)."""

    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_preferences_user_key"),
    )


class WorkflowDefinitionRecord(Base):
    """Durable workflow graph definition (Epic 06 Phase 1).

    Graph structure is stored as JSONB ``{nodes, edges}``; independent of
    ``memory_records`` and ``document_chunks`` (Part I § Persistence Schema).
    """

    __tablename__ = "workflow_definitions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(nullable=False)
    entry_node_id: Mapped[str] = mapped_column(nullable=False)
    graph: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="workflow_definition_status_valid",
        ),
        UniqueConstraint(
            "owner_id",
            "name",
            "version",
            name="uq_workflow_definitions_owner_name_version",
        ),
        Index("ix_workflow_definitions_owner_status", "owner_id", "status"),
    )


class WorkflowRunRecord(Base):
    """A single workflow execution instance (Epic 06 Phase 1)."""

    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(nullable=False)
    context: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    current_node_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    checkpoint_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'completed', "
            "'failed', 'cancelled')",
            name="workflow_run_status_valid",
        ),
        UniqueConstraint(
            "owner_id",
            "workflow_definition_id",
            "idempotency_key",
            name="uq_workflow_runs_owner_definition_idempotency",
        ),
        Index("ix_workflow_runs_owner_status", "owner_id", "status"),
        Index("ix_workflow_runs_workflow_definition_id", "workflow_definition_id"),
    )


class WorkflowNodeExecutionRecord(Base):
    """Persisted node attempt within a workflow run (Epic 06 Phase 1)."""

    __tablename__ = "workflow_node_executions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(nullable=False)
    node_type: Mapped[str] = mapped_column(nullable=False)
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(nullable=False)
    input: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    output: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    decision: Mapped[str | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "node_type IN ('task', 'llm', 'agent', 'router', 'fork', 'join', "
            "'approval', 'terminal', 'plugin')",
            name="workflow_node_execution_type_valid",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'succeeded', "
            "'failed', 'skipped', 'cancelled')",
            name="workflow_node_execution_status_valid",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('approved', 'rejected')",
            name="workflow_node_execution_decision_valid",
        ),
        UniqueConstraint(
            "run_id",
            "node_id",
            "attempt",
            name="uq_workflow_node_executions_run_node_attempt",
        ),
        Index("ix_workflow_node_executions_run_status", "run_id", "status"),
    )


class AgentToolApprovalRecord(Base):
    """Persisted agent tool-call approval pause snapshot (Epic 09)."""

    __tablename__ = "agent_tool_approvals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    execution_id: Mapped[str] = mapped_column(nullable=False)
    approval_correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(nullable=False)
    proposed_calls: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    edited_calls: Mapped[list[object] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(nullable=True)
    paused_scratchpad: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    paused_state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    pending_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    decided_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=_NOW,
        onupdate=_NOW,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled')",
            name="agent_tool_approval_status_valid",
        ),
        Index("ix_agent_tool_approvals_owner_status", "owner_id", "status"),
        Index("ix_agent_tool_approvals_session_id", "session_id"),
    )


class ApprovalRevisionRecord(Base):
    """Append-only edit history for a pending approval (Epic 09)."""

    __tablename__ = "approval_revisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    approval_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approval_kind: Mapped[str] = mapped_column(nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    edited_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    edited_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )
    edited_payload: Mapped[object] = mapped_column(JSONB, nullable=False)
    note: Mapped[str | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "approval_kind IN ('agent_tool', 'workflow_node')",
            name="approval_revision_kind_valid",
        ),
        UniqueConstraint(
            "approval_id",
            "approval_kind",
            "revision_number",
            name="uq_approval_revisions_approval_kind_number",
        ),
        Index("ix_approval_revisions_approval_id_kind", "approval_id", "approval_kind"),
    )
