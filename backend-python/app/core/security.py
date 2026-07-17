"""Application security primitives: auth errors and app JWT handling.

The app issues a stateless signed JWT (HS256) after Google verification
(plan Section 3.2). There is no sessions table, refresh token, or revocation
infrastructure in the MVP.
"""

from __future__ import annotations

import datetime
import hashlib
import secrets
import uuid

import jwt

from app.core.config import Settings


class AuthError(Exception):
    """Base auth error carrying the standard ``{error: {code, message}}`` shape."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class InvalidGoogleTokenError(AuthError):
    def __init__(self) -> None:
        super().__init__(
            code="invalid_google_token",
            message="The Google ID token could not be verified.",
            status_code=401,
        )


class InvalidAccessTokenError(AuthError):
    def __init__(self) -> None:
        super().__init__(
            code="invalid_access_token",
            message="The provided access token is invalid or expired.",
            status_code=401,
        )


class AuthConfigError(AuthError):
    def __init__(self) -> None:
        super().__init__(
            code="auth_not_configured",
            message="Authentication is not configured on the server.",
            status_code=503,
        )


def create_access_token(*, user_id: uuid.UUID, settings: Settings) -> str:
    """Issue a signed app JWT with ``sub = user_id`` and an expiry (Section 3.2)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    expires = now + datetime.timedelta(
        minutes=settings.jwt_access_token_expires_minutes
    )
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, *, settings: Settings) -> uuid.UUID:
    """Decode a valid app JWT and return the caller's ``user_id``.

    Raises ``InvalidAccessTokenError`` for any malformed/expired token or a
    missing/invalid ``sub`` claim.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError() from exc

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise InvalidAccessTokenError()
    try:
        return uuid.UUID(sub)
    except ValueError as exc:
        raise InvalidAccessTokenError() from exc


def generate_guest_token() -> str:
    """Mint an opaque, high-entropy guest continuity token (plan Section 2.3)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of an opaque token.

    The server stores only this hash; the raw token lives on the client
    (plan Sections 2.3, 2.12).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_ip(ip: str) -> str:
    """Return a SHA-256 hex digest of a client IP (no raw IPs stored, Section 2.12)."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()
