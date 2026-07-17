"""Phase 3 tests: guest identity issuance/continuity and guest quota.

Unit tests use in-memory fakes (no DB). Integration tests exercise the real
SQL stores against the compose Postgres and skip automatically when the database
is unavailable (CI Postgres wiring lands in Phase 6).
"""

import datetime
import uuid

import pytest
from starlette.requests import Request

from app.core.caller import GUEST_TOKEN_HEADER, resolve_guest_caller
from app.core.config import Settings
from app.core.security import hash_token
from app.db.identity import SqlGuestQuotaStore, SqlGuestStore, SqlUserStore
from app.services.chat_service import ChatServiceError
from app.services.quota_service import QuotaExceededError, QuotaService
from tests.fakes import FakeGuestQuotaStore, FakeGuestStore


def _request(headers: dict[str, str], client_host: str | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    if client_host is not None:
        scope["client"] = (client_host, 12345)
    return Request(scope)


# --------------------------------------------------------------------------- #
# Guest resolution (unit)                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_resolve_guest_issues_new_identity_when_no_token() -> None:
    store = FakeGuestStore()

    caller = await resolve_guest_caller(_request({}, client_host="203.0.113.7"), store)

    assert caller.kind == "guest"
    assert caller.guest_id is not None
    assert caller.issued_guest_token is not None
    # Exactly one guest created; the server stored only the token hash.
    assert len(store.guests) == 1
    assert store.guests[0].id == caller.guest_id
    assert store.guests[0].token_hash == hash_token(caller.issued_guest_token)
    assert store.guests[0].created_ip_hash is not None


@pytest.mark.anyio
async def test_resolve_guest_resolves_existing_identity_from_token() -> None:
    store = FakeGuestStore()
    raw_token = "existing-guest-token"
    guest = await store.create(token_hash=hash_token(raw_token))

    caller = await resolve_guest_caller(
        _request({GUEST_TOKEN_HEADER: raw_token}), store
    )

    assert caller.guest_id == guest.id
    assert caller.issued_guest_token is None  # continuity: no new token minted
    assert len(store.guests) == 1  # no duplicate identity
    assert store.touched == [guest.id]


@pytest.mark.anyio
async def test_resolve_guest_issues_new_identity_for_unknown_token() -> None:
    store = FakeGuestStore()

    caller = await resolve_guest_caller(
        _request({GUEST_TOKEN_HEADER: "unrecognized-token"}), store
    )

    assert caller.issued_guest_token is not None
    assert len(store.guests) == 1
    assert store.guests[0].id == caller.guest_id


# --------------------------------------------------------------------------- #
# Guest quota (unit)                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_quota_allows_calls_below_limit() -> None:
    settings = Settings(guest_daily_message_quota=3)
    service = QuotaService(store=FakeGuestQuotaStore(), settings=settings)

    # No exception below the limit.
    await service.check(uuid.uuid4())


@pytest.mark.anyio
async def test_quota_denies_and_records_increment() -> None:
    settings = Settings(guest_daily_message_quota=2)
    store = FakeGuestQuotaStore()
    service = QuotaService(store=store, settings=settings)
    guest_id = uuid.uuid4()

    await service.check(guest_id)  # 0 < 2 -> ok
    await service.record(guest_id)
    await service.record(guest_id)  # count now 2 == limit

    with pytest.raises(QuotaExceededError):
        await service.check(guest_id)


def test_quota_exceeded_is_first_class_429() -> None:
    error = QuotaExceededError()
    assert isinstance(error, ChatServiceError)  # flows through the error envelope
    assert error.status_code == 429
    assert error.code == "quota_exceeded"


# --------------------------------------------------------------------------- #
# Integration against real Postgres (skips when unavailable)                   #
# --------------------------------------------------------------------------- #
# The ``db_session`` fixture is provided by tests/conftest.py.


@pytest.mark.anyio
async def test_sql_guest_store_roundtrip_and_linking(db_session) -> None:
    guest_store = SqlGuestStore(db_session)
    user_store = SqlUserStore(db_session)
    raw_token = f"int-token-{uuid.uuid4()}"

    guest = await guest_store.create(
        token_hash=hash_token(raw_token), created_ip_hash="hashed-ip"
    )
    fetched = await guest_store.get_by_token_hash(hash_token(raw_token))
    assert fetched is not None and fetched.id == guest.id

    await guest_store.touch(guest.id)

    user = await user_store.create(
        sub=f"guest-link-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    await guest_store.link_to_user(guest.id, user.id)
    await db_session.flush()

    relinked = await guest_store.get_by_token_hash(hash_token(raw_token))
    assert relinked is not None and relinked.linked_user_id == user.id


@pytest.mark.anyio
async def test_sql_quota_counter_upsert_is_atomic(db_session) -> None:
    guest_store = SqlGuestStore(db_session)
    quota_store = SqlGuestQuotaStore(db_session)
    guest = await guest_store.create(token_hash=hash_token(f"quota-{uuid.uuid4()}"))
    window = datetime.datetime.now(datetime.timezone.utc).date()

    assert await quota_store.get_message_count(guest.id, window) == 0
    await quota_store.increment(guest.id, window, tokens=5)
    await quota_store.increment(guest.id, window, tokens=7)

    assert await quota_store.get_message_count(guest.id, window) == 2


@pytest.mark.anyio
async def test_resolve_guest_persists_identity_against_real_db(db_session) -> None:
    store = SqlGuestStore(db_session)

    caller = await resolve_guest_caller(_request({}), store)
    await db_session.flush()

    assert caller.guest_id is not None
    assert caller.issued_guest_token is not None
    persisted = await store.get_by_token_hash(hash_token(caller.issued_guest_token))
    assert persisted is not None and persisted.id == caller.guest_id
