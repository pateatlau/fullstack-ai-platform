import asyncio
import datetime
import uuid
from typing import AsyncIterator

from app.db.models import (
    ChatMessage,
    ChatSession,
    GuestIdentity,
    SessionSummary,
    UsageEvent,
    User,
)
from app.providers.base import ProviderChunk, ProviderCompletion, ProviderUsage
from app.schemas.chat import ChatMessageSchema
from app.services.auth_service import GoogleClaims


class FakeProvider:
    """Deterministic `LLMProvider` test double — no network calls.

    Streams the words of a fixed (or injected) response one at a time so
    endpoint/streaming tests can assert on chunk sequencing without hitting
    a real LLM API. Reports fixed provider usage by default.
    """

    def __init__(
        self,
        response: str = "Hello from the fake provider.",
        usage: ProviderUsage | None = ProviderUsage(
            prompt_tokens=11, completion_tokens=7, total_tokens=18
        ),
    ) -> None:
        self.response = response
        self.usage = usage

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        words = self.response.split(" ")
        for i, word in enumerate(words):
            await asyncio.sleep(
                0.05
            )  # simulate token pacing for manual SSE verification
            is_last = i == len(words) - 1
            content = word if is_last else f"{word} "
            yield ProviderChunk(
                content=content,
                finish_reason="stop" if is_last else None,
            )

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> ProviderCompletion:
        return ProviderCompletion(
            content=self.response,
            finish_reason="stop",
            usage=self.usage,
        )


class FakeGoogleVerifier:
    """In-memory Google token verifier for auth unit tests (no network).

    Returns fixed claims for any token, unless configured to raise (to simulate
    an invalid/unverifiable Google ID token).
    """

    def __init__(
        self,
        claims: GoogleClaims | None = None,
        error: Exception | None = None,
    ) -> None:
        self._claims = claims
        self._error = error
        self.calls: list[str] = []

    async def verify(self, raw_id_token: str) -> GoogleClaims:
        self.calls.append(raw_id_token)
        if self._error is not None:
            raise self._error
        assert self._claims is not None
        return self._claims


class FakeUserStore:
    """In-memory ``UserStore`` for auth unit tests (no database)."""

    def __init__(self) -> None:
        self.users: list[User] = []

    async def get_by_google_sub(self, sub: str) -> User | None:
        return next(
            (
                user
                for user in self.users
                if user.auth_provider == "google" and user.external_auth_id == sub
            ),
            None,
        )

    async def create(
        self,
        *,
        sub: str,
        email: str | None,
        name: str | None,
        picture: str | None,
    ) -> User:
        user = User(
            id=uuid.uuid4(),
            auth_provider="google",
            external_auth_id=sub,
            email=email,
            display_name=name,
            picture_url=picture,
        )
        self.users.append(user)
        return user

    async def update_profile(
        self,
        user: User,
        *,
        email: str | None,
        name: str | None,
        picture: str | None,
    ) -> User:
        if name is not None:
            user.display_name = name
        if picture is not None:
            user.picture_url = picture
        if email is not None:
            user.email = email
        return user


