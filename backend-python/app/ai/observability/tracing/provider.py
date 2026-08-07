"""Process-wide OpenTelemetry tracer bootstrap and access."""

from __future__ import annotations

from collections.abc import Sequence

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import NoOpTracerProvider, Tracer

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class TracerRegistry:
    """Provides the process-wide OTel ``Tracer`` (real or no-op)."""

    _initialized = False
    _enabled = False

    @classmethod
    def initialize(
        cls,
        settings: Settings,
        *,
        extra_span_processors: Sequence[SpanProcessor] | None = None,
    ) -> None:
        """Idempotent bootstrap — safe to call once at application startup."""
        if cls._initialized:
            return

        cls._enabled = settings.observability_enabled
        if not cls._enabled:
            trace.set_tracer_provider(NoOpTracerProvider())
            cls._initialized = True
            return

        resource = Resource.create({"service.name": settings.otel_service_name})
        sampler = ParentBased(root=TraceIdRatioBased(settings.otel_traces_sample_ratio))
        provider = TracerProvider(resource=resource, sampler=sampler)

        if settings.otel_exporter_otlp_endpoint.strip():
            exporter = OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint.strip()
            )
        else:
            exporter = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exporter))
        for processor in extra_span_processors or ():
            provider.add_span_processor(processor)

        trace.set_tracer_provider(provider)
        cls._initialized = True

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def get_tracer(cls, name: str) -> Tracer:
        return trace.get_tracer(name)

    @classmethod
    def reset_for_tests(cls) -> None:
        """Reset registry state — test helper only."""
        import opentelemetry.trace as ot_trace

        cls._initialized = False
        cls._enabled = False
        ot_trace._TRACER_PROVIDER = ot_trace._PROXY_TRACER_PROVIDER
        ot_trace._TRACER_PROVIDER_SET_ONCE._done = False


def get_tracer(name: str) -> Tracer:
    """Return the process-wide OTel tracer for ``name``."""
    return TracerRegistry.get_tracer(name)
