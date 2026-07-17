"""Authentication router: Google login exchanged for an app JWT.

Dependencies are provided via ``Depends`` so tests can override the Google
verifier and user store with in-memory fakes (no network, no database).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.caller import GUEST_TOKEN_HEADER
from app.core.config import Settings, get_settings
from app.core.security import AuthConfigError
from app.db.identity import SqlGuestStore, SqlUserStore
from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser, GoogleLoginRequest, TokenResponse
from app.services.auth_service import (
    AuthService,
    GoogleIDTokenVerifier,
    GoogleTokenVerifier,
    GuestLinkStore,
    UserStore,
)

router = APIRouter()


def get_google_verifier(
    settings: Settings = Depends(get_settings),
) -> GoogleTokenVerifier:
    if not settings.google_client_id:
        raise AuthConfigError()
    return GoogleIDTokenVerifier(settings.google_client_id)


def get_user_store(session=Depends(get_db_session)) -> UserStore:
    return SqlUserStore(session)


def get_guest_store(session=Depends(get_db_session)) -> GuestLinkStore:
    return SqlGuestStore(session)


def get_auth_service(
    verifier: GoogleTokenVerifier = Depends(get_google_verifier),
    store: UserStore = Depends(get_user_store),
    guest_store: GuestLinkStore = Depends(get_guest_store),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    return AuthService(
        verifier=verifier,
        store=store,
        settings=settings,
        guest_store=guest_store,
    )


@router.post("/api/auth/google", response_model=TokenResponse)
async def login_with_google(
    payload: GoogleLoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    guest_token = request.headers.get(GUEST_TOKEN_HEADER)
    result = await service.login_with_google(payload.id_token, guest_token)
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        user=AuthenticatedUser(
            id=result.user.id,
            email=result.user.email,
            display_name=result.user.display_name,
            picture_url=result.user.picture_url,
        ),
    )
