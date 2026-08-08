"""Span helper context managers (stable public API after Phase 1).

Bodies are generic scaffolds until Phases 2–4 wire real pipeline call sites.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any, Literal

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from app.ai.observability.tracing.provider import get_tracer
from app.core.logging import get_logger, sanitize_value

logger = get_logger(__name__)

_TOKEN_COUNT_ATTRIBUTE_KEYS = frozenset(
    {"prompt_tokens", "completion_tokens", "total_tokens"}
)

_LITERAL_ATTRIBUTE_KEYS = frozenset(
    {
        "authorization_result",
        "finish_reason",
        "success",
        "streaming",
    }
)

AgentSpanAction = Literal["iteration", "tool_call", "reflection"]
RagSpanAction = Literal["retrieve"]
MemorySpanAction = Literal["retrieve", "extract"]
VoiceSpanAction = Literal["session"]
WorkflowSpanAction = Literal["run", "node"]

_AGENT_SPAN_NAMES: dict[AgentSpanAction, str] = {
    "iteration": "agent.iteration",
    "tool_call": "agent.tool_call",
    "reflection": "agent.reflection",
}

_tool_retry_count: contextvars.ContextVar[int] = contextvars.ContextVar(
    "tool_retry_count",
    default=0,
)


def current_tool_retry_count() -> int:
    """Retry attempt index for the active ``ToolExecutor.execute()`` call."""
    return _tool_retry_count.get()


def set_tool_retry_count(retry_count: int) -> contextvars.Token[int]:
    """Bind retry attempt index for nested ``ToolExecutor.execute()`` calls."""
    return _tool_retry_count.set(retry_count)


def reset_tool_retry_count(token: contextvars.Token[int]) -> None:
    """Restore the prior retry attempt index."""
    _tool_retry_count.reset(token)


def _format_trace_id(trace_id: int) -> str:
    return format(trace_id, "032x")


def _format_span_id(span_id: int) -> str:
    return format(span_id, "016x")


def format_span_context(span: Span | None) -> tuple[str | None, str | None]:
    """Return ``(trace_id, span_id)`` hex strings for log correlation."""
    if span is None:
        return None, None
    context = span.get_span_context()
    if not context.is_valid:
        return None, None
    return _format_trace_id(context.trace_id), _format_span_id(context.span_id)


def _set_span_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            if key in _TOKEN_COUNT_ATTRIBUTE_KEYS or key in _LITERAL_ATTRIBUTE_KEYS:
                sanitized = value
            else:
                sanitized = sanitize_value(key, value)
            span.set_attribute(key, sanitized)
        except Exception as exc:
            logger.warning(
                "Observability span attribute failed",
                span_attribute=key,
                error=str(exc),
                exc_info=True,
            )


@contextmanager
def _observability_span(
    span_name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, Any] | None = None,
) -> Generator[Span | None, None, None]:
    """Open a named span; telemetry failures are fail-open, business errors propagate."""
    span: Span | None = None
    token: object | None = None
    try:
        span = get_tracer(__name__).start_span(span_name, kind=kind)
        if attributes:
            _set_span_attributes(span, attributes)
        token = otel_context.attach(trace.set_span_in_context(span))
    except Exception as exc:
        logger.warning(
            "Observability span setup failed",
            span_name=span_name,
            error=str(exc),
            exc_info=True,
        )
        span = None
        token = None

    try:
        yield span
    except Exception:
        if span is not None:
            try:
                span.set_status(Status(StatusCode.ERROR))
            except Exception as exc:
                logger.warning(
                    "Observability span status failed",
                    span_name=span_name,
                    error=str(exc),
                    exc_info=True,
                )
        raise
    finally:
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception as exc:
                logger.warning(
                    "Observability span context detach failed",
                    span_name=span_name,
                    error=str(exc),
                    exc_info=True,
                )
        if span is not None:
            try:
                span.end()
            except Exception as exc:
                logger.warning(
                    "Observability span end failed",
                    span_name=span_name,
                    error=str(exc),
                    exc_info=True,
                )


@contextmanager
def http_server_span(
    *,
    method: str,
    route: str,
) -> Generator[Span | None, None, None]:
    """Root HTTP request span — opened by correlation middleware when enabled."""
    with _observability_span(
        "http.server",
        kind=SpanKind.SERVER,
        attributes={"http.request.method": method, "http.route": route},
    ) as span:
        yield span


@contextmanager
def llm_span(
    provider: str,
    model: str,
    streaming: bool,
) -> Generator[Span | None, None, None]:
    with _observability_span(
        "llm.complete",
        attributes={"provider": provider, "model": model, "streaming": streaming},
    ) as span:
        yield span


@contextmanager
def prompt_span(
    category: str,
    name: str,
    version: str,
) -> Generator[Span | None, None, None]:
    with _observability_span(
        "prompt.render",
        attributes={"category": category, "name": name, "version": version},
    ) as span:
        yield span


@contextmanager
def tool_span(tool_name: str) -> Generator[Span | None, None, None]:
    with _observability_span(
        "tool.execute",
        attributes={"tool_name": tool_name},
    ) as span:
        yield span


@contextmanager
def agent_span(action: AgentSpanAction) -> Generator[Span | None, None, None]:
    with _observability_span(_AGENT_SPAN_NAMES[action]) as span:
        yield span


@contextmanager
def rag_span(action: RagSpanAction) -> Generator[Span | None, None, None]:
    del action  # Only ``retrieve`` is supported in Phase 1 scaffold.
    with _observability_span("rag.retrieve") as span:
        yield span


@contextmanager
def memory_span(action: MemorySpanAction) -> Generator[Span | None, None, None]:
    span_name = f"memory.{action}"
    with _observability_span(span_name) as span:
        yield span


@contextmanager
def voice_span(action: VoiceSpanAction) -> Generator[Span | None, None, None]:
    del action  # Only ``session`` is supported in Phase 1 scaffold.
    with _observability_span("voice.session") as span:
        yield span


@contextmanager
def workflow_span(action: WorkflowSpanAction) -> Generator[Span | None, None, None]:
    span_name = f"workflow.{action}"
    with _observability_span(span_name) as span:
        yield span


def record_tool_span_outcome(
    span: Span | None,
    *,
    success: bool,
    latency_ms: int,
    authorization_result: str | None = None,
    retry_count: int | None = None,
) -> None:
    """Attach terminal tool execution attributes and mark failed outcomes on the span."""
    if span is None:
        return
    attributes: dict[str, Any] = {
        "success": success,
        "latency_ms": latency_ms,
        "retry_count": retry_count
        if retry_count is not None
        else current_tool_retry_count(),
    }
    if authorization_result is not None:
        attributes["authorization_result"] = authorization_result
    _set_span_attributes(span, attributes)
    if not success:
        try:
            span.set_status(Status(StatusCode.ERROR))
        except Exception as exc:
            logger.warning(
                "Observability span status failed",
                span_name="tool.execute",
                error=str(exc),
                exc_info=True,
            )


def record_agent_iteration_attributes(
    span: Span | None,
    *,
    iteration_index: int,
    tool_calls_count: int,
    latency_ms: int,
    finish_reason: str | None = None,
) -> None:
    """Attach terminal agent iteration attributes to an ``agent.iteration`` span."""
    if span is None:
        return
    attributes: dict[str, Any] = {
        "iteration_index": iteration_index,
        "tool_calls_count": tool_calls_count,
        "latency_ms": latency_ms,
    }
    if finish_reason is not None:
        attributes["finish_reason"] = finish_reason
    _set_span_attributes(span, attributes)


def record_agent_tool_call_attributes(
    span: Span | None,
    *,
    tool_name: str,
    latency_ms: int,
) -> None:
    """Attach terminal attributes to an ``agent.tool_call`` span."""
    if span is None:
        return
    _set_span_attributes(
        span,
        {
            "tool_name": tool_name,
            "latency_ms": latency_ms,
        },
    )


def elapsed_ms_since(start: float) -> int:
    """Return whole milliseconds elapsed since a ``time.perf_counter()`` timestamp."""
    return int((time.perf_counter() - start) * 1000)
