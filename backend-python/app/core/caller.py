"""Caller resolution: turn each request into a typed :class:`CallerContext`.

A request is resolved to exactly one caller identity (plan Section 3.3):

- A valid app JWT resolves to an authenticated caller with a ``user_id``.
- Otherwise the caller is an anonymous guest: an existing guest is resolved from
  the opaque guest-continuity token, or a fresh guest identity + token is issued.

The server stores only the SHA-256 hash of the guest token; the raw token is
returned to the client (via ``CallerContext.issued_guest_token``) when a new one
is minted, so the client can present it on subsequent requests (Section 2.3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import (
    InvalidAccessTokenError,
    decode_access_token,
    generate_guest_token,
    hash_ip,
    hash_token,
)
from app.db.identity import SqlGuestStore
from app.db.models import GuestIdentity
from app.db.session import get_db_session

CallerKind = Literal["user", "guest"]

#: Header carrying the opaque guest-continuity token issued by the server.
GUEST_TOKEN_HEADER = "X-Guest-Token"


@dataclass(frozen=True)
class CallerContext:
    """Typed identity handed to downstream chat logic."""

    kind: CallerKind
    user_id: uuid.UUID | None = None
    guest_id: uuid.UUID | None = None
    #: Set only when a brand-new guest token was minted this request; the
    #: caller must return it to the client so continuity is preserved.
    issued_guest_token: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.kind == "user"

    @classmethod
    def for_user(cls, user_id: uuid.UUID) -> "CallerContext":
        return cls(kind="user", user_id=user_id)

    @classmethod
    def anonymous(
        cls,
        guest_id: uuid.UUID | None = None,
        issued_guest_token: str | None = None,
    ) -> "CallerContext":
        return cls(
            kind="guest",
            guest_id=guest_id,
            issued_guest_token=issued_guest_token,
        )


class GuestStore(Protocol):
    async def get_by_token_hash(self, token_hash: str) -> GuestIdentity | None: ...

    async def create(
        self, *, token_hash: str, created_ip_hash: str | None = None
    ) -> GuestIdentity: ...

    async def touch(self, guest_id: uuid.UUID) -> None: ...


def _extract_bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _client_ip_hash(request: Request) -> str | None:
    client = request.client
    if client is None or not client.host:
        return None
    return hash_ip(client.host)


async def resolve_guest_caller(request: Request, store: GuestStore) -> CallerContext:
    """Resolve an existing guest from its token, or issue a new guest identity.

    Never trusts a raw client token directly: it is hashed and matched against
    ``guest_identities.token_hash``. An absent/unknown token yields a fresh
    identity and a newly issued token (plan Sections 2.3, 5.1).
    """
    raw_token = request.headers.get(GUEST_TOKEN_HEADER)
    if raw_token:
        existing = await store.get_by_token_hash(hash_token(raw_token))
        if existing is not None:
            await store.touch(existing.id)
            return CallerContext.anonymous(guest_id=existing.id)

    new_token = generate_guest_token()
    guest = await store.create(
        token_hash=hash_token(new_token),
        created_ip_hash=_client_ip_hash(request),
    )
    return CallerContext.anonymous(guest_id=guest.id, issued_guest_token=new_token)


async def get_current_caller(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> CallerContext:
    """Resolve the request to an authenticated user or an anonymous guest.

    An invalid/expired app JWT is treated as an anonymous caller rather than an
    error, because guest access is a valid MVP tier (plan Section 3.3). The
    frontend reacquires a Google credential when its app JWT expires.
    """
    token = _extract_bearer_token(request)
    if token is not None:
        try:
            user_id = decode_access_token(token, settings=settings)
            return CallerContext.for_user(user_id)
        except InvalidAccessTokenError:
            pass  # Fall through to anonymous/guest resolution.

    return await resolve_guest_caller(request, SqlGuestStore(session))
