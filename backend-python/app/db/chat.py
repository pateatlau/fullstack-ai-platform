"""SQLAlchemy-backed chat persistence (plan Section 8.2).

Owns chat sessions, messages, and per-session sequence allocation. Summary
persistence is added when summarization lands (Phase 6). Methods here implement
only what the current MVP flows need — no generic repository framework.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, ChatSession, GuestIdentity, SessionSummary

#: Cap for the session list endpoint (plan Section 2.2, D1: simple cap, no
#: cursor pagination for MVP).
DEFAULT_SESSION_LIST_LIMIT = 50


class SqlChatStore:
    """Persist and read chat sessions and messages against an ``AsyncSession``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(
        self,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        title: str | None = None,
    ) -> ChatSession:
        chat_session = ChatSession(user_id=user_id, guest_id=guest_id, title=title)
        self._session.add(chat_session)
        await self._session.flush()
        return chat_session

    async def get_owned_session(
        self,
        session_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
    ) -> ChatSession | None:
        """Fetch a session filtered by its owner (ownership mismatch → None).

        Authenticated callers (``user_id``) also match a session owned by any
        guest identity linked to their account — the same read-time projection
        used by ``list_sessions_for_owner`` (plan Section 2.6) — so a linked
        guest session that appears in the list can also be resumed/appended to.
        """
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        if user_id is not None:
            linked_guest_ids = select(GuestIdentity.id).where(
                GuestIdentity.linked_user_id == user_id
            )
            stmt = stmt.where(
                or_(
                    ChatSession.user_id == user_id,
                    ChatSession.guest_id.in_(linked_guest_ids),
                )
            )
        elif guest_id is not None:
            stmt = stmt.where(ChatSession.guest_id == guest_id)
        else:
            return None
        return await self._session.scalar(stmt)

    async def get_default_guest_session(
        self, guest_id: uuid.UUID
    ) -> ChatSession | None:
        """Fetch the guest's single default session, if one exists.

        Guests are restricted to exactly one session (plan Section 2.3,
        application-level enforcement). When multiple rows exist (e.g. legacy
        data predating this constraint), the earliest-created session is the
        canonical default; ``id`` is a tiebreaker for deterministic selection
        when ``created_at`` values collide.
        """
        return await self._session.scalar(
            select(ChatSession)
            .where(ChatSession.guest_id == guest_id)
            .order_by(ChatSession.created_at.asc(), ChatSession.id.asc())
            .limit(1)
        )

    async def list_sessions_for_owner(
        self,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        limit: int = DEFAULT_SESSION_LIST_LIMIT,
    ) -> list[ChatSession]:
        """List the caller's sessions, most-recently-active first (plan Section 2.2).

        Authenticated callers (``user_id``) also see sessions owned by any
        guest identity linked to their account — a read-time projection (plan
        Section 2.6) with no ownership migration. Guest callers see only their
        single default session (plan Section 2.3).
        """
        if user_id is not None:
            linked_guest_ids = select(GuestIdentity.id).where(
                GuestIdentity.linked_user_id == user_id
            )
            stmt = (
                select(ChatSession)
                .where(
                    or_(
                        ChatSession.user_id == user_id,
                        ChatSession.guest_id.in_(linked_guest_ids),
                    )
                )
                .order_by(
                    ChatSession.last_message_at.desc().nulls_last(),
                    ChatSession.created_at.desc(),
                )
                .limit(limit)
            )
            result = await self._session.scalars(stmt)
            return list(result)

        if guest_id is not None:
            default = await self.get_default_guest_session(guest_id)
            return [default] if default is not None else []

        return []

    async def delete_session(self, session_id: uuid.UUID) -> bool:
        """Delete a chat session row by id (ownership verified by service layer).

        Child rows cascade via FK ``ondelete="CASCADE"``.
        """
        chat_session = await self._session.get(ChatSession, session_id)
        if chat_session is None:
            return False
        await self._session.delete(chat_session)
        return True

    async def allocate_seq(self, session_id: uuid.UUID) -> int:
        """Assign the next gap-free per-session sequence number (plan Section 2.11).

        Reads ``next_seq`` under ``SELECT ... FOR UPDATE`` (row lock) and advances
        it, so concurrent appends to the same session cannot collide.
        """
        seq = await self._session.scalar(
            select(ChatSession.next_seq)
            .where(ChatSession.id == session_id)
            .with_for_update()
        )
        if seq is None:
            raise ValueError(f"Unknown chat session: {session_id}")
        await self._session.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(next_seq=seq + 1)
        )
        return seq

    async def add_message(
        self,
        *,
        session_id: uuid.UUID,
        seq: int,
        role: str,
        content: str,
        provider: str | None = None,
        model: str | None = None,
        status: str = "complete",
        finish_reason: str | None = None,
        client_message_id: str | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=session_id,
            seq=seq,
            role=role,
            content=content,
            provider=provider,
            model=model,
            status=status,
            finish_reason=finish_reason,
            client_message_id=client_message_id,
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def list_messages(self, session_id: uuid.UUID) -> list[ChatMessage]:
        result = await self._session.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq)
        )
        return list(result)

    async def find_by_client_message_id(
        self, session_id: uuid.UUID, client_message_id: str
    ) -> ChatMessage | None:
        """Look up a prior append by its idempotency key (plan Section 2.11)."""
        return await self._session.scalar(
            select(ChatMessage).where(
                ChatMessage.session_id == session_id,
                ChatMessage.client_message_id == client_message_id,
            )
        )

    async def get_message_by_seq(
        self, session_id: uuid.UUID, seq: int
    ) -> ChatMessage | None:
        return await self._session.scalar(
            select(ChatMessage).where(
                ChatMessage.session_id == session_id,
                ChatMessage.seq == seq,
            )
        )

    async def mark_last_message_at(self, session_id: uuid.UUID) -> None:
        await self._session.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(last_message_at=func.now())
        )

    # ---- summaries (plan Sections 2.6, 5.5, 5.6) ----------------------------

    async def list_messages_after_seq(
        self, session_id: uuid.UUID, after_seq: int
    ) -> list[ChatMessage]:
        result = await self._session.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.seq > after_seq,
            )
            .order_by(ChatMessage.seq)
        )
        return list(result)

    async def get_latest_summary(self, session_id: uuid.UUID) -> SessionSummary | None:
        return await self._session.scalar(
            select(SessionSummary)
            .where(SessionSummary.session_id == session_id)
            .order_by(SessionSummary.version.desc())
            .limit(1)
        )

    async def add_summary(
        self,
        *,
        session_id: uuid.UUID,
        version: int,
        covers_through_seq: int,
        content: str,
        provider: str,
        model: str,
    ) -> SessionSummary:
        summary = SessionSummary(
            session_id=session_id,
            version=version,
            covers_through_seq=covers_through_seq,
            content=content,
            provider=provider,
            model=model,
        )
        self._session.add(summary)
        await self._session.flush()
        return summary
