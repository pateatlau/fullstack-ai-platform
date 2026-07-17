"""Phase 4 integration tests for chat/usage persistence (skips when DB is down).

Exercises the real SQL stores against the compose Postgres to verify sequence
ordering, message persistence, XOR ownership, and usage recording. The
``db_session`` fixture lives in tests/conftest.py.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_token
from app.db.chat import SqlChatStore
from app.db.identity import SqlGuestStore, SqlUserStore
from app.db.usage import SqlUsageStore


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"persist-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    return user.id


@pytest.mark.anyio
async def test_chat_store_sequences_and_orders_messages(db_session) -> None:
    chat_store = SqlChatStore(db_session)
    user_id = await _make_user(db_session)

    chat = await chat_store.create_session(user_id=user_id, title="Session")
    seqs = [await chat_store.allocate_seq(chat.id) for _ in range(3)]
    assert seqs == [1, 2, 3]

    await chat_store.add_message(
        session_id=chat.id, seq=1, role="system", content="rules"
    )
    await chat_store.add_message(session_id=chat.id, seq=2, role="user", content="hi")
    await chat_store.add_message(
        session_id=chat.id,
        seq=3,
        role="assistant",
        content="hello",
        provider="openai",
        model="gpt-4o-mini",
        finish_reason="stop",
    )

    messages = await chat_store.list_messages(chat.id)
    assert [(m.seq, m.role) for m in messages] == [
        (1, "system"),
        (2, "user"),
        (3, "assistant"),
    ]


@pytest.mark.anyio
async def test_chat_store_get_owned_session_filters_by_owner(db_session) -> None:
    chat_store = SqlChatStore(db_session)
    user_id = await _make_user(db_session)
    other_user_id = await _make_user(db_session)

    chat = await chat_store.create_session(user_id=user_id)

    assert await chat_store.get_owned_session(chat.id, user_id=user_id) is not None
    assert await chat_store.get_owned_session(chat.id, user_id=other_user_id) is None


@pytest.mark.anyio
async def test_chat_session_xor_ownership_is_enforced(db_session) -> None:
    user_id = await _make_user(db_session)
    guest = await SqlGuestStore(db_session).create(
        token_hash=hash_token(f"xor-{uuid.uuid4()}")
    )

    # Both owners set violates the XOR CHECK constraint.
    with pytest.raises(IntegrityError):
        await SqlChatStore(db_session).create_session(
            user_id=user_id, guest_id=guest.id
        )


@pytest.mark.anyio
async def test_usage_store_records_event_for_message(db_session) -> None:
    chat_store = SqlChatStore(db_session)
    usage_store = SqlUsageStore(db_session)
    user_id = await _make_user(db_session)

    chat = await chat_store.create_session(user_id=user_id)
    seq = await chat_store.allocate_seq(chat.id)
    message = await chat_store.add_message(
        session_id=chat.id,
        seq=seq,
        role="assistant",
        content="hello",
        provider="openai",
        model="gpt-4o-mini",
    )

    event = await usage_store.record(
        session_id=chat.id,
        user_id=user_id,
        message_id=message.id,
        provider="openai",
        model="gpt-4o-mini",
        token_source="provider_reported",
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
    )

    assert event.id is not None
    assert event.message_id == message.id
    assert event.total_tokens == 18


@pytest.mark.anyio
async def test_guest_link_to_user_is_last_writer_wins(db_session) -> None:
    """Locks the SQL-layer behavior behind the already-linked/different-user
    edge case (plan Section 4.2): ``link_to_user`` is a plain UPDATE, so
    presenting the same guest token to a second, different user overwrites
    ``linked_user_id`` rather than erroring or preserving the first link.
    """
    guest_store = SqlGuestStore(db_session)
    guest = await guest_store.create(token_hash=hash_token(f"relink-{uuid.uuid4()}"))
    first_user_id = await _make_user(db_session)
    second_user_id = await _make_user(db_session)

    await guest_store.link_to_user(guest.id, first_user_id)
    await db_session.flush()
    after_first = await guest_store.get_by_token_hash(guest.token_hash)
    assert after_first is not None
    assert after_first.linked_user_id == first_user_id

    await guest_store.link_to_user(guest.id, second_user_id)
    await db_session.flush()
    after_second = await guest_store.get_by_token_hash(guest.token_hash)
    assert after_second is not None
    assert after_second.linked_user_id == second_user_id
