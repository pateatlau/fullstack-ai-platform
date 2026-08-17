"""HTTP rate limiting middleware (Phase 5).

Uses an in-memory sliding window per caller bucket — suitable for single-instance
MVP deploys. For multi-instance production, replace the module-level
:class:`SlidingWindowRateLimiter` store with a shared Redis sorted-set backend
using the same bucket keys and window semantics.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.caller import GUEST_TOKEN_HEADER
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import (
    InvalidAccessTokenError,
    decode_access_token,
    hash_ip,
    hash_token,
)

logger = get_logger(__name__)

WINDOW_SECONDS = 60
EXEMPT_PATHS = frozenset({"/api/health", "/api/health/ready"})

CallerTier = Literal["authenticated", "anonymous"]


@dataclass(frozen=True)
class RateLimitIdentity:
    tier: CallerTier
    bucket_key: str
    user_id: uuid.UUID | None = None


class SlidingWindowRateLimiter:
    """In-memory sliding-window counter keyed by caller bucket."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, bucket_key: str, limit: int) -> int | None:
        """Record the request when under limit; otherwise return retry-after seconds."""
        now = time.monotonic()
        window_start = now - WINDOW_SECONDS

        async with self._lock:
            timestamps = self._events[bucket_key]
            while timestamps and timestamps[0] <= window_start:
                timestamps.popleft()

            if len(timestamps) >= limit:
                oldest = timestamps[0]
                retry_after = max(1, math.ceil(WINDOW_SECONDS - (now - oldest)))
                return retry_after

            timestamps.append(now)
            return None

    def reset(self) -> None:
        self._events.clear()


_limiter = SlidingWindowRateLimiter()
_role_cache: dict[uuid.UUID, tuple[float, str]] = {}
_role_cache_lock = asyncio.Lock()


def get_rate_limiter() -> SlidingWindowRateLimiter:
    return _limiter


def reset_rate_limiter() -> None:
    _limiter.reset()
    _role_cache.clear()


async def check_rate_limit_bucket(bucket_key: str, limit: int) -> int | None:
    """Check a non-HTTP bucket using the shared sliding-window limiter."""
    return await get_rate_limiter().check(bucket_key, limit)


async def _get_rate_limit_role(user_id: uuid.UUID, settings: Settings) -> str:
    now = time.monotonic()
    if settings.security_rbac_cache_ttl_seconds > 0:
        cached = _role_cache.get(user_id)
        if (
            cached is not None
            and now - cached[0] < settings.security_rbac_cache_ttl_seconds
        ):
            return cached[1]

    async with _role_cache_lock:
        now = time.monotonic()
        if settings.security_rbac_cache_ttl_seconds > 0:
            cached = _role_cache.get(user_id)
            if (
                cached is not None
                and now - cached[0] < settings.security_rbac_cache_ttl_seconds
            ):
                return cached[1]

        from app.ai.security.rbac.service import RbacService
        from app.ai.security.rbac.store import PostgresRoleStore
        from app.db.engine import get_sessionmaker

        async with get_sessionmaker()() as session:
            role = await RbacService(
                PostgresRoleStore(session),
                cache_ttl_seconds=settings.security_rbac_cache_ttl_seconds,
            ).get_highest_priority_role(user_id)
        if settings.security_rbac_cache_ttl_seconds > 0:
            _role_cache[user_id] = (time.monotonic(), role)
        return role


def _extract_bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def resolve_rate_limit_identity(
    request: Request, settings: Settings
) -> RateLimitIdentity:
    """Classify the caller tier and derive a stable rate-limit bucket key."""
    bearer = _extract_bearer_token(request)
    if bearer is not None:
        try:
            user_id = decode_access_token(bearer, settings=settings)
            return RateLimitIdentity(
                tier="authenticated",
                bucket_key=f"auth:{user_id}",
                user_id=user_id,
            )
        except InvalidAccessTokenError:
            pass

    guest_token = request.headers.get(GUEST_TOKEN_HEADER)
    if guest_token:
        return RateLimitIdentity(
            tier="anonymous",
            bucket_key=f"guest:{hash_token(guest_token)}",
        )

    client = request.client
    ip = client.host if client and client.host else "unknown"
    return RateLimitIdentity(
        tier="anonymous",
        bucket_key=f"ip:{hash_ip(ip)}",
    )


async def rate_limit_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    from app.core.errors import rate_limit_error_response

    if request.url.path in EXEMPT_PATHS:
        return await call_next(request)

    settings = get_settings()
    identity = resolve_rate_limit_identity(request, settings)
    limit = (
        settings.rate_limit_authenticated_per_minute
        if identity.tier == "authenticated"
        else settings.rate_limit_anonymous_per_minute
    )

    if identity.user_id is not None and settings.security_rate_limit_extensions_enabled:
        role = "member"
        try:
            role = await _get_rate_limit_role(identity.user_id, settings)
        except Exception:
            logger.exception("Unable to resolve rate-limit role; using member")
        multiplier = settings.security_role_rate_limit_multipliers.get(role, 1.0)
        limit = max(1, math.floor(limit * max(multiplier, 0.0)))

    retry_after = await check_rate_limit_bucket(identity.bucket_key, limit)
    if retry_after is not None:
        logger.warning(
            "HTTP rate limit exceeded",
            caller_tier=identity.tier,
        )
        return rate_limit_error_response(retry_after)

    return await call_next(request)
