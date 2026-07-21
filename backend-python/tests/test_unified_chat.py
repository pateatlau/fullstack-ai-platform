"""Unified chat integration tests (Phase 3 / V1.1b)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import text

from app.ai.deps import (
    get_knowledge_service,
    get_rag_service,
    get_retriever,
    get_tool_registry,
)
from app.ai.documents.pipeline import IngestionPipeline
from app.ai.prompts.manager import create_prompt_manager
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.prompt_builder import PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.service import RAGService
from app.ai.tools.implementations.web_search import (
    WEB_SEARCH_TOOL_NAME,
    WebSearchResult,
)
from app.ai.tools.registration import register_production_tools
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.db.identity import SqlUserStore
from app.main import app
from app.providers.base import (
    ChatMessageSchema,
    ProviderCompletion,
    ProviderToolCall,
    ProviderToolCompletion,
)
from app.ai.interfaces.vector_store import ScoredChunk
from app.providers.capabilities import ProviderCapabilities, get_capabilities
from app.providers.factory import ProviderFactory
from app.routers.chat import get_optional_caller
from app.services.knowledge_service import KnowledgeService
from app.services.tool_chat_service import _GUEST_TOOL_DENIED_MESSAGE
from tests.fakes import FakeChatStore, FakeGuestQuotaStore, FakeProvider, FakeUsageStore

FIXTURES = Path(__file__).resolve().parent / "data" / "documents"
DIMENSIONS = 1536


class _FakeEmbeddingProvider:
    dimensions = DIMENSIONS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index % DIMENSIONS), 0.0] + [0.0] * (DIMENSIONS - 2)
            for index, _ in enumerate(texts)
        ]


class _CapturingLLMProvider(FakeProvider):
    captured_messages: list[list[ChatMessageSchema]] = []

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> ProviderCompletion:
        del model, temperature
        self.captured_messages.append(list(messages))
        return ProviderCompletion(
            content="Grounded answer references Plain text fixture content.",
            finish_reason="stop",
        )


class _FakeSearchClient:
    async def search(self, query: str, *, max_results: int) -> list[WebSearchResult]:
        del query, max_results
        return [
            WebSearchResult(
                title="Example",
                url="https://example.com",
                snippet="Example snippet",
            )
        ]


@pytest.fixture(autouse=True)
def _clear_settings_and_registry() -> Iterator[None]:
    get_settings.cache_clear()
    get_tool_registry.cache_clear()
    yield
    get_settings.cache_clear()
    get_tool_registry.cache_clear()
    app.dependency_overrides.clear()


async def _pgvector_available(session) -> bool:
    try:
        result = await session.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result == 1
    except Exception:
        return False


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"unified-chat-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


def _mock_provider_factory(provider: FakeProvider):
    return staticmethod(lambda name=None, settings=None: provider)


def _knowledge_service(session) -> KnowledgeService:
    settings = Settings(openai_api_key="test-key", rag_enabled=True)
    pipeline = IngestionPipeline(settings, embedding_provider=_FakeEmbeddingProvider())
    vector_store = PgVectorStore(session, settings)
    return KnowledgeService(
        session=session,
        settings=settings,
        pipeline=pipeline,
        vector_store=vector_store,
    )


def _rag_service(session, llm_provider: FakeProvider) -> RAGService:
    settings = Settings(openai_api_key="test-key", rag_enabled=True)
    retriever = Retriever(
        embedding_provider=_FakeEmbeddingProvider(),
        vector_store=PgVectorStore(session, settings),
        settings=settings,
    )
    return RAGService(
        retriever=retriever,
        context_builder=ContextBuilder(settings),
        prompt_builder=PromptBuilder(
            prompt_manager=create_prompt_manager(),
            settings=settings,
        ),
        settings=settings,
    )


def _register_web_search_tools(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()
    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )


@pytest.fixture
async def pgvector_session(db_session):
    if not await _pgvector_available(db_session):
        pytest.skip("pgvector extension not available — run alembic upgrade head")
    yield db_session


@pytest.fixture
def unified_api_dependencies(pgvector_session, monkeypatch: pytest.MonkeyPatch):
    llm = _CapturingLLMProvider()
    _CapturingLLMProvider.captured_messages.clear()
    monkeypatch.setenv("RAG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(llm),
    )

    def _override_knowledge_service() -> KnowledgeService:
        return _knowledge_service(pgvector_session)

    def _override_rag_service() -> RAGService:
        return _rag_service(pgvector_session, llm)

    app.dependency_overrides[get_knowledge_service] = _override_knowledge_service
    app.dependency_overrides[get_rag_service] = _override_rag_service

    def _override_retriever() -> Retriever:
        settings = Settings(openai_api_key="test-key", rag_enabled=True)
        return Retriever(
            embedding_provider=_FakeEmbeddingProvider(),
            vector_store=PgVectorStore(pgvector_session, settings),
            settings=settings,
        )

    app.dependency_overrides[get_retriever] = _override_retriever
    yield llm
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_chat_use_documents_returns_grounded_answer(
    pgvector_session,
    unified_api_dependencies,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(user_id)

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        upload = await client.post(
            "/api/documents/upload",
            files={
                "file": (
                    "sample.txt",
                    (FIXTURES / "sample.txt").read_bytes(),
                    "text/plain",
                )
            },
            headers=headers,
        )
        assert upload.status_code == 200

        response = await client.post(
            "/api/chat",
            json={
                "messages": [
                    {"role": "user", "content": "What does the plain text fixture say?"}
                ],
                "use_documents": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert "Plain text fixture content" in body["content"]
    assert body["retrieved_chunks"]
    assert unified_api_dependencies.tool_completion_calls == 0


@pytest.mark.anyio
async def test_chat_use_documents_ndjson_reports_retrieval_activity(
    pgvector_session,
    unified_api_dependencies,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(user_id)

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        upload = await client.post(
            "/api/documents/upload",
            files={
                "file": (
                    "sample.txt",
                    (FIXTURES / "sample.txt").read_bytes(),
                    "text/plain",
                )
            },
            headers=headers,
        )
        assert upload.status_code == 200

        response = await client.post(
            "/api/chat",
            headers={"Accept": "application/x-ndjson", **headers},
            json={
                "messages": [
                    {"role": "user", "content": "What does the plain text fixture say?"}
                ],
                "use_documents": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [line for line in response.text.strip().split("\n") if line]
    assert any('"phase": "document_retrieval"' in line for line in lines)
    assert any('"type": "complete"' in line for line in lines)


@pytest.mark.anyio
async def test_chat_use_web_search_invokes_tool(
    monkeypatch: MonkeyPatch,
) -> None:
    _register_web_search_tools(monkeypatch)

    fake_provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name=WEB_SEARCH_TOOL_NAME,
                        arguments={"query": "news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="Grounded answer from Example — https://example.com",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Search for news"}],
                "use_web_search": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "Grounded answer" in body["content"]
    assert body["tools_used"] == [WEB_SEARCH_TOOL_NAME]
    assert fake_provider.tool_completion_calls == 2


@pytest.mark.anyio
async def test_chat_both_toggles_together(
    pgvector_session,
    unified_api_dependencies,
    monkeypatch: MonkeyPatch,
) -> None:
    _register_web_search_tools(monkeypatch)
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)

    tool_provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-both",
                        name=WEB_SEARCH_TOOL_NAME,
                        arguments={"query": "fixture"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="Combined answer with fixture and web results.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )

    class _RoutingProvider(FakeProvider):
        async def complete_chat_with_tools(
            self, messages, model, tools, temperature=0.7
        ):
            return await tool_provider.complete_chat_with_tools(
                messages, model, tools, temperature
            )

        async def complete_chat(self, messages, model, temperature=0.7):
            return await unified_api_dependencies.complete_chat(
                messages, model, temperature
            )

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(_RoutingProvider()),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(user_id)

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        upload = await client.post(
            "/api/documents/upload",
            files={
                "file": (
                    "sample.txt",
                    (FIXTURES / "sample.txt").read_bytes(),
                    "text/plain",
                )
            },
            headers=headers,
        )
        assert upload.status_code == 200

        response = await client.post(
            "/api/chat",
            json={
                "messages": [
                    {"role": "user", "content": "Search and use my documents"}
                ],
                "use_documents": True,
                "use_web_search": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["retrieved_chunks"]
    assert body["tools_used"] == [WEB_SEARCH_TOOL_NAME]
    assert tool_provider.tool_completion_calls >= 1


@pytest.mark.anyio
async def test_chat_guest_toggles_rejected(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    monkeypatch.setenv("RAG_ENABLED", "true")
    get_settings.cache_clear()
    fake_provider = FakeProvider("Should not run")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        web = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Search"}],
                "use_web_search": True,
            },
        )
        docs = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Docs"}],
                "use_documents": True,
            },
        )

    assert web.status_code == 200
    assert web.json()["content"] == _GUEST_TOOL_DENIED_MESSAGE
    assert docs.status_code == 200
    assert docs.json()["content"] == _GUEST_TOOL_DENIED_MESSAGE
    assert fake_provider.tool_completion_calls == 0


@pytest.mark.anyio
async def test_chat_toggles_off_plain_chat_unchanged(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()
    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )

    fake_provider = FakeProvider("Plain chat without toggles")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    assert response.json()["content"] == "Plain chat without toggles"
    assert fake_provider.tool_completion_calls == 0
    assert response.json().get("tools_used") is None


@pytest.mark.anyio
async def test_chat_flags_off_toggles_ignored(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "false")
    monkeypatch.setenv("RAG_ENABLED", "false")
    get_settings.cache_clear()

    fake_provider = FakeProvider("Flags off plain chat")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "use_web_search": True,
                "use_documents": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    assert response.json()["content"] == "Flags off plain chat"
    assert fake_provider.tool_completion_calls == 0


@pytest.mark.anyio
async def test_chat_guest_toggles_ignored_when_flags_off(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "false")
    monkeypatch.setenv("RAG_ENABLED", "false")
    get_settings.cache_clear()

    fake_provider = FakeProvider("Guest plain chat when flags off")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "use_web_search": True,
                "use_documents": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    assert response.json()["content"] == "Guest plain chat when flags off"
    assert response.json()["content"] != _GUEST_TOOL_DENIED_MESSAGE
    assert fake_provider.tool_completion_calls == 0


@pytest.mark.anyio
async def test_chat_use_web_search_unsupported_provider(
    monkeypatch: MonkeyPatch,
) -> None:
    _register_web_search_tools(monkeypatch)

    fake_provider = FakeProvider("Should not be reached")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    original_get_capabilities = get_capabilities

    def _unsupported_openai(name: str) -> ProviderCapabilities:
        caps = original_get_capabilities(name)  # type: ignore[arg-type]
        if name == "openai":
            return ProviderCapabilities(
                supports_streaming=caps.supports_streaming,
                supports_tool_calling=False,
                supports_json_mode=caps.supports_json_mode,
                supports_reasoning=caps.supports_reasoning,
                supports_image_input=caps.supports_image_input,
                supports_image_output=caps.supports_image_output,
                supports_audio=caps.supports_audio,
                supports_embeddings=caps.supports_embeddings,
            )
        return caps

    monkeypatch.setattr(
        "app.services.unified_chat_service.get_capabilities",
        _unsupported_openai,
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Search"}],
                "use_web_search": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.anyio
async def test_unified_chat_persists_session_messages_with_documents(
    monkeypatch: MonkeyPatch,
) -> None:
    from app.ai.prompts.manager import create_prompt_manager
    from app.services.chat_service import ChatService
    from app.services.quota_service import QuotaService
    from app.services.tool_chat_service import ToolChatService
    from app.services.unified_chat_service import UnifiedChatService
    from app.ai.tools.registry import ToolRegistry
    from app.ai.tools.executor import ToolExecutor
    from app.schemas.chat import ChatMessageSchema, ChatRequestSchema

    provider = _CapturingLLMProvider()
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(provider),
    )

    class _FakeRetriever:
        async def retrieve(
            self, *, question: str, user_id: uuid.UUID, top_k: int | None
        ):
            del question, top_k
            return [
                ScoredChunk(
                    chunk_id=uuid.uuid4(),
                    document_id=uuid.uuid4(),
                    chunk_index=0,
                    content="Plain text fixture content.",
                    metadata={"source": "sample.txt"},
                    score=0.95,
                )
            ]

    settings = Settings(
        chat_persistence_enabled=True,
        openai_api_key="test-key",
        rag_enabled=True,
    )
    chat_store = FakeChatStore()
    chat_service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
        prompt_manager=create_prompt_manager(),
    )
    tool_service = ToolChatService(
        chat_service=chat_service,
        tool_executor=ToolExecutor(registry=ToolRegistry(), settings=settings),
        tool_registry=ToolRegistry(),
        prompt_manager=create_prompt_manager(),
        settings=settings,
    )
    unified = UnifiedChatService(
        chat_service=chat_service,
        tool_chat_service=tool_service,
        retriever=cast(Retriever, _FakeRetriever()),
        context_builder=ContextBuilder(settings),
        prompt_manager=create_prompt_manager(),
        settings=settings,
    )
    caller = CallerContext.for_user(uuid.uuid4())

    response = await unified.execute(
        ChatRequestSchema(
            messages=[
                ChatMessageSchema(role="user", content="What is in my documents?")
            ],
            use_documents=True,
            provider="openai",
            model="gpt-4o-mini",
        ),
        caller,
    )

    assert response.session_id is not None
    messages = await chat_store.list_messages(response.session_id)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "What is in my documents?"
    assert "Plain text fixture content" in messages[1].content
