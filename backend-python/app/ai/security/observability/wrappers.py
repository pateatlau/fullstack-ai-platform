"""Security observability wrappers for spans and metrics (Epic 11 Phase 9).

Context managers wrapping ToolAuthorizer.authorize(), AgentApprovalService.decide()
stage checks, and GuardrailEngine.evaluate() to emit security telemetry when
OBSERVABILITY_ENABLED=true, with spans (content-free, actor_user_id/rule_id as
attributes only) and metrics (bounded cardinality labels only).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextlib import contextmanager as sync_contextmanager
from typing import AsyncGenerator, Generator

from opentelemetry.trace import Span

from app.ai.observability.metrics.instruments import (
    record_authz_denied,
    record_guardrail_verdict,
)
from app.ai.observability.tracing.spans import (
    authz_span,
    guardrail_span,
    record_authz_outcome,
    record_guardrail_outcome,
)


@sync_contextmanager
def authz_span_context(
    *,
    actor_user_id: str | None = None,
    permission_key: str | None = None,
) -> Generator[Span | None, None, None]:
    """Context manager for authorization decision spans.

    Yields the span (or None if observability is disabled) for caller to attach
    terminal outcomes via record_authz_outcome().
    """
    with authz_span(
        actor_user_id=actor_user_id,
        permission_key=permission_key,
    ) as span:
        yield span


def record_authz_denial(
    span: Span | None,
    *,
    actor_user_id: str | None = None,
    permission_key: str | None = None,
    resource_type: str | None = None,
) -> None:
    """Record authorization denial: both span attributes and metric counter.

    Span attributes include actor_user_id and resource_type (content-free ids only).
    Metric counter uses bounded labels: permission_key, resource_type.
    """
    record_authz_outcome(
        span,
        actor_user_id=actor_user_id,
        permission_key=permission_key,
        outcome="denied",
        resource_type=resource_type,
    )
    if permission_key:
        record_authz_denied(
            permission_key=permission_key,
            resource_type=resource_type,
        )


def record_authz_allowed(
    span: Span | None,
    *,
    actor_user_id: str | None = None,
    permission_key: str | None = None,
    resource_type: str | None = None,
) -> None:
    """Record authorization allowed: span attributes only (no denial metric)."""
    record_authz_outcome(
        span,
        actor_user_id=actor_user_id,
        permission_key=permission_key,
        outcome="allowed",
        resource_type=resource_type,
    )


@sync_contextmanager
def guardrail_span_context(
    *,
    source: str | None = None,
) -> Generator[Span | None, None, None]:
    """Context manager for guardrail evaluation spans.

    Yields the span (or None if observability is disabled) for caller to attach
    terminal outcomes via record_guardrail_outcome().
    """
    with guardrail_span(source=source) as span:
        yield span


def record_guardrail_verdict_telemetry(
    span: Span | None,
    *,
    source: str | None = None,
    action: str,  # "allow", "flag", or "block"
    matched_rule_id: str | None = None,
    matched_rule_version: str | None = None,
) -> None:
    """Record guardrail verdict: both span attributes and metric counter.

    Span attributes include matched_rule_id and matched_rule_version (content-free ids only).
    Metric counter uses bounded labels: source, action.
    """
    record_guardrail_outcome(
        span,
        source=source,
        action=action,
        matched_rule_id=matched_rule_id,
        matched_rule_version=matched_rule_version,
    )
    # Record metric with bounded labels only
    if source:
        record_guardrail_verdict(source=source, action=action)


@asynccontextmanager
async def authz_span_async(
    *,
    actor_user_id: str | None = None,
    permission_key: str | None = None,
) -> AsyncGenerator[Span | None, None]:
    """Async context manager for authorization decision spans.

    For use with AgentApprovalService.decide() which is async.
    """
    with authz_span(
        actor_user_id=actor_user_id,
        permission_key=permission_key,
    ) as span:
        yield span


@asynccontextmanager
async def guardrail_span_async(
    *,
    source: str | None = None,
) -> AsyncGenerator[Span | None, None]:
    """Async context manager for guardrail evaluation spans.

    For use with async GuardrailEngine.evaluate() calls.
    """
    with guardrail_span(source=source) as span:
        yield span
