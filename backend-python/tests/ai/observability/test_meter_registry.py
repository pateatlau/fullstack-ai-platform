"""MeterRegistry bootstrap and real-vs-no-op behaviour tests."""

from __future__ import annotations

from opentelemetry import metrics

from app.ai.observability.metrics.meter import MeterRegistry, get_meter
from app.core.config import Settings


def test_disabled_registry_installs_noop_meter() -> None:
    MeterRegistry.reset_for_tests()
    settings = Settings(openai_api_key="test-key", observability_enabled=False)
    MeterRegistry.initialize(settings)

    meter = get_meter("test")
    counter = meter.create_counter("probe_total")
    counter.add(1)
    assert MeterRegistry.is_enabled() is False
    assert MeterRegistry.get_prometheus_reader() is None


def test_enabled_registry_exposes_prometheus_reader() -> None:
    MeterRegistry.reset_for_tests()
    settings = Settings(
        openai_api_key="test-key",
        observability_enabled=True,
    )
    MeterRegistry.initialize(settings)

    meter = get_meter("test")
    counter = meter.create_counter("probe_total")
    counter.add(1)
    reader = MeterRegistry.get_prometheus_reader()
    assert reader is not None
    assert MeterRegistry.is_enabled() is True


def test_initialize_is_idempotent() -> None:
    MeterRegistry.reset_for_tests()
    settings = Settings(
        openai_api_key="test-key",
        observability_enabled=True,
    )
    MeterRegistry.initialize(settings)
    first_reader = MeterRegistry.get_prometheus_reader()
    MeterRegistry.initialize(settings)
    assert MeterRegistry.get_prometheus_reader() is first_reader


def test_get_meter_delegates_to_global_provider() -> None:
    MeterRegistry.reset_for_tests()
    settings = Settings(openai_api_key="test-key", observability_enabled=False)
    MeterRegistry.initialize(settings)

    meter = get_meter("app.test")
    global_meter = metrics.get_meter("app.test")
    assert type(meter) is type(global_meter)
    counter = meter.create_counter("delegation_probe")
    counter.add(1)
