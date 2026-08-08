"""Voice session manager — lifecycle, chat attach, heartbeat."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from opentelemetry.trace import Span

from app.ai.observability.tracing.spans import (
    begin_voice_session_span,
    end_voice_session_span,
)
from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import VoiceAuthError, VoiceSessionError
from app.core.caller import CallerContext
from app.db.models import ChatSession


class ChatSessionStore(Protocol):
    """Minimal chat store surface for voice session attach."""

    async def get_owned_session(
        self,
        session_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
    ) -> ChatSession | None: ...


CleanupCallback = Callable[[], Awaitable[None]]


@dataclass
class ManagedVoiceSession:
    """Concrete voice session handle managed by :class:`VoiceSessionManager`."""

    voice_session_id: str
    session_id: uuid.UUID
    user_id: uuid.UUID
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_activity_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    close_reason: str | None = None
    _observability_span: Span | None = field(default=None, repr=False, compare=False)
    _observability_started_at: float = field(default=0.0, repr=False, compare=False)
    _cleanup_callbacks: list[CleanupCallback] = field(default_factory=list)
    _tasks: list[asyncio.Task[object]] = field(default_factory=list)

    @property
    def chat_session_id(self) -> str:
        """Associated chat session identifier as a string."""
        return str(self.session_id)


class VoiceSessionManager:
    """Create/attach/teardown voice sessions; heartbeat; timeout."""

    def __init__(
        self,
        config: VoiceConfig,
        chat_store: ChatSessionStore,
        *,
        heartbeat_miss_multiplier: int = 3,
    ) -> None:
        """Initialize voice session manager.

        Args:
            config: Voice configuration (timeouts, heartbeat interval).
            chat_store: Chat persistence store for session ownership checks.
            heartbeat_miss_multiplier: Heartbeat miss threshold as a multiple of
                ``heartbeat_interval_seconds`` (default 3 → 90 s at 30 s interval).
        """
        self._config = config
        self._chat_store = chat_store
        self._heartbeat_miss_seconds = (
            config.heartbeat_interval_seconds * heartbeat_miss_multiplier
        )
        self._sessions: dict[str, ManagedVoiceSession] = {}
        self._active_by_user: dict[uuid.UUID, str] = {}

    async def create(
        self,
        session_id: uuid.UUID | str,
        caller: CallerContext,
    ) -> ManagedVoiceSession:
        """Create a voice session attached to an owned chat session.

        Args:
            session_id: Chat session identifier to attach to.
            caller: Authenticated caller context.

        Returns:
            Active managed voice session.

        Raises:
            VoiceAuthError: When caller is a guest.
            VoiceSessionError: When chat session is missing, not owned, or the
                user already has an active voice session.
        """
        if not caller.is_authenticated or caller.user_id is None:
            raise VoiceAuthError("Voice sessions require an authenticated user")

        parsed_session_id = (
            session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(session_id)
        )
        chat_session = await self._chat_store.get_owned_session(
            parsed_session_id,
            user_id=caller.user_id,
        )
        if chat_session is None:
            raise VoiceSessionError(
                "Chat session not found or not owned by caller",
                code="session_not_found",
            )

        existing_voice_session_id = self._active_by_user.get(caller.user_id)
        if existing_voice_session_id is not None:
            existing = self._sessions.get(existing_voice_session_id)
            if existing is not None and existing.is_active:
                raise VoiceSessionError(
                    "User already has an active voice session",
                    code="session_already_active",
                )
            self._active_by_user.pop(caller.user_id, None)

        now = datetime.now(timezone.utc)
        voice_session_id = str(uuid.uuid4())
        managed = ManagedVoiceSession(
            voice_session_id=voice_session_id,
            session_id=chat_session.id,
            user_id=caller.user_id,
            created_at=now,
            last_heartbeat_at=now,
            last_activity_at=now,
        )
        span, started_at = begin_voice_session_span()
        managed._observability_span = span
        managed._observability_started_at = started_at
        self._sessions[voice_session_id] = managed
        self._active_by_user[caller.user_id] = voice_session_id
        return managed

    def get(self, voice_session_id: str) -> ManagedVoiceSession | None:
        """Look up a voice session by identifier."""
        return self._sessions.get(voice_session_id)

    def get_active_for_user(self, user_id: uuid.UUID) -> ManagedVoiceSession | None:
        """Return the active voice session for a user, if any."""
        voice_session_id = self._active_by_user.get(user_id)
        if voice_session_id is None:
            return None
        session = self._sessions.get(voice_session_id)
        if session is None or not session.is_active:
            return None
        return session

    def record_heartbeat(self, voice_session_id: str) -> None:
        """Record a client heartbeat for the session."""
        session = self._require_active_session(voice_session_id)
        now = datetime.now(timezone.utc)
        session.last_heartbeat_at = now
        session.last_activity_at = now

    def record_activity(self, voice_session_id: str) -> None:
        """Record non-heartbeat activity (e.g. inbound audio) for idle tracking."""
        session = self._require_active_session(voice_session_id)
        session.last_activity_at = datetime.now(timezone.utc)

    def register_cleanup(
        self,
        voice_session_id: str,
        callback: CleanupCallback,
    ) -> None:
        """Register an async cleanup hook invoked on teardown."""
        session = self._require_active_session(voice_session_id)
        session._cleanup_callbacks.append(callback)

    def register_task(
        self,
        voice_session_id: str,
        task: asyncio.Task[object],
    ) -> None:
        """Register an asyncio task cancelled on teardown."""
        session = self._require_active_session(voice_session_id)
        session._tasks.append(task)

    async def expire_stale_sessions(
        self,
        *,
        now: datetime | None = None,
    ) -> list[tuple[str, str]]:
        """Teardown sessions that exceeded heartbeat or idle timeouts.

        Returns:
            List of ``(voice_session_id, reason)`` pairs for sessions closed.
        """
        current = now or datetime.now(timezone.utc)
        expired: list[tuple[str, str]] = []

        for voice_session_id, session in list(self._sessions.items()):
            if not session.is_active:
                continue

            heartbeat_elapsed = (current - session.last_heartbeat_at).total_seconds()
            if heartbeat_elapsed > self._heartbeat_miss_seconds:
                await self.teardown(voice_session_id, reason="heartbeat_timeout")
                expired.append((voice_session_id, "heartbeat_timeout"))
                continue

            idle_elapsed = (current - session.last_activity_at).total_seconds()
            if idle_elapsed > self._config.session_timeout_seconds:
                await self.teardown(voice_session_id, reason="idle_timeout")
                expired.append((voice_session_id, "idle_timeout"))

        return expired

    async def teardown(
        self,
        voice_session_id: str,
        *,
        reason: str = "client_end",
    ) -> bool:
        """Idempotent teardown; cancel tasks and run cleanup hooks.

        Args:
            voice_session_id: Voice session identifier.
            reason: Close reason for observability and ``session_closed`` WS frame.

        Returns:
            ``True`` when an active session was torn down; ``False`` if already
            inactive or unknown.
        """
        session = self._sessions.get(voice_session_id)
        if session is None or not session.is_active:
            return False

        session.is_active = False
        session.close_reason = reason

        end_voice_session_span(
            session._observability_span,
            start=session._observability_started_at,
            status=reason,
        )
        session._observability_span = None

        active_id = self._active_by_user.get(session.user_id)
        if active_id == voice_session_id:
            self._active_by_user.pop(session.user_id, None)

        for task in session._tasks:
            if not task.done():
                task.cancel()

        for callback in session._cleanup_callbacks:
            try:
                await callback()
            except Exception:
                # Cleanup hooks are best-effort; teardown must remain idempotent.
                pass

        session._tasks.clear()
        session._cleanup_callbacks.clear()
        return True

    def _require_active_session(self, voice_session_id: str) -> ManagedVoiceSession:
        session = self._sessions.get(voice_session_id)
        if session is None:
            raise VoiceSessionError(
                f"Voice session not found: {voice_session_id}",
                code="voice_session_not_found",
            )
        if not session.is_active:
            raise VoiceSessionError(
                f"Voice session is not active: {voice_session_id}",
                code="voice_session_not_active",
            )
        return session
