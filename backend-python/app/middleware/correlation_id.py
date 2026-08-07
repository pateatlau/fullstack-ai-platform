"""Request correlation ID middleware and context access."""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.observability.tracing.spans import format_span_context, http_server_span
from app.core.cors import bind_request_origin, reset_request_origin
from app.core.logging import bind_context, clear_context

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def resolve_request_id(header_value: str | None) -> str:
    """Return a validated incoming request ID or mint a new UUID."""
    if header_value:
        try:
            return str(uuid.UUID(header_value.strip()))
        except ValueError:
            pass
    return str(uuid.uuid4())


def get_request_id() -> str | None:
    return _request_id.get()


def _set_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def _reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


async def correlation_id_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Assign/propagate ``X-Request-ID`` and bind it into structured log context."""
    request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
    request_id_token = _set_request_id(request_id)
    origin_token = bind_request_origin(request.headers.get("origin"))
    bind_context(request_id=request_id)

    observability_enabled = TracerRegistry.is_enabled()
    if observability_enabled:
        try:
            with http_server_span(
                method=request.method,
                route=request.url.path,
            ) as span:
                trace_id, span_id = format_span_context(span)
                bind_context(trace_id=trace_id, span_id=span_id)
                response = await call_next(request)
                response.headers[REQUEST_ID_HEADER] = request_id
                return response
        finally:
            reset_request_origin(origin_token)
            _reset_request_id(request_id_token)
            clear_context()

    try:
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    finally:
        reset_request_origin(origin_token)
        _reset_request_id(request_id_token)
        clear_context()
