"""Observability public API (stable after Phase 1).

See ``docs/plans/post-mvp-v2-epic-07-observability-and-evaluation.md`` Part I § Public APIs.
"""

from app.ai.observability.exceptions import (
    ObservabilityConfigError,
    ObservabilityDisabledError,
    ObservabilityError,
)
from app.ai.observability.metrics.meter import MeterRegistry, get_meter
from app.ai.observability.tracing.provider import TracerRegistry, get_tracer
from app.ai.observability.tracing.spans import (
    agent_span,
    format_span_context,
    http_server_span,
    llm_span,
    memory_span,
    prompt_span,
    rag_span,
    tool_span,
    voice_span,
    workflow_span,
)

OBSERVABILITY_ENABLED = "observability_enabled"

__all__ = [
    "OBSERVABILITY_ENABLED",
    "MeterRegistry",
    "ObservabilityConfigError",
    "ObservabilityDisabledError",
    "ObservabilityError",
    "TracerRegistry",
    "agent_span",
    "format_span_context",
    "get_meter",
    "get_tracer",
    "http_server_span",
    "llm_span",
    "memory_span",
    "prompt_span",
    "rag_span",
    "tool_span",
    "voice_span",
    "workflow_span",
]
