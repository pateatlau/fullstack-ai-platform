"""Authentication use case: Google login exchanging an ID token for an app JWT.

Flow (plan Section 3.1):
1. Verify the Google ID token (signature + expected audience) via ``google-auth``.
2. Resolve the user by ``auth_provider='google'`` and ``external_auth_id=sub``.
3. Create the user from the verified profile if none exists; otherwise refresh
   mutable profile fields when they change.
4. Issue a signed app JWT.

Verification is behind a small :class:`GoogleTokenVerifier` protocol so unit
tests can inject a fake instead of calling Google (plan Sections 8.6, 14.1).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi.concurrency import run_in_threadpool
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import Settings
from app.core.security import (
    InvalidGoogleTokenError,
    create_access_token,
    hash_token,
)
from app.db.models import GuestIdentity, User


@dataclass(frozen=True)
class GoogleClaims:
    """The verified subset of Google ID-token claims the MVP consumes."""

    sub: str
    email: str | None
    name: str | None
    picture: str | None


class GoogleTokenVerifier(Protocol):
    async def verify(self, raw_id_token: str) -> GoogleClaims: ...


class UserStore(Protocol):
    """Identity/auth persistence surface for Google users (plan Section 8.2)."""

    async def get_by_google_sub(self, sub: str) -> User | None: ...

    async def create(
        self,
        *,
        sub: str,
        email: str | None,
        name: str | None,
        picture: str | None,
    ) -> User: ...

    async def update_profile(
        self,
        user: User,
        *,
        email: str | None,
        name: str | None,
        picture: str | None,
    ) -> User: ...


class GuestLinkStore(Protocol):
    """Guest-identity surface needed to link a guest to a user (plan Section 7)."""

    async def get_by_token_hash(self, token_hash: str) -> GuestIdentity | None: ...

    async def link_to_user(self, guest_id: uuid.UUID, user_id: uuid.UUID) -> None: ...


class GoogleIDTokenVerifier:
    """Real verifier backed by ``google-auth`` (validates signature + audience)."""

    def __init__(self, client_id: str) -> None:
        self._client_id = client_id

    async def verify(self, raw_id_token: str) -> GoogleClaims:
        def _verify_sync() -> Mapping[str, Any]:
            # verify_oauth2_token checks signature, expiry, and that the token's
            # audience matches self._client_id; it raises ValueError otherwise.
            # google-auth ships no type stubs, so this member is untyped.
            verified: Mapping[str, Any] = (
                google_id_token.verify_oauth2_token(  # pyright: ignore[reportUnknownMemberType]
                    raw_id_token,
                    google_requests.Request(),
                    self._client_id,
                )
            )
            return verified

        try:
            claims = await run_in_threadpool(_verify_sync)
        except ValueError as exc:
            raise InvalidGoogleTokenError() from exc

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise InvalidGoogleTokenError()

        return GoogleClaims(
            sub=sub,
            email=claims.get("email"),
            name=claims.get("name"),
            picture=claims.get("picture"),
        )


@dataclass(frozen=True)
class LoginResult:
    user: User
    access_token: str
    expires_in: int
    linked_guest_id: uuid.UUID | None = None


class AuthService:
    """Verifies Google tokens, resolves/creates users, and issues app JWTs."""

    def __init__(
        self,
        *,
        verifier: GoogleTokenVerifier,
        store: UserStore,
        settings: Settings,
        guest_store: GuestLinkStore | None = None,
    ) -> None:
        self._verifier = verifier
        self._store = store
        self._settings = settings
        self._guest_store = guest_store

    async def login_with_google(
        self, raw_id_token: str, guest_token: str | None = None
    ) -> LoginResult:
        claims = await self._verifier.verify(raw_id_token)

        user = await self._store.get_by_google_sub(claims.sub)
        if user is None:
            user = await self._store.create(
                sub=claims.sub,
                email=claims.email,
                name=claims.name,
                picture=claims.picture,
            )
        else:
            user = await self._store.update_profile(
                user,
                email=claims.email,
                name=claims.name,
                picture=claims.picture,
            )

        linked_guest_id = await self._maybe_link_guest(guest_token, user.id)

        access_token = create_access_token(user_id=user.id, settings=self._settings)
        return LoginResult(
            user=user,
            access_token=access_token,
            expires_in=self._settings.jwt_access_token_expires_minutes * 60,
            linked_guest_id=linked_guest_id,
        )

    async def _maybe_link_guest(
        self, guest_token: str | None, user_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Link a presenting guest identity to the user (link only, plan Sections 5.8, 7).

        Ownership of existing guest sessions is intentionally NOT migrated; only
        ``guest_identities.linked_user_id`` is set.
        """
        if not guest_token or self._guest_store is None:
            return None
        guest = await self._guest_store.get_by_token_hash(hash_token(guest_token))
        if guest is None:
            return None
        await self._guest_store.link_to_user(guest.id, user_id)
        return guest.id
