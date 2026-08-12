"""Background Jobs observability span and metric tests (Epic 10 Phase 8)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.ai.jobs.models import BackgroundJob, JobResult, JobStatus
from app.ai.jobs.queue import PostgresJobQueue
from app.ai.jobs.registry import JobHandlerRegistry
from app.ai.jobs.worker import JobWorker
from app.ai.observability.metrics.instruments import (
    MetricInstruments,
    record_job_dead_lettered,
    record_job_duration_ms,
    record_job_enqueued,
    record_job_manual_retry,
    record_job_retry,
    record_job_succeeded,
)
from app.ai.observability.metrics.meter import MeterRegistry
from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.observability.tracing.spans import job_span, record_job_dispatch_outcome
from app.core.config import Settings
from app.core.logging import clear_context, get_log_context
from tests.ai.jobs.conftest import (
    background_jobs_table_available,
    make_queue_session_factory,
)


pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _reset_observability() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()
    clear_context()
    yield
    TracerRegistry.reset_for_tests()
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()
    clear_context()


@pytest.fixture
def observability_stack() -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    settings = Settings(openai_api_key="test-key", observability_enabled=True)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    MeterRegistry._initialized = True
    MeterRegistry._enabled = True
    MetricInstruments.initialize()
    return exporter, reader


def _metric_sum(reader: InMemoryMetricReader, name: str) -> float:
    data = reader.get_metrics_data()
    assert data is not None
    total = 0.0
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    counter_value = getattr(point, "value", None)
                    if counter_value is not None:
                        total += float(counter_value)
                        continue
                    histogram_sum = getattr(point, "sum", None)
                    if histogram_sum is not None:
                        total += float(histogram_sum)
    return total


def _metric_present(reader: InMemoryMetricReader, name: str) -> bool:
    data = reader.get_metrics_data()
    if data is None:
        return False
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name and metric.data.data_points:
                    return True
    return False


def _job_spans(exporter: InMemorySpanExporter) -> list:
    return [
        span for span in exporter.get_finished_spans() if span.name == "job.dispatch"
    ]


def test_job_span_success_attributes(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, reader = observability_stack
    job_id = str(uuid.uuid4())

    with job_span(
        job_id=job_id,
        job_type="rag_document_indexing",
        job_status="running",
        attempt_count=1,
    ) as span:
        record_job_dispatch_outcome(
            span,
            job_status="succeeded",
            handler_duration_ms=42,
            dispatch_duration_ms=42,
            job_type="rag_document_indexing",
        )

    spans = _job_spans(exporter)
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["job_id"] == job_id
    assert attrs["job_type"] == "rag_document_indexing"
    assert attrs["job_status"] == "succeeded"
    assert attrs["attempt_count"] == 1
    assert attrs["duration_ms"] == 42
    assert spans[0].status.status_code == StatusCode.UNSET
    assert _metric_present(reader, "job_duration_ms")


def test_job_span_failure_marks_error(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, _reader = observability_stack

    with (
        pytest.raises(RuntimeError, match="handler failed"),
        job_span(
            job_id=str(uuid.uuid4()),
            job_type="fixture_fail",
            job_status="running",
            attempt_count=2,
        ),
    ):
        raise RuntimeError("handler failed")

    spans = _job_spans(exporter)
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR


def test_job_span_failed_outcome_marks_error(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, _reader = observability_stack

    with job_span(
        job_id=str(uuid.uuid4()),
        job_type="fixture_fail",
        job_status="running",
        attempt_count=1,
    ) as span:
        record_job_dispatch_outcome(
            span,
            job_status="dead_letter",
            handler_duration_ms=5,
            job_type="fixture_fail",
            failed=True,
        )

    spans = _job_spans(exporter)
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR


def test_queue_metrics_all_six_instruments(
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    _exporter, reader = observability_stack

    record_job_enqueued(job_type="rag_document_indexing")
    record_job_retry(job_type="rag_document_indexing")
    record_job_succeeded(job_type="rag_document_indexing")
    record_job_dead_lettered(job_type="hitl_approval_expiry_sweep")
    record_job_manual_retry()
    record_job_duration_ms(job_type="scheduled_evaluation_run", duration_ms=100)

    assert _metric_sum(reader, "jobs_enqueued_total") == 1.0
    assert _metric_sum(reader, "job_retries_total") == 1.0
    assert _metric_sum(reader, "jobs_completed_total") == 2.0
    assert _metric_sum(reader, "jobs_pending_count") == 0.0
    assert _metric_sum(reader, "jobs_dead_letter_count") == 0.0
    assert _metric_present(reader, "job_duration_ms")

    for name in (
        "jobs_enqueued_total",
        "jobs_completed_total",
        "job_retries_total",
        "jobs_pending_count",
        "jobs_dead_letter_count",
        "job_duration_ms",
    ):
        assert _metric_present(reader, name)


def test_observability_disabled_emits_no_job_telemetry() -> None:
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    settings = Settings(openai_api_key="test-key", observability_enabled=False)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    MeterRegistry._initialized = True
    MeterRegistry._enabled = False
    MetricInstruments.initialize()

    with job_span(
        job_id=str(uuid.uuid4()),
        job_type="rag_document_indexing",
        job_status="running",
        attempt_count=1,
    ) as span:
        record_job_dispatch_outcome(
            span,
            job_status="succeeded",
            handler_duration_ms=10,
            job_type="rag_document_indexing",
        )

    record_job_enqueued(job_type="rag_document_indexing")
    record_job_succeeded(job_type="rag_document_indexing")

    assert len(exporter.get_finished_spans()) == 0
    assert not _metric_present(reader, "jobs_enqueued_total")


@pytest.fixture
def worker_settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        observability_enabled=True,
        background_jobs_default_max_attempts=3,
        background_jobs_worker_batch_size=10,
        background_jobs_claim_lease_seconds=300,
        background_jobs_handler_timeout_seconds=1,
        background_jobs_retry_base_delay_seconds=0.0,
        background_jobs_retry_max_delay_seconds=0.0,
        background_jobs_worker_poll_interval_seconds=60,
    )


@pytest.mark.anyio
async def test_worker_dispatch_emits_span_and_queue_metrics(
    db_session,
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    exporter, reader = observability_stack
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, worker_settings)
    registry = JobHandlerRegistry()
    observed_ctx: dict[str, object] = {}

    async def success_handler(job: BackgroundJob) -> JobResult:
        observed_ctx.update(get_log_context())
        return JobResult(summary=f"handled {job.job_type}")

    registry.register("rag_document_indexing", success_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=worker_settings)

    await queue.enqueue(job_type="rag_document_indexing", payload={"version": 1})
    await worker.poll_once()

    spans = _job_spans(exporter)
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["job_type"] == "rag_document_indexing"
    assert attrs["job_status"] == "succeeded"
    assert "duration_ms" in attrs

    assert _metric_sum(reader, "jobs_enqueued_total") == 1.0
    assert _metric_sum(reader, "jobs_completed_total") == 1.0
    assert _metric_sum(reader, "jobs_pending_count") == 0.0
    assert _metric_present(reader, "job_duration_ms")

    assert observed_ctx.get("job_type") == "rag_document_indexing"
    assert observed_ctx.get("attempt_count") == 1
    assert "job_id" in observed_ctx


@pytest.mark.anyio
async def test_worker_failed_dispatch_emits_error_span(
    db_session,
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    exporter, reader = observability_stack
    settings = worker_settings.model_copy(
        update={"background_jobs_default_max_attempts": 1}
    )
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    registry = JobHandlerRegistry()

    async def fail_handler(job: BackgroundJob) -> JobResult:
        del job
        raise RuntimeError("boom")

    registry.register("fixture_fail", fail_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=settings)

    await queue.enqueue(
        job_type="fixture_fail",
        payload={"version": 1},
        max_attempts=1,
    )
    await worker.poll_once()

    spans = _job_spans(exporter)
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert dict(spans[0].attributes or {})["job_status"] == "dead_letter"
    assert _metric_sum(reader, "jobs_dead_letter_count") == 1.0


@pytest.mark.anyio
async def test_manual_retry_adjusts_depth_gauges(
    db_session,
    observability_stack: tuple[InMemorySpanExporter, InMemoryMetricReader],
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    _exporter, reader = observability_stack
    settings = worker_settings.model_copy(
        update={"background_jobs_default_max_attempts": 1}
    )
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    registry = JobHandlerRegistry()

    async def fail_handler(job: BackgroundJob) -> JobResult:
        del job
        raise RuntimeError("permanent")

    registry.register("rag_document_indexing", fail_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=settings)

    job = await queue.enqueue(job_type="rag_document_indexing", payload={"version": 1})
    await worker.poll_once()

    dead = await queue.get(job.id)
    assert dead is not None
    assert dead.status is JobStatus.DEAD_LETTER
    assert _metric_sum(reader, "jobs_dead_letter_count") == 1.0
    assert _metric_sum(reader, "jobs_pending_count") == 0.0

    retried = await queue.retry_dead_letter(job.id)
    assert retried is not None
    assert retried.status is JobStatus.QUEUED
    assert _metric_sum(reader, "jobs_dead_letter_count") == 0.0
    assert _metric_sum(reader, "jobs_pending_count") == 1.0
