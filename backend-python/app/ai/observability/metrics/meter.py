"""Process-wide OpenTelemetry meter bootstrap and access."""

from __future__ import annotations

from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import Meter, NoOpMeterProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource

from app.core.config import Settings


class MeterRegistry:
    """Provides the process-wide OTel ``Meter`` (real or no-op)."""

    _initialized = False
    _enabled = False
    _prometheus_reader: PrometheusMetricReader | None = None

    @classmethod
    def initialize(cls, settings: Settings) -> None:
        """Idempotent bootstrap — safe to call once at application startup."""
        if cls._initialized:
            return

        cls._enabled = settings.observability_enabled
        if not cls._enabled:
            metrics.set_meter_provider(NoOpMeterProvider())
            cls._initialized = True
            return

        resource = Resource.create({"service.name": settings.otel_service_name})
        cls._prometheus_reader = PrometheusMetricReader()
        provider = MeterProvider(
            resource=resource,
            metric_readers=[cls._prometheus_reader],
        )
        metrics.set_meter_provider(provider)
        cls._initialized = True

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def get_meter(cls, name: str) -> Meter:
        return metrics.get_meter(name)

    @classmethod
    def get_prometheus_reader(cls) -> PrometheusMetricReader | None:
        """Return the Prometheus reader when enabled; ``None`` when disabled."""
        return cls._prometheus_reader

    @classmethod
    def reset_for_tests(cls) -> None:
        """Reset registry state — test helper only."""
        import opentelemetry.metrics._internal as ot_metrics

        cls._initialized = False
        cls._enabled = False
        cls._prometheus_reader = None
        ot_metrics._METER_PROVIDER = ot_metrics._PROXY_METER_PROVIDER
        ot_metrics._METER_PROVIDER_SET_ONCE._done = False


def get_meter(name: str) -> Meter:
    """Return the process-wide OTel meter for ``name``."""
    return MeterRegistry.get_meter(name)
