"""Phase 6 tests: threshold summarization, deterministic assembly, guest linking."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.caller import CallerContext
from app.core.config import Settings
from app.core.security import hash_token
from app.main import app
from app.providers.base import LLMProvider
from app.providers.factory import ProviderFactory
from app.routers.auth import get_google_verifier, get_guest_store, get_user_store
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.services.auth_service import AuthService, GoogleClaims
from app.services.chat_service import ChatService
from app.services.quota_service import QuotaService
from tests.fakes import (
    FakeChatStore,
    FakeGoogleVerifier,
    FakeGuestQuotaStore,
    FakeGuestStore,
    FakeProvider,
    FakeUsageStore,
    FakeUserStore,
)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: LLMProvider) -> None:
    def get_provider(
        name: str | None = None, settings: Settings | None = None
    ) -> LLMProvider:
        return provider

    monkeypatch.setattr(ProviderFactory, "get_provider", staticmethod(get_provider))


# --------------------------------------------------------------------------- #
# Summarization (unit, fakes)                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_summary_created_when_threshold_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("A concise summary."))
    settings = Settings(
        chat_persistence_enabled=True,
        llm_provider="openai",
        summary_trigger_message_count=2,
    )
    chat_store = FakeChatStore()
    usage_store = FakeUsageStore()
    service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=usage_store,
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
    )
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(
        ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="hi")]),
        caller,
    )

    # One user + one assistant message = 2 pending >= threshold -> summarized.
    assert result.session_id is not None
    summary = await chat_store.get_latest_summary(result.session_id)
    assert summary is not None
    assert summary.version == 1
    assert summary.covers_through_seq == 2
    assert summary.content == "A concise summary."
    assert any(event.kind == "summary" for event in usage_store.events)


@pytest.mark.anyio
async def test_no_summary_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(
        chat_persistence_enabled=True,
        llm_provider="openai",
        summary_trigger_message_count=50,
    )
    chat_store = FakeChatStore()
    service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
    )
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(
        ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="hi")]),
        caller,
    )

    assert result.session_id is not None
    assert await chat_store.get_latest_summary(result.session_id) is None


@pytest.mark.anyio
async def test_build_context_messages_is_deterministic() -> None:
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
    )
    session = await chat_store.create_session(user_id=uuid.uuid4())
    seed = [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
    for seq, (role, content) in enumerate(seed, start=1):
        await chat_store.add_message(
            session_id=session.id, seq=seq, role=role, content=content
        )
    await chat_store.add_summary(
        session_id=session.id,
        version=1,
        covers_through_seq=2,
        content="Earlier: greeting.",
        provider="openai",
        model="gpt-4o-mini",
    )

    context = await service.build_context_messages(session.id)

    # Latest summary as a leading system message, then only messages seq > 2.
    assert context[0].role == "system"
    assert "Earlier: greeting." in context[0].content
    assert [(m.role, m.content) for m in context[1:]] == [
        ("user", "q2"),
        ("assistant", "a2"),
    ]


@pytest.mark.anyio
async def test_summary_persisted_against_real_db(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.chat import SqlChatStore
    from app.db.identity import SqlGuestQuotaStore, SqlUserStore
    from app.db.usage import SqlUsageStore

    _patch_provider(monkeypatch, FakeProvider("DB summary."))
    settings = Settings(
        chat_persistence_enabled=True,
        llm_provider="openai",
        summary_trigger_message_count=2,
    )
    user = await SqlUserStore(db_session).create(
        sub=f"sum-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    chat_store = SqlChatStore(db_session)
    service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=SqlUsageStore(db_session),
        quota_service=QuotaService(
            store=SqlGuestQuotaStore(db_session), settings=settings
        ),
        session=db_session,
    )

    result = await service.complete_chat(
        ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="hi")]),
        CallerContext.for_user(user.id),
    )

    assert result.session_id is not None
    summary = await chat_store.get_latest_summary(result.session_id)
    assert summary is not None
    assert summary.covers_through_seq == 2
    assert summary.content == "DB summary."


# --------------------------------------------------------------------------- #
# Guest -> user linking on login (plan Sections 5.8, 7)                        #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_login_links_presenting_guest_without_migration() -> None:
    settings = Settings()
    verifier = FakeGoogleVerifier(
        claims=GoogleClaims(sub="sub-1", email=None, name=None, picture=None)
    )
    user_store = FakeUserStore()
    guest_store = FakeGuestStore()
    guest = await guest_store.create(token_hash=hash_token("guest-token"))
    service = AuthService(
        verifier=verifier,
        store=user_store,
        settings=settings,
        guest_store=guest_store,
    )

    result = await service.login_with_google("fake", guest_token="guest-token")

    assert result.linked_guest_id == guest.id
    assert guest_store.linked == [(guest.id, result.user.id)]


@pytest.mark.anyio
async def test_login_without_guest_token_does_not_link() -> None:
    settings = Settings()
    verifier = FakeGoogleVerifier(
        claims=GoogleClaims(sub="sub-2", email=None, name=None, picture=None)
    )
    guest_store = FakeGuestStore()
    service = AuthService(
        verifier=verifier,
        store=FakeUserStore(),
        settings=settings,
        guest_store=guest_store,
    )

    result = await service.login_with_google("fake")

    assert result.linked_guest_id is None
    assert guest_store.linked == []


@pytest.mark.anyio
async def test_auth_endpoint_links_guest_from_header() -> None:
    verifier = FakeGoogleVerifier(
        claims=GoogleClaims(sub="sub-3", email=None, name=None, picture=None)
    )
    user_store = FakeUserStore()
    guest_store = FakeGuestStore()
    guest = await guest_store.create(token_hash=hash_token("hdr-token"))

    app.dependency_overrides[get_google_verifier] = lambda: verifier
    app.dependency_overrides[get_user_store] = lambda: user_store
    app.dependency_overrides[get_guest_store] = lambda: guest_store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/auth/google",
                json={"id_token": "x"},
                headers={"X-Guest-Token": "hdr-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(guest_store.linked) == 1
    assert guest_store.linked[0][0] == guest.id


@pytest.mark.anyio
async def test_login_relinks_already_linked_guest_to_different_user() -> None:
    """Locks the intended edge-case behavior (plan Section 4.2, [Verify] resolved):

    presenting the same guest token at a second login by a *different* user
    re-links the guest to that user (last-writer-wins) rather than erroring or
    leaving the original link untouched. Linking is fail-soft and idempotent
    but always reflects the most recently presenting user.
    """
    guest_store = FakeGuestStore()
    guest = await guest_store.create(token_hash=hash_token("shared-token"))

    verifier_1 = FakeGoogleVerifier(
        claims=GoogleClaims(sub="sub-first", email=None, name=None, picture=None)
    )
    service_1 = AuthService(
        verifier=verifier_1,
        store=FakeUserStore(),
        settings=Settings(),
        guest_store=guest_store,
    )
    result_1 = await service_1.login_with_google("fake", guest_token="shared-token")

    verifier_2 = FakeGoogleVerifier(
        claims=GoogleClaims(sub="sub-second", email=None, name=None, picture=None)
    )
    service_2 = AuthService(
        verifier=verifier_2,
        store=FakeUserStore(),
        settings=Settings(),
        guest_store=guest_store,
    )
    result_2 = await service_2.login_with_google("fake", guest_token="shared-token")

    assert result_1.user.id != result_2.user.id
    assert result_1.linked_guest_id == guest.id
    assert result_2.linked_guest_id == guest.id
    # Both logins linked the same guest; the most recent call is last, and a
    # real UPDATE (SqlGuestStore.link_to_user) makes it authoritative.
    assert guest_store.linked == [
        (guest.id, result_1.user.id),
        (guest.id, result_2.user.id),
    ]
