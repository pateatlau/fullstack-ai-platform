"""init chat persistence

Revision ID: 0001_init_chat_persistence
Revises:
Create Date: 2026-07-17

Greenfield baseline creating all Section 2 tables, constraints, and indexes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_init_chat_persistence"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("picture_url", sa.Text(), nullable=True),
        sa.Column(
            "auth_provider",
            sa.Text(),
            server_default=sa.text("'google'"),
            nullable=False,
        ),
        sa.Column("external_auth_id", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint(
            "auth_provider", "external_auth_id", name="uq_users_google_identity"
        ),
    )

    op.create_table(
        "guest_identities",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column("created_ip_hash", sa.Text(), nullable=True),
        sa.Column("linked_user_id", _UUID, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_guest_identities"),
        sa.UniqueConstraint("token_hash", name="uq_guest_identities_token_hash"),
        sa.ForeignKeyConstraint(
            ["linked_user_id"],
            ["users.id"],
            name="fk_guest_identities_linked_user_id_users",
        ),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", _UUID, nullable=True),
        sa.Column("guest_id", _UUID, nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "next_seq", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("last_message_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_chat_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_chat_sessions_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["guest_id"],
            ["guest_identities.id"],
            name="fk_chat_sessions_guest_id_guest_identities",
        ),
        sa.CheckConstraint(
            "(user_id IS NOT NULL) <> (guest_id IS NOT NULL)",
            name="owner_xor",
        ),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'complete'"),
            nullable=False,
        ),
        sa.Column("finish_reason", sa.Text(), nullable=True),
        sa.Column("client_message_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chat_messages"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_chat_messages_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("session_id", "seq", name="uq_chat_messages_session_seq"),
        sa.CheckConstraint(
            "role IN ('system', 'user', 'assistant')",
            name="role_valid",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'stopped', 'error', 'interrupted')",
            name="status_valid",
        ),
    )
    op.create_index(
        "uq_chat_messages_session_client_message_id",
        "chat_messages",
        ["session_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_chat_messages_session_seq",
        "chat_messages",
        ["session_id", "seq"],
    )

    op.create_table(
        "session_summaries",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("covers_through_seq", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_summaries"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_session_summaries_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "session_id", "version", name="uq_session_summaries_session_version"
        ),
    )
    op.create_index(
        "ix_session_summaries_session_covers",
        "session_summaries",
        ["session_id", sa.text("covers_through_seq DESC")],
    )

    op.create_table(
        "usage_events",
        sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=True),
        sa.Column("guest_id", _UUID, nullable=True),
        sa.Column("message_id", _UUID, nullable=True),
        sa.Column("kind", sa.Text(), server_default=sa.text("'chat'"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("token_source", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage_events"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_usage_events_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_usage_events_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["guest_id"],
            ["guest_identities.id"],
            name="fk_usage_events_guest_id_guest_identities",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name="fk_usage_events_message_id_chat_messages",
        ),
        sa.CheckConstraint(
            "kind IN ('chat', 'summary')", name="kind_valid"
        ),
        sa.CheckConstraint(
            "token_source IN ('provider_reported', 'estimated')",
            name="token_source_valid",
        ),
    )
    op.create_index(
        "uq_usage_events_request_id",
        "usage_events",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )
    op.create_index(
        "ix_usage_events_user_created", "usage_events", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_usage_events_guest_created", "usage_events", ["guest_id", "created_at"]
    )
    op.create_index(
        "ix_usage_events_session_created", "usage_events", ["session_id", "created_at"]
    )

    op.create_table(
        "guest_quota_counters",
        sa.Column("guest_id", _UUID, nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column(
            "message_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "guest_id", "window_start", name="pk_guest_quota_counters"
        ),
        sa.ForeignKeyConstraint(
            ["guest_id"],
            ["guest_identities.id"],
            name="fk_guest_quota_counters_guest_id_guest_identities",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    # Greenfield baseline rollback = drop all tables (safe pre-traffic only).
    op.drop_table("guest_quota_counters")
    op.drop_index("ix_usage_events_session_created", table_name="usage_events")
    op.drop_index("ix_usage_events_guest_created", table_name="usage_events")
    op.drop_index("ix_usage_events_user_created", table_name="usage_events")
    op.drop_index("uq_usage_events_request_id", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index(
        "ix_session_summaries_session_covers", table_name="session_summaries"
    )
    op.drop_table("session_summaries")
    op.drop_index("ix_chat_messages_session_seq", table_name="chat_messages")
    op.drop_index(
        "uq_chat_messages_session_client_message_id", table_name="chat_messages"
    )
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("guest_identities")
    op.drop_table("users")
