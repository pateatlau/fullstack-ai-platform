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

from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

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
            "status IN ('complete', 'stopped', 'error', 'interrupted')",
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
    # Phase 5: nullable REAL[] placeholder until Phase 7 migrates to vector(N).
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_index"
        ),
        Index("ix_document_chunks_document_id", "document_id"),
    )