class FakeGuestStore:
    """In-memory guest-identity store for caller-resolution unit tests."""

    def __init__(self) -> None:
        self.guests: list[GuestIdentity] = []
        self.touched: list[uuid.UUID] = []
        self.linked: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def get_by_token_hash(self, token_hash: str) -> GuestIdentity | None:
        return next(
            (guest for guest in self.guests if guest.token_hash == token_hash),
            None,
        )

    async def create(
        self,
        *,
        token_hash: str,
        created_ip_hash: str | None = None,
    ) -> GuestIdentity:
        guest = GuestIdentity(
            id=uuid.uuid4(),
            token_hash=token_hash,
            created_ip_hash=created_ip_hash,
        )
        self.guests.append(guest)
        return guest

    async def touch(self, guest_id: uuid.UUID) -> None:
        self.touched.append(guest_id)

    async def link_to_user(self, guest_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.linked.append((guest_id, user_id))


class FakeGuestQuotaStore:
    """In-memory windowed guest quota counters for quota unit tests."""

    def __init__(self) -> None:
        self.counters: dict[tuple[uuid.UUID, object], int] = {}
        self.token_totals: dict[tuple[uuid.UUID, object], int] = {}

    async def get_message_count(self, guest_id: uuid.UUID, window_start: object) -> int:
        return self.counters.get((guest_id, window_start), 0)

    async def increment(
        self,
        guest_id: uuid.UUID,
        window_start: object,
        *,
        tokens: int = 0,
    ) -> None:
        key = (guest_id, window_start)
        self.counters[key] = self.counters.get(key, 0) + 1
        self.token_totals[key] = self.token_totals.get(key, 0) + tokens


class FakeChatStore:
    """In-memory chat persistence for chat-flow unit tests (no database)."""

    def __init__(self) -> None:
        self.sessions: dict[uuid.UUID, ChatSession] = {}
        self.messages: list[ChatMessage] = []
        self.summaries: list[SessionSummary] = []
        # Test-only injection point mirroring the SQL store's read-time
        # projection (plan Section 2.6): user_id -> set of linked guest_ids.
        self.linked_guest_ids_by_user: dict[uuid.UUID, set[uuid.UUID]] = {}

    async def create_session(
        self,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        title: str | None = None,
    ) -> ChatSession:
        chat_session = ChatSession(
            id=uuid.uuid4(),
            user_id=user_id,
            guest_id=guest_id,
            title=title,
            next_seq=1,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        self.sessions[chat_session.id] = chat_session
        return chat_session

    async def get_owned_session(
        self,
        session_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
    ) -> ChatSession | None:
        chat_session = self.sessions.get(session_id)
        if chat_session is None:
            return None
        if user_id is not None and chat_session.user_id == user_id:
            return chat_session
        if guest_id is not None and chat_session.guest_id == guest_id:
            return chat_session
        return None

    async def get_default_guest_session(
        self, guest_id: uuid.UUID
    ) -> ChatSession | None:
        guest_sessions = [s for s in self.sessions.values() if s.guest_id == guest_id]
        if not guest_sessions:
            return None
        return min(guest_sessions, key=lambda s: s.created_at or datetime.datetime.min)

    async def list_sessions_for_owner(
        self,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[ChatSession]:
        if user_id is not None:
            linked_ids = self.linked_guest_ids_by_user.get(user_id, set())
            sessions = [
                s
                for s in self.sessions.values()
                if s.user_id == user_id
                or (s.guest_id is not None and s.guest_id in linked_ids)
            ]
            epoch = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
            sessions.sort(
                key=lambda s: (s.last_message_at or epoch, s.created_at or epoch),
                reverse=True,
            )
            return sessions[:limit]
        if guest_id is not None:
            default = await self.get_default_guest_session(guest_id)
            return [default] if default is not None else []
        return []

    async def allocate_seq(self, session_id: uuid.UUID) -> int:
        chat_session = self.sessions[session_id]
        seq = chat_session.next_seq
        chat_session.next_seq = seq + 1
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
            id=uuid.uuid4(),
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
        self.messages.append(message)
        return message

    async def list_messages(self, session_id: uuid.UUID) -> list[ChatMessage]:
        return sorted(
            (m for m in self.messages if m.session_id == session_id),
            key=lambda m: m.seq,
        )

    async def find_by_client_message_id(
        self, session_id: uuid.UUID, client_message_id: str
    ) -> ChatMessage | None:
        return next(
            (
                m
                for m in self.messages
                if m.session_id == session_id
                and m.client_message_id == client_message_id
            ),
            None,
        )

    async def get_message_by_seq(
        self, session_id: uuid.UUID, seq: int
    ) -> ChatMessage | None:
        return next(
            (m for m in self.messages if m.session_id == session_id and m.seq == seq),
            None,
        )

    async def mark_last_message_at(self, session_id: uuid.UUID) -> None:
        chat_session = self.sessions.get(session_id)
        if chat_session is not None:
            chat_session.last_message_at = datetime.datetime.now(datetime.timezone.utc)

    async def list_messages_after_seq(
        self, session_id: uuid.UUID, after_seq: int
    ) -> list[ChatMessage]:
        return sorted(
            (
                m
                for m in self.messages
                if m.session_id == session_id and m.seq > after_seq
            ),
            key=lambda m: m.seq,
        )

    async def get_latest_summary(self, session_id: uuid.UUID) -> SessionSummary | None:
        summaries = [s for s in self.summaries if s.session_id == session_id]
        if not summaries:
            return None
        return max(summaries, key=lambda s: s.version)

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
            id=uuid.uuid4(),
            session_id=session_id,
            version=version,
            covers_through_seq=covers_through_seq,
            content=content,
            provider=provider,
            model=model,
        )
        self.summaries.append(summary)
        return summary


class FakeUsageStore:
    """In-memory usage-event recorder for chat-flow unit tests (no database)."""

    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    async def record(
        self,
        *,
        session_id: uuid.UUID,
        provider: str,
        model: str,
        token_source: str,
        kind: str = "chat",
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        message_id: uuid.UUID | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
        request_id: str | None = None,
    ) -> UsageEvent:
        event = UsageEvent(
            id=uuid.uuid4(),
            session_id=session_id,
            user_id=user_id,
            guest_id=guest_id,
            message_id=message_id,
            kind=kind,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            token_source=token_source,
            latency_ms=latency_ms,
            request_id=request_id,
        )
        self.events.append(event)
        return event
