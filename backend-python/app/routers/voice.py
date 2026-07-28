"""Voice WebSocket router — bidirectional voice transport (Epic 04 Phase 7).

Phase 7 wires session management, STT/TTS pipelines, and stream framing.
``transcript_final`` triggers a stub chat turn; full ``UnifiedChatService``
integration lands in Phase 8.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass

from typing import cast

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import (
    SttError,
    VoiceAuthError,
    VoiceSessionError,
)
from app.ai.voice.interrupt import InterruptController
from app.ai.voice.interfaces import SttProvider, TtsProvider
from app.ai.voice.providers.openai_voice import OpenAiVoiceAdapter
from app.ai.voice.session import VoiceSessionManager
from app.ai.voice.streaming import ServerMessage, VoiceStreamBridge
from app.ai.voice.stt import SttPipeline
from app.ai.voice.tts import TtsPipeline
from app.core.caller import CallerContext, resolve_guest_caller
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import InvalidAccessTokenError, decode_access_token
from app.db.chat import SqlChatStore
from app.db.identity import SqlGuestStore
from app.db.session import get_db_session
from app.schemas.voice import (
    AssistantTextDeltaMessage,
    AudioInMessage,
    AudioOutMessage,
    ErrorMessage,
    HeartbeatMessage,
    SessionClosedMessage,
    SessionEndMessage,
    SessionStartedMessage,
    TranscriptFinalMessage,
    TurnCompleteMessage,
)

logger = get_logger(__name__)


@dataclass
class VoiceConnectionServices:
    """Per-connection voice dependencies (overridable in tests)."""

    config: VoiceConfig
    session_manager: VoiceSessionManager
    stt_provider: SttProvider
    tts_provider: TtsProvider


def _create_voice_provider(
    settings: Settings, config: VoiceConfig
) -> OpenAiVoiceAdapter:
    if settings.voice_provider != "openai":
        raise VoiceSessionError(
            f"Unsupported voice provider: {settings.voice_provider}",
            code="unsupported_voice_provider",
        )
    if not settings.openai_api_key:
        raise VoiceSessionError(
            "OpenAI API key is required for voice",
            code="voice_provider_not_configured",
        )
    return OpenAiVoiceAdapter(settings.openai_api_key, config)


def build_voice_connection_services(
    settings: Settings,
    session: AsyncSession,
) -> VoiceConnectionServices:
    """Construct default voice services for a WebSocket connection."""
    config = VoiceConfig.from_settings(settings)
    provider = _create_voice_provider(settings, config)
    chat_store = SqlChatStore(session)
    return VoiceConnectionServices(
        config=config,
        session_manager=VoiceSessionManager(config, chat_store),
        stt_provider=provider,
        tts_provider=provider,
    )


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


class _WebSocketHeaderAdapter:
    """Expose WebSocket headers/client for guest caller resolution."""

    def __init__(self, websocket: WebSocket) -> None:
        self.headers = websocket.headers
        self.client = websocket.client


async def resolve_websocket_caller(
    websocket: WebSocket,
    settings: Settings,
    session: AsyncSession,
) -> CallerContext:
    """Resolve authenticated user or guest from WebSocket headers."""
    token = _extract_bearer_token(websocket.headers.get("authorization"))
    if token is not None:
        try:
            user_id = decode_access_token(token, settings=settings)
            return CallerContext.for_user(user_id)
        except InvalidAccessTokenError:
            pass

    return await resolve_guest_caller(
        cast(Request, _WebSocketHeaderAdapter(websocket)),
        SqlGuestStore(session),
    )


async def _send_message(
    websocket: WebSocket,
    bridge: VoiceStreamBridge,
    message: ServerMessage,
) -> None:
    await websocket.send_text(bridge.encode_message(message))


async def _run_stub_chat_turn(
    *,
    websocket: WebSocket,
    bridge: VoiceStreamBridge,
    services: VoiceConnectionServices,
    interrupt: InterruptController,
    voice_session_id: str,
    transcript: str,
) -> None:
    """Phase 7 stub: echo transcript as assistant text + TTS audio.

    Full ``UnifiedChatService.stream_execute()`` wiring replaces this in Phase 8.
    """
    stub_text = f"You said: {transcript}"
    interrupt.set_turn_active(voice_session_id, True)
    tts_pipeline = TtsPipeline(services.tts_provider, services.config)
    interrupt.register_tts_pipeline(voice_session_id, tts_pipeline)

    async def turn_worker() -> None:
        await _send_message(
            websocket,
            bridge,
            AssistantTextDeltaMessage(text=stub_text),
        )

        seq = 0

        async def text_chunks() -> AsyncIterator[str]:
            yield stub_text

        async for audio_chunk in tts_pipeline.process(text_chunks()):
            seq += 1
            await _send_message(
                websocket,
                bridge,
                AudioOutMessage(
                    seq=seq,
                    payload_b64=bridge.encode_audio_payload(audio_chunk),
                ),
            )

        await _send_message(websocket, bridge, TurnCompleteMessage())

    turn_task = asyncio.create_task(turn_worker())
    interrupt.register_llm_task(voice_session_id, turn_task)
    services.session_manager.register_task(voice_session_id, turn_task)

    try:
        await turn_task
    except asyncio.CancelledError:
        pass
    finally:
        interrupt.set_turn_active(voice_session_id, False)


async def _process_stt_utterance(
    *,
    websocket: WebSocket,
    bridge: VoiceStreamBridge,
    services: VoiceConnectionServices,
    interrupt: InterruptController,
    voice_session_id: str,
    audio_buffer: bytes,
) -> None:
    """Run STT on a completed utterance and trigger the stub chat turn."""
    stt_pipeline = SttPipeline(services.stt_provider, services.config)
    interrupt.register_stt_pipeline(voice_session_id, stt_pipeline)

    async def audio_chunks() -> AsyncIterable[bytes]:
        yield audio_buffer

    transcript_text: str | None = None
    async for event in stt_pipeline.process(audio_chunks(), final=True):
        if event.type == "transcript_final":
            transcript_text = event.text
            await _send_message(
                websocket,
                bridge,
                TranscriptFinalMessage(text=event.text),
            )

    if transcript_text is None:
        return

    await _run_stub_chat_turn(
        websocket=websocket,
        bridge=bridge,
        services=services,
        interrupt=interrupt,
        voice_session_id=voice_session_id,
        transcript=transcript_text,
    )


async def _handle_voice_session(
    websocket: WebSocket,
    *,
    session_id: str,
    caller: CallerContext,
    services: VoiceConnectionServices,
) -> None:
    bridge = VoiceStreamBridge(services.config)
    interrupt = InterruptController()
    bridge.bind_interrupt_controller(interrupt)

    try:
        voice_session = await services.session_manager.create(session_id, caller)
    except VoiceAuthError:
        await _send_message(
            websocket,
            bridge,
            ErrorMessage(
                code="voice_auth_required",
                message="Voice sessions require an authenticated user",
            ),
        )
        return
    except VoiceSessionError as exc:
        await _send_message(
            websocket,
            bridge,
            ErrorMessage(
                code=exc.code or "voice_session_error",
                message=str(exc),
            ),
        )
        return

    voice_session_id = voice_session.voice_session_id
    logger.info(
        "Voice session started",
        voice_session_id=voice_session_id,
        chat_session_id=str(voice_session.session_id),
        voice_event_type="session_started",
    )

    await _send_message(
        websocket,
        bridge,
        SessionStartedMessage(
            voice_session_id=voice_session_id,
            audio_format="pcm16_24k_mono",
        ),
    )

    audio_buffer = bytearray()

    async def cleanup() -> None:
        interrupt.clear_session(voice_session_id)

    services.session_manager.register_cleanup(voice_session_id, cleanup)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message, interrupted = await bridge.decode_and_handle_barge_in(
                    raw,
                    voice_session_id=voice_session_id,
                )
            except VoiceSessionError as exc:
                await _send_message(
                    websocket,
                    bridge,
                    ErrorMessage(
                        code="invalid_message",
                        message=str(exc),
                    ),
                )
                continue

            if interrupted is not None:
                logger.info(
                    "Voice turn interrupted",
                    voice_session_id=voice_session_id,
                    voice_interrupted=True,
                    voice_event_type="interrupted",
                )
                await _send_message(websocket, bridge, interrupted)
                audio_buffer.clear()
                continue

            if isinstance(message, HeartbeatMessage):
                services.session_manager.record_heartbeat(voice_session_id)
                await _send_message(websocket, bridge, message)
                continue

            if isinstance(message, SessionEndMessage):
                break

            if isinstance(message, AudioInMessage):
                services.session_manager.record_activity(voice_session_id)
                chunk = bridge.decode_audio_payload(message.payload_b64)
                audio_buffer.extend(chunk)

                if not message.final:
                    continue

                buffer_snapshot = bytes(audio_buffer)
                audio_buffer.clear()

                try:
                    await _process_stt_utterance(
                        websocket=websocket,
                        bridge=bridge,
                        services=services,
                        interrupt=interrupt,
                        voice_session_id=voice_session_id,
                        audio_buffer=buffer_snapshot,
                    )
                except SttError as exc:
                    code = exc.code or "stt_error"
                    logger.warning(
                        "STT processing failed",
                        voice_session_id=voice_session_id,
                        voice_error_code=code,
                        voice_event_type="error",
                    )
                    await _send_message(
                        websocket,
                        bridge,
                        ErrorMessage(code=code, message=str(exc)),
                    )
                continue

    except WebSocketDisconnect:
        logger.info(
            "Voice WebSocket disconnected",
            voice_session_id=voice_session_id,
            voice_event_type="disconnect",
        )
    finally:
        closed = await services.session_manager.teardown(
            voice_session_id,
            reason="client_end",
        )
        if closed:
            reason = voice_session.close_reason or "client_end"
            logger.info(
                "Voice session closed",
                voice_session_id=voice_session_id,
                voice_session_closed_reason=reason,
                voice_event_type="session_closed",
            )
            await _send_message(
                websocket,
                bridge,
                SessionClosedMessage(reason=reason),
            )


def create_voice_router(
    _settings: Settings,
    *,
    services_builder: Callable[[Settings, AsyncSession], VoiceConnectionServices]
    | None = None,
) -> APIRouter:
    """Build the voice WebSocket router (mount only when ``VOICE_ENABLED``)."""
    router = APIRouter()

    @router.websocket("/api/voice/ws")
    async def voice_websocket(
        websocket: WebSocket,
        session_id: str,
        app_settings: Settings = Depends(get_settings),
        db_session: AsyncSession = Depends(get_db_session),
    ) -> None:
        if not app_settings.voice_enabled:
            await websocket.close(code=1008, reason="Voice is disabled")
            return

        await websocket.accept()

        bridge = VoiceStreamBridge(VoiceConfig.from_settings(app_settings))

        if not session_id.strip():
            await _send_message(
                websocket,
                bridge,
                ErrorMessage(
                    code="invalid_session_id",
                    message="session_id query parameter is required",
                ),
            )
            await websocket.close()
            return

        try:
            uuid.UUID(session_id)
        except ValueError:
            await _send_message(
                websocket,
                bridge,
                ErrorMessage(
                    code="invalid_session_id",
                    message="session_id must be a valid UUID",
                ),
            )
            await websocket.close()
            return

        caller = await resolve_websocket_caller(websocket, app_settings, db_session)
        if not caller.is_authenticated:
            await _send_message(
                websocket,
                bridge,
                ErrorMessage(
                    code="voice_auth_required",
                    message="Voice sessions require an authenticated user",
                ),
            )
            await websocket.close()
            return

        if services_builder is not None:
            services = services_builder(app_settings, db_session)
        else:
            services = build_voice_connection_services(app_settings, db_session)

        await _handle_voice_session(
            websocket,
            session_id=session_id,
            caller=caller,
            services=services,
        )

    return router
