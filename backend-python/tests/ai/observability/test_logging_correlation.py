"""Logging correlation tests for trace_id/span_id binding."""

from __future__ import annotations

import io
import json
import logging

from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.metrics.meter import MeterRegistry
from app.ai.observability.tracing.provider import TracerRegistry
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.main import app
from app.middleware.correlation_id import REQUEST_ID_HEADER


@pytest.fixture(autouse=True)
def _reset_observability_registries() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    MeterRegistry.reset_for_tests()
    get_settings.cache_clear()
    yield
    TracerRegistry.reset_for_tests()
    MeterRegistry.reset_for_tests()
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_trace_ids_absent_when_observability_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "false")
    get_settings.cache_clear()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    settings = Settings(
        openai_api_key="test-key",
        app_env="production",
        log_level="INFO",
        observability_enabled=False,
    )
    setup_logging(settings, handler=handler)
    TracerRegistry.initialize(settings)
    MeterRegistry.initialize(settings)

    incoming = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.get("/api/health", headers={REQUEST_ID_HEADER: incoming})

    for line in stream.getvalue().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        assert "trace_id" not in payload
        assert "span_id" not in payload


@pytest.mark.anyio
async def test_trace_ids_present_when_observability_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "true")
    get_settings.cache_clear()

    memory_exporter = InMemorySpanExporter()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    settings = Settings(
        openai_api_key="test-key",
        app_env="production",
        log_level="INFO",
        observability_enabled=True,
    )
    setup_logging(settings, handler=handler)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(memory_exporter)],
    )
    MeterRegistry.initialize(settings)

    incoming = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.get("/api/health", headers={REQUEST_ID_HEADER: incoming})

    logged_trace_ids: set[str] = set()
    for line in stream.getvalue().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if "trace_id" in payload:
            logged_trace_ids.add(payload["trace_id"])
            assert payload.get("span_id")

    assert logged_trace_ids
    http_spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "http.server"
    ]
    assert len(http_spans) == 1
