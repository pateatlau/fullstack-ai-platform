"""Tests for voice session management."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import VoiceAuthError, VoiceSessionError
from app.ai.voice.session import VoiceSessionManager
from app.core.caller import CallerContext
from tests.fakes import FakeChatStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def voice_config() -> VoiceConfig:
    """Short timeouts for session lifecycle tests."""
    return VoiceConfig(
        session_timeout_seconds=60,
        heartbeat_interval_seconds=10,
    )


@pytest.fixture
def chat_store() -> FakeChatStore:
    return FakeChatStore()


@pytest.fixture
def manager(
    voice_config: VoiceConfig, chat_store: FakeChatStore
) -> VoiceSessionManager:
    return VoiceSessionManager(voice_config, chat_store)


async def _create_owned_chat_session(
    chat_store: FakeChatStore,
    user_id: uuid.UUID,
) -> uuid.UUID:
    chat_session = await chat_store.create_session(user_id=user_id, title="Voice test")
    return chat_session.id


async def test_create_attaches_to_owned_chat_session(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)

    voice_session = await manager.create(session_id, caller)

    assert voice_session.is_active is True
    assert voice_session.session_id == session_id
    assert voice_session.user_id == user_id
    assert manager.get(voice_session.voice_session_id) is voice_session
    assert manager.get_active_for_user(user_id) is voice_session


async def test_create_rejects_guest(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    guest = CallerContext.anonymous(guest_id=uuid.uuid4())

    with pytest.raises(VoiceAuthError, match="authenticated"):
        await manager.create(session_id, guest)


async def test_create_rejects_unowned_chat_session(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    owner_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, owner_id)
    caller = CallerContext.for_user(other_user_id)

    with pytest.raises(VoiceSessionError) as exc_info:
        await manager.create(session_id, caller)

    assert exc_info.value.code == "session_not_found"


async def test_create_rejects_missing_chat_session(
    manager: VoiceSessionManager,
) -> None:
    caller = CallerContext.for_user(uuid.uuid4())

    with pytest.raises(VoiceSessionError) as exc_info:
        await manager.create(uuid.uuid4(), caller)

    assert exc_info.value.code == "session_not_found"


async def test_create_rejects_duplicate_active_session(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    first_session_id = await _create_owned_chat_session(chat_store, user_id)
    second_session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)

    await manager.create(first_session_id, caller)

    with pytest.raises(VoiceSessionError) as exc_info:
        await manager.create(second_session_id, caller)

    assert exc_info.value.code == "session_already_active"


async def test_teardown_is_idempotent(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)
    voice_session = await manager.create(session_id, caller)

    assert await manager.teardown(voice_session.voice_session_id) is True
    assert voice_session.is_active is False
    assert voice_session.close_reason == "client_end"
    assert manager.get_active_for_user(user_id) is None

    assert await manager.teardown(voice_session.voice_session_id) is False
    assert await manager.teardown("missing-session") is False


async def test_teardown_cancels_registered_tasks(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)
    voice_session = await manager.create(session_id, caller)

    started = asyncio.Event()

    async def long_running() -> None:
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(long_running())
    await started.wait()
    manager.register_task(voice_session.voice_session_id, task)

    await manager.teardown(voice_session.voice_session_id, reason="client_end")
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled() or task.done()


async def test_teardown_runs_cleanup_callbacks(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)
    voice_session = await manager.create(session_id, caller)
    cleaned_up = False

    async def cleanup() -> None:
        nonlocal cleaned_up
        cleaned_up = True

    manager.register_cleanup(voice_session.voice_session_id, cleanup)
    await manager.teardown(voice_session.voice_session_id)

    assert cleaned_up is True


async def test_record_heartbeat_updates_timestamps(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)
    voice_session = await manager.create(session_id, caller)

    original_heartbeat = voice_session.last_heartbeat_at
    manager.record_heartbeat(voice_session.voice_session_id)

    assert voice_session.last_heartbeat_at >= original_heartbeat
    assert voice_session.last_activity_at >= original_heartbeat


async def test_record_activity_updates_idle_timestamp_only(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)
    voice_session = await manager.create(session_id, caller)

    original_heartbeat = voice_session.last_heartbeat_at
    manager.record_activity(voice_session.voice_session_id)

    assert voice_session.last_heartbeat_at == original_heartbeat
    assert voice_session.last_activity_at >= original_heartbeat


async def test_expire_stale_sessions_on_heartbeat_miss(
    voice_config: VoiceConfig,
    chat_store: FakeChatStore,
) -> None:
    manager = VoiceSessionManager(voice_config, chat_store)
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)
    voice_session = await manager.create(session_id, caller)

    created_at = voice_session.created_at
    stale_at = created_at + timedelta(
        seconds=manager._heartbeat_miss_seconds + 1,
    )

    expired = await manager.expire_stale_sessions(now=stale_at)

    assert expired == [(voice_session.voice_session_id, "heartbeat_timeout")]
    assert voice_session.is_active is False
    assert voice_session.close_reason == "heartbeat_timeout"


async def test_expire_stale_sessions_on_idle_timeout(
    voice_config: VoiceConfig,
    chat_store: FakeChatStore,
) -> None:
    manager = VoiceSessionManager(voice_config, chat_store)
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)
    voice_session = await manager.create(session_id, caller)

    base = datetime.now(timezone.utc)
    stale_at = base + timedelta(seconds=voice_config.session_timeout_seconds + 1)
    voice_session.created_at = base
    voice_session.last_activity_at = base
    # Heartbeat remains fresh so only idle timeout applies.
    voice_session.last_heartbeat_at = stale_at

    expired = await manager.expire_stale_sessions(now=stale_at)

    assert expired == [(voice_session.voice_session_id, "idle_timeout")]
    assert voice_session.is_active is False
    assert voice_session.close_reason == "idle_timeout"


async def test_user_can_create_new_session_after_teardown(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    first_session_id = await _create_owned_chat_session(chat_store, user_id)
    second_session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)

    first_voice = await manager.create(first_session_id, caller)
    await manager.teardown(first_voice.voice_session_id)

    second_voice = await manager.create(second_session_id, caller)

    assert second_voice.is_active is True
    assert second_voice.voice_session_id != first_voice.voice_session_id


async def test_record_heartbeat_requires_active_session(
    manager: VoiceSessionManager,
    chat_store: FakeChatStore,
) -> None:
    user_id = uuid.uuid4()
    session_id = await _create_owned_chat_session(chat_store, user_id)
    caller = CallerContext.for_user(user_id)
    voice_session = await manager.create(session_id, caller)
    await manager.teardown(voice_session.voice_session_id)

    with pytest.raises(VoiceSessionError) as exc_info:
        manager.record_heartbeat(voice_session.voice_session_id)

    assert exc_info.value.code == "voice_session_not_active"
