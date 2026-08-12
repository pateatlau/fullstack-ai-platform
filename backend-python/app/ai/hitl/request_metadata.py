"""Capture HITL audit request metadata from inbound HTTP requests."""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.ai.hitl.models import RequestMetadata
from app.core.config import Settings
from app.middleware.correlation_id import get_request_id

_FORWARDED_FOR_HEADER = "x-forwarded-for"


def resolve_client_source_ip(request: Request, *, trust_forwarded: bool) -> str | None:
    """Resolve the client IP, optionally trusting ``X-Forwarded-For``."""
    if trust_forwarded:
        forwarded = request.headers.get(_FORWARDED_FOR_HEADER)
        if forwarded:
            for candidate in forwarded.split(","):
                parsed = _parse_ip(candidate)
                if parsed is not None:
                    return parsed
    if request.client is not None and request.client.host:
        parsed = _parse_ip(request.client.host)
        if parsed is not None:
            return parsed
    return None


def build_request_metadata(request: Request, settings: Settings) -> RequestMetadata:
    """Capture requester context for audit metadata (recommendation #4)."""
    user_agent = request.headers.get("user-agent")
    bounded_user_agent = (
        _bound_user_agent(user_agent, max_length=settings.hitl_max_user_agent_length)
        if user_agent
        else None
    )
    return RequestMetadata(
        request_id=get_request_id(),
        source_ip=resolve_client_source_ip(
            request,
            trust_forwarded=settings.hitl_trust_forwarded_client_ip,
        ),
        client_metadata=(
            {"user_agent": bounded_user_agent} if bounded_user_agent else {}
        ),
    )


def _bound_user_agent(value: str, *, max_length: int) -> str:
    return value if len(value) <= max_length else value[:max_length]


def _parse_ip(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.startswith('"') and candidate.endswith('"'):
        candidate = candidate[1:-1]
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None
