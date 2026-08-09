"""TracerRegistry bootstrap and real-vs-no-op behaviour tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.tracing.provider import TracerRegistry, get_tracer
from app.core.config import Settings


def _enabled_settings(**overrides: object) -> Settings:
    return Settings(
        openai_api_key="test-key",
        observability_enabled=True,
        **overrides,  # type: ignore[arg-type]
    )


def test_disabled_registry_installs_noop_tracer() -> None:
    TracerRegistry.reset_for_tests()
    settings = Settings(openai_api_key="test-key", observability_enabled=False)
    TracerRegistry.initialize(settings)

    tracer = get_tracer("test")
    span = tracer.start_span("probe")
    assert not span.get_span_context().is_valid
    span.end()
    assert TracerRegistry.is_enabled() is False


def test_enabled_registry_produces_recording_spans() -> None:
    TracerRegistry.reset_for_tests()
    memory_exporter = InMemorySpanExporter()
    settings = _enabled_settings()
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(memory_exporter)],
    )

    tracer = get_tracer("test")
    with tracer.start_as_current_span("probe") as span:
        assert span.get_span_context().is_valid

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "probe"
    assert TracerRegistry.is_enabled() is True


def test_initialize_is_idempotent() -> None:
    TracerRegistry.reset_for_tests()
    memory_exporter = InMemorySpanExporter()
    settings = _enabled_settings()
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(memory_exporter)],
    )
    TracerRegistry.initialize(settings)

    get_tracer("test").start_span("second").end()
    assert len(memory_exporter.get_finished_spans()) == 1


def test_get_tracer_delegates_to_registry() -> None:
    TracerRegistry.reset_for_tests()
    settings = Settings(openai_api_key="test-key", observability_enabled=False)
    TracerRegistry.initialize(settings)

    tracer = get_tracer("app.test")
    global_tracer = trace.get_tracer("app.test")
    assert type(tracer) is type(global_tracer)
    span = tracer.start_span("delegation_probe")
    assert not span.get_span_context().is_valid
    span.end()


def test_reset_for_tests_shuts_down_active_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown = MagicMock()
    provider = MagicMock()
    provider.shutdown = shutdown
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)

    TracerRegistry.reset_for_tests()

    shutdown.assert_called_once()
