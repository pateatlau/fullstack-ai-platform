"""QueueIndexingRunner integration tests (Epic 10 Phase 5)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.documents.schemas import DocumentChunk
from app.ai.jobs.handlers.rag_indexing import rag_document_indexing
from app.ai.jobs.models import JobStatus
from app.ai.jobs.queue import PostgresJobQueue
from app.ai.jobs.registry import JobHandlerRegistry
from app.ai.jobs.worker import JobWorker
from app.ai.rag.indexing import SyncIndexingRunner
from app.ai.rag.schemas import IndexingJobState
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings
from app.db.documents import SqlDocumentStore
from app.db.identity import SqlUserStore
from app.db.models import Document
from app.services.knowledge_service import KnowledgeService
from tests.ai.jobs.conftest import (
    background_jobs_table_available,
    make_queue_session_factory,
)

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "documents"
DIMENSIONS = 1536


class _FakeEmbeddingProvider:
    dimensions = DIMENSIONS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index % DIMENSIONS), 0.0] + [0.0] * (DIMENSIONS - 2)
            for index, _ in enumerate(texts)
        ]


async def _pgvector_available(session) -> bool:
    try:
        result = await session.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result == 1
    except Exception:
        return False


async def _staging_table_available(session) -> bool:
    result = await session.scalar(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'document_upload_staging'"
        )
    )
    return result == 1


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"queue-indexing-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "advanced_rag_enabled": False,
        "background_jobs_enabled": True,
        "rag_indexing_runner": "queue",
        "background_jobs_worker_batch_size": 10,
        "background_jobs_claim_lease_seconds": 300,
        "background_jobs_handler_timeout_seconds": 60,
        "background_jobs_retry_base_delay_seconds": 0.0,
        "background_jobs_retry_max_delay_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


def _pipeline(settings: Settings) -> IngestionPipeline:
    return IngestionPipeline(settings, embedding_provider=_FakeEmbeddingProvider())


def _queue_service(
    session,
    settings: Settings,
    *,
    build_pipeline: Callable[[Settings], IngestionPipeline] | None = None,
) -> tuple[KnowledgeService, PostgresJobQueue, JobHandlerRegistry, JobWorker]:
    factory = make_queue_session_factory(session.bind)
    queue = PostgresJobQueue(factory, settings)
    pipeline_builder = build_pipeline or _pipeline
    registry = JobHandlerRegistry()
    registry.register(
        "rag_document_indexing",
        lambda job: rag_document_indexing(
            job,
            settings=settings,
            session_factory=factory,
            build_pipeline=pipeline_builder,
        ),
    )
    worker = JobWorker(queue=queue, registry=registry, settings=settings)
    service = KnowledgeService(
        session=session,
        settings=settings,
        pipeline=_pipeline(settings),
        vector_store=PgVectorStore(session, settings),
        job_queue=queue,
    )
    return service, queue, registry, worker


@pytest.fixture
async def pgvector_session(db_session):
    if not await _pgvector_available(db_session):
        pytest.skip("pgvector extension not available — run alembic upgrade head")
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")
    if not await _staging_table_available(db_session):
        pytest.skip("document_upload_staging not available — run alembic upgrade head")
    await db_session.execute(text("TRUNCATE background_jobs RESTART IDENTITY CASCADE"))
    await db_session.execute(
        text("TRUNCATE document_upload_staging RESTART IDENTITY CASCADE")
    )
    await db_session.commit()
    yield db_session


@pytest.mark.anyio
async def test_queue_submit_enqueues_and_status_transitions(
    pgvector_session,
) -> None:
    user_id = await _make_user(pgvector_session)
    settings = _settings()
    service, queue, _, worker = _queue_service(pgvector_session, settings)
    file_bytes = (FIXTURES / "sample.txt").read_bytes()

    document_id = await service.ingest_document(
        user_id=user_id,
        file_bytes=file_bytes,
        filename="sample.txt",
        mime_type="text/plain",
    )
    await pgvector_session.commit()

    jobs = await queue.list(job_type="rag_document_indexing")
    matching = [
        job for job in jobs if job.payload.get("document_id") == str(document_id)
    ]
    assert len(matching) == 1
    job_id = str(matching[0].id)

    status = await service._indexing.get_status(job_id)
    assert status.state is IndexingJobState.QUEUED

    await worker.poll_once()

    status = await service._indexing.get_status(job_id)
    assert status.state is IndexingJobState.SUCCEEDED

    updated = await queue.get(matching[0].id)
    assert updated is not None
    assert updated.status is JobStatus.SUCCEEDED

    document = await pgvector_session.scalar(
        select(Document).where(Document.id == document_id)
    )
    assert document is not None
    assert document.status == "ready"


@pytest.mark.anyio
async def test_queue_and_sync_indexing_parity(pgvector_session) -> None:
    user_id = await _make_user(pgvector_session)
    settings = _settings()
    file_bytes = (FIXTURES / "sample.txt").read_bytes()

    queue_service, _, _, worker = _queue_service(pgvector_session, settings)
    queue_document_id = await queue_service.ingest_document(
        user_id=user_id,
        file_bytes=file_bytes,
        filename="sample.txt",
        mime_type="text/plain",
    )
    await pgvector_session.commit()
    await worker.poll_once()

    sync_user_id = await _make_user(pgvector_session)
    sync_settings = settings.model_copy(
        update={"background_jobs_enabled": False, "rag_indexing_runner": "sync"}
    )
    sync_service = KnowledgeService(
        session=pgvector_session,
        settings=sync_settings,
        pipeline=_pipeline(sync_settings),
        vector_store=PgVectorStore(pgvector_session, sync_settings),
    )
    sync_document_id = await sync_service.ingest_document(
        user_id=sync_user_id,
        file_bytes=file_bytes,
        filename="sample.txt",
        mime_type="text/plain",
    )

    queue_store = SqlDocumentStore(pgvector_session)
    queue_chunks = await queue_store.list_chunks(queue_document_id)
    sync_chunks = await queue_store.list_chunks(sync_document_id)

    assert [(c.chunk_index, c.content) for c in queue_chunks] == [
        (c.chunk_index, c.content) for c in sync_chunks
    ]
    assert [c.embedding for c in queue_chunks] == [c.embedding for c in sync_chunks]


@pytest.mark.anyio
async def test_indexing_failure_surfaces_failed_status(pgvector_session) -> None:
    user_id = await _make_user(pgvector_session)
    settings = _settings(background_jobs_default_max_attempts=1)

    failing_pipeline = AsyncMock(spec=IngestionPipeline)
    failing_pipeline.parse = AsyncMock(
        return_value=type("Parsed", (), {"text": "x", "metadata": {}})()
    )
    failing_pipeline.chunk.return_value = [
        DocumentChunk(chunk_index=0, content="chunk", metadata={})
    ]
    failing_pipeline.embed.side_effect = RuntimeError("embed failed")

    def _build_failing_pipeline(_settings: Settings) -> IngestionPipeline:
        return failing_pipeline

    service, queue, _, worker = _queue_service(
        pgvector_session,
        settings,
        build_pipeline=_build_failing_pipeline,
    )

    document_id = await service.ingest_document(
        user_id=user_id,
        file_bytes=(FIXTURES / "sample.txt").read_bytes(),
        filename="sample.txt",
        mime_type="text/plain",
    )
    await pgvector_session.commit()

    jobs = await queue.list(job_type="rag_document_indexing")
    job_id = str(jobs[0].id)
    await worker.poll_once()

    status = await service._indexing.get_status(job_id)
    assert status.state is IndexingJobState.FAILED
    assert status.error_message == "RuntimeError"

    document = await pgvector_session.scalar(
        select(Document).where(Document.id == document_id)
    )
    assert document is not None
    assert document.status == "failed"


@pytest.mark.anyio
async def test_sync_default_when_flag_off(pgvector_session) -> None:
    user_id = await _make_user(pgvector_session)
    settings = _settings(
        background_jobs_enabled=False,
        rag_indexing_runner="queue",
    )
    service = KnowledgeService(
        session=pgvector_session,
        settings=settings,
        pipeline=_pipeline(settings),
        vector_store=PgVectorStore(pgvector_session, settings),
        job_queue=PostgresJobQueue(
            make_queue_session_factory(pgvector_session.bind),
            settings,
        ),
    )

    assert isinstance(service._indexing, SyncIndexingRunner)

    document_id = await service.ingest_document(
        user_id=user_id,
        file_bytes=(FIXTURES / "sample.txt").read_bytes(),
        filename="sample.txt",
        mime_type="text/plain",
    )

    document = await pgvector_session.scalar(
        select(Document).where(Document.id == document_id)
    )
    assert document is not None
    assert document.status == "ready"


@pytest.mark.anyio
async def test_sync_default_when_runner_sync(pgvector_session) -> None:
    user_id = await _make_user(pgvector_session)
    settings = _settings(rag_indexing_runner="sync")
    factory = make_queue_session_factory(pgvector_session.bind)
    service = KnowledgeService(
        session=pgvector_session,
        settings=settings,
        pipeline=_pipeline(settings),
        vector_store=PgVectorStore(pgvector_session, settings),
        job_queue=PostgresJobQueue(factory, settings),
    )

    assert isinstance(service._indexing, SyncIndexingRunner)

    document_id = await service.ingest_document(
        user_id=user_id,
        file_bytes=(FIXTURES / "sample.txt").read_bytes(),
        filename="sample.txt",
        mime_type="text/plain",
    )

    document = await pgvector_session.scalar(
        select(Document).where(Document.id == document_id)
    )
    assert document is not None
    assert document.status == "ready"
