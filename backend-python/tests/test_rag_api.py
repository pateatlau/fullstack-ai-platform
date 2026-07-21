"""Generic RAG API integration tests (Phase 11)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.ai.deps import get_knowledge_service, get_rag_service
from app.ai.documents.pipeline import IngestionPipeline
from app.ai.prompts.manager import create_prompt_manager
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.prompt_builder import PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.service import EMPTY_CORPUS_MESSAGE, RAGService
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.db.identity import SqlUserStore
from app.main import app
from app.providers.base import ProviderCompletion
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema
from app.services.knowledge_service import KnowledgeService
from tests.fakes import FakeProvider

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
    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> ProviderCompletion:
        del model, temperature
        return ProviderCompletion(
            content="Answer references fixture content.",
            finish_reason="stop",
        )


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
        sub=f"rag-api-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


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


def _mock_provider_factory(provider: FakeProvider):
    def get_provider(
        name: str | None = None, settings: Settings | None = None
    ) -> FakeProvider:
        del name, settings
        return provider

    return staticmethod(get_provider)


def _rag_service(session, llm_provider: _CapturingLLMProvider) -> RAGService:
    settings = Settings(
        openai_api_key="test-key",
        gemini_api_key="test-gemini-key",
        groq_api_key="test-groq-key",
        anthropic_api_key="test-anthropic-key",
        rag_enabled=True,
    )
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


@pytest.fixture
async def pgvector_session(db_session):
    if not await _pgvector_available(db_session):
        pytest.skip("pgvector extension not available — run alembic upgrade head")
    yield db_session


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def rag_api_dependencies(pgvector_session, monkeypatch: pytest.MonkeyPatch):
    llm = _CapturingLLMProvider()
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
    yield llm
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_rag_api_ask_after_ingest(
    pgvector_session,
    rag_api_dependencies,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)

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
            "/api/rag/ask",
            json={"question": "What does the plain text fixture say?"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Answer references fixture content."
    assert body["retrieved_chunks"]
    assert "content" not in body["retrieved_chunks"][0]
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o-mini"


@pytest.mark.anyio
async def test_rag_api_guest_ask_returns_401() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/rag/ask",
            json={"question": "Hello?"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_rag_api_disabled_returns_503(
    pgvector_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)
    monkeypatch.setenv("RAG_ENABLED", "false")
    get_settings.cache_clear()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/rag/ask",
            json={"question": "Anything?"},
            headers=headers,
        )

    get_settings.cache_clear()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "feature_disabled"


@pytest.mark.anyio
async def test_rag_api_empty_corpus_returns_graceful_answer(
    pgvector_session,
    rag_api_dependencies,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/rag/ask",
            json={"question": "Anything at all?"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == EMPTY_CORPUS_MESSAGE
    assert body["retrieved_chunks"] == []


@pytest.mark.anyio
async def test_rag_api_error_envelope_includes_request_id(
    pgvector_session,
    rag_api_dependencies,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/rag/ask",
            json={"question": "   "},
            headers=headers,
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"] is not None
    assert response.headers.get("X-Request-ID") == body["error"]["request_id"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider",
    ["openai", "gemini", "groq", "anthropic"],
)
async def test_rag_api_ask_with_provider_override(
    pgvector_session,
    rag_api_dependencies,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)
    llm = rag_api_dependencies
    resolved_providers: list[str | None] = []

    def tracking_get_provider(
        name: str | None = None, settings: Settings | None = None
    ) -> _CapturingLLMProvider:
        del settings
        resolved_providers.append(name)
        return llm

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(tracking_get_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/rag/ask",
            json={"question": "Anything at all?", "provider": provider},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == provider
    assert resolved_providers == [provider]


@pytest.mark.anyio
async def test_rag_api_ask_without_provider_backward_compatible(
    pgvector_session,
    rag_api_dependencies,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/rag/ask",
            json={"question": "Anything at all?"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o-mini"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "claude-haiku-4-5-20251001"),
        ("groq", "gemini-3.1-flash-lite"),
    ],
)
async def test_rag_api_invalid_provider_model_combo(
    pgvector_session,
    rag_api_dependencies,
    provider: str,
    model: str,
) -> None:
    user_id = await _make_user(pgvector_session)
    headers = _auth_headers(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/rag/ask",
            json={
                "question": "Hello?",
                "provider": provider,
                "model": model,
            },
            headers=headers,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
