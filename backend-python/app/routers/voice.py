"""Voice WebSocket endpoint (flag-guarded when mounted from ``app.main``)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.ai.deps import (
    get_interrupt_controller,
    get_stt_provider,
    get_tts_provider,
    get_voice_session_manager,
)
from app.ai.voice.config import VoiceConfig
from app.ai.voice.chat_bridge import VoiceChatBridge
from app.ai.voice.exceptions import SttError, VoiceAuthError, VoiceSessionError
from app.ai.voice.interrupt import InterruptController
from app.ai.voice.interfaces import SttProvider, TtsProvider
from app.ai.voice.session import ManagedVoiceSession, VoiceSessionManager
from app.ai.voice.stt import SttPipeline
from app.ai.voice.streaming import VoiceStreamBridge
from app.ai.voice.tts import TtsPipeline
from app.core.caller import CallerContext, extract_bearer_token
from app.core.config import Settings, get_settings
from app.core.logging import bind_context, get_logger
from app.core.security import InvalidAccessTokenError, decode_access_token
from app.db.session import get_db_session
from app.routers.chat import get_chat_service, get_unified_chat_service
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema, ProviderName
from app.schemas.voice import (
    AudioInMessage,
    ErrorMessage,
    HeartbeatMessage,
    InterruptMessage,
    SessionClosedMessage,
    SessionEndMessage,
    SessionStartedMessage,
    TranscriptFinalMessage,
    TranscriptPartialMessage,
)
from app.services.chat_service import ChatService, ChatServiceError
from app.services.unified_chat_service import UnifiedChatService
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


@dataclass
class VoiceConnectionServices:
    """Per-connection voice dependencies (overridable in tests)."""

    config: VoiceConfig
    session_manager: VoiceSessionManager
    stt_provider: SttProvider
    tts_provider: TtsProvider
    interrupt: InterruptController
    unified_service: UnifiedChatService
    chat_service: ChatService


class _WebSocketDisconnectProxy:
    """Minimal ``Request`` stand-in for ``is_disconnected()`` checks in chat streams."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def is_disconnected(self) -> bool:
        return self._websocket.client_state != WebSocketState.CONNECTED


@dataclass
class VoiceTurnOptions:
    """Per-connection chat toggles forwarded into ``UnifiedChatService``."""

    use_web_search: bool = False
    use_documents: bool = False
    provider: ProviderName | None = None
    model: str | None = None


@dataclass
class _UtteranceState:
    """Buffered inbound audio for one user utterance."""

    chunks: list[bytes] = field(default_factory=list)
    final_received: bool = False


async def _send_error_and_close(
    websocket: WebSocket,
    *,
    bridge: VoiceStreamBridge,
    code: str,
    message: str,
    close_code: int = 1008,
) -> None:
    if websocket.client_state == WebSocketState.CONNECTING:
        await websocket.accept()
    if websocket.client_state == WebSocketState.CONNECTED:
        await websocket.send_text(
            bridge.encode_message(ErrorMessage(code=code, message=message))
        )
        await websocket.close(code=close_code, reason=code)


class VoiceWebSocketHandler:
    """Orchestrates one bidirectional voice WebSocket connection."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        settings: Settings,
        session_manager: VoiceSessionManager,
        stt_provider: SttProvider,
        tts_provider: TtsProvider,
        interrupt: InterruptController,
        unified_service: UnifiedChatService,
        chat_service: ChatService,
        caller: CallerContext,
        chat_session_id: uuid.UUID,
        turn_options: VoiceTurnOptions,
    ) -> None:
        self._websocket = websocket
        self._settings = settings
        self._session_manager = session_manager
        self._stt_provider = stt_provider
        self._tts_provider = tts_provider
        self._interrupt = interrupt
        self._unified_service = unified_service
        self._chat_service = chat_service
        self._caller = caller
        self._chat_session_id = chat_session_id
        self._turn_options = turn_options

        voice_config = VoiceConfig.from_settings(settings)
        self._stream_bridge = VoiceStreamBridge(
            voice_config, interrupt_controller=interrupt
        )
        self._utterance = _UtteranceState()
        self._managed: ManagedVoiceSession | None = None
        self._running = True

    async def run(self) -> None:
        await self._websocket.accept()
        try:
            self._managed = await self._session_manager.create(
                self._chat_session_id, self._caller
            )
        except VoiceAuthError:
            await _send_error_and_close(
                self._websocket,
                bridge=self._stream_bridge,
                code="voice_auth_required",
                message="Voice sessions require an authenticated user",
            )
            return
        except VoiceSessionError as exc:
            await _send_error_and_close(
                self._websocket,
                bridge=self._stream_bridge,
                code=exc.code or "voice_session_error",
                message=str(exc),
            )
            return

        voice_session_id = self._managed.voice_session_id
        bind_context(
            voice_session_id=voice_session_id,
            chat_session_id=str(self._chat_session_id),
        )
        logger.info(
            "Voice session started",
            voice_session_id=voice_session_id,
            chat_session_id=str(self._chat_session_id),
        )

        await self._send_json(
            self._stream_bridge.encode_message(
                SessionStartedMessage(
                    voice_session_id=voice_session_id,
                    audio_format="pcm16_24k_mono",
                )
            )
        )

        expire_task = asyncio.create_task(self._expire_loop(voice_session_id))
        self._session_manager.register_task(voice_session_id, expire_task)

        try:
            while self._running and self._managed.is_active:
                try:
                    data = await self._websocket.receive_text()
                except WebSocketDisconnect:
                    break

                try:
                    (
                        message,
                        interrupted,
                    ) = await self._stream_bridge.decode_and_handle_barge_in(
                        data,
                        voice_session_id=voice_session_id,
                    )
                except VoiceSessionError as exc:
                    await self._send_json(
                        self._stream_bridge.encode_message(
                            ErrorMessage(
                                code="invalid_message",
                                message=str(exc),
                            )
                        )
                    )
                    continue

                if interrupted is not None:
                    await self._send_json(
                        self._stream_bridge.encode_message(interrupted)
                    )
                    self._utterance = _UtteranceState()

                if isinstance(message, SessionEndMessage):
                    await self._session_manager.teardown(
                        voice_session_id, reason="client_end"
                    )
                    await self._send_json(
                        self._stream_bridge.encode_message(
                            SessionClosedMessage(reason="client_end")
                        )
                    )
                    break

                if isinstance(message, HeartbeatMessage):
                    self._session_manager.record_heartbeat(voice_session_id)
                    await self._send_json(
                        self._stream_bridge.encode_message(
                            HeartbeatMessage(ts=message.ts)
                        )
                    )
                    continue

                if isinstance(message, InterruptMessage):
                    continue

                if isinstance(message, AudioInMessage):
                    await self._handle_audio_in(message, voice_session_id)
        finally:
            expire_task.cancel()
            await asyncio.gather(expire_task, return_exceptions=True)
            if self._managed is not None and self._managed.is_active:
                await self._session_manager.teardown(
                    self._managed.voice_session_id, reason="disconnect"
                )
            self._interrupt.clear_session(voice_session_id)

    async def _expire_loop(self, voice_session_id: str) -> None:
        while self._running:
            await asyncio.sleep(5)
            expired = await self._session_manager.expire_stale_sessions()
            for expired_id, reason in expired:
                if expired_id == voice_session_id:
                    await self._send_json(
                        self._stream_bridge.encode_message(
                            SessionClosedMessage(reason=reason)
                        )
                    )
                    self._running = False
                    await self._websocket.close(code=1000, reason=reason)
                    return

    async def _handle_audio_in(
        self,
        message: AudioInMessage,
        voice_session_id: str,
    ) -> None:
        self._session_manager.record_activity(voice_session_id)
        audio_bytes = self._stream_bridge.decode_audio_payload(message.payload_b64)
        self._utterance.chunks.append(audio_bytes)
        if message.final:
            self._utterance.final_received = True

        if not self._utterance.final_received:
            return

        voice_config = VoiceConfig.from_settings(self._settings)
        stt_pipeline = SttPipeline(self._stt_provider, voice_config)
        self._interrupt.register_stt_pipeline(voice_session_id, stt_pipeline)

        async def audio_iter() -> AsyncIterator[bytes]:
            for chunk in self._utterance.chunks:
                yield chunk

        try:
            stt_task = asyncio.create_task(self._run_stt(stt_pipeline, audio_iter()))
            self._interrupt.register_stt_task(voice_session_id, stt_task)
            transcript_text = await stt_task
        except SttError as exc:
            error_code = exc.code or "stt_error"
            await self._send_json(
                self._stream_bridge.encode_message(
                    ErrorMessage(code=error_code, message=str(exc))
                )
            )
            self._utterance = _UtteranceState()
            return
        except asyncio.CancelledError:
            self._utterance = _UtteranceState()
            return

        self._utterance = _UtteranceState()
        if not transcript_text:
            return

        await self._send_json(
            self._stream_bridge.encode_message(
                TranscriptFinalMessage(text=transcript_text)
            )
        )
        await self._run_chat_turn(transcript_text, voice_session_id)

    async def _run_stt(
        self,
        stt_pipeline: SttPipeline,
        audio_iter: AsyncIterator[bytes],
    ) -> str:
        transcript_text = ""
        async for event in stt_pipeline.process(audio_iter, final=True):
            if event.type == "transcript_partial":
                await self._send_json(
                    self._stream_bridge.encode_message(
                        TranscriptPartialMessage(
                            text=event.text,
                            stability=event.stability,
                        )
                    )
                )
            elif event.type == "transcript_final":
                transcript_text = event.text
        return transcript_text

    async def _run_chat_turn(self, transcript_text: str, voice_session_id: str) -> None:
        request = ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content=transcript_text)],
            session_id=self._chat_session_id,
            use_web_search=self._turn_options.use_web_search,
            use_documents=self._turn_options.use_documents,
            provider=self._turn_options.provider,
            model=self._turn_options.model,
        )

        try:
            prep = await self._chat_service.prepare_stream(request, self._caller)
        except ChatServiceError as exc:
            await self._send_json(
                self._stream_bridge.encode_message(
                    ErrorMessage(code=exc.code, message=exc.message)
                )
            )
            return

        http_request = _WebSocketDisconnectProxy(self._websocket)
        tts_pipeline = TtsPipeline(
            self._tts_provider,
            VoiceConfig.from_settings(self._settings),
        )
        chat_bridge = VoiceChatBridge(
            stream_bridge=self._stream_bridge,
            tts_pipeline=tts_pipeline,
            interrupt=self._interrupt,
            voice_session_id=voice_session_id,
            send_json=self._send_json,
        )

        turn_task = asyncio.create_task(
            chat_bridge.run_turn(
                unified_service=self._unified_service,
                chat_service=self._chat_service,
                request=request,
                http_request=http_request,
                caller=self._caller,
                prep=prep,
            )
        )
        self._interrupt.register_llm_task(voice_session_id, turn_task)

        try:
            await turn_task
        except asyncio.CancelledError:
            pass

    async def _send_json(self, payload: str) -> None:
        if self._websocket.client_state == WebSocketState.CONNECTED:
            await self._websocket.send_text(payload)


def _resolve_voice_bearer_token(websocket: WebSocket) -> str | None:
    """Resolve JWT from Authorization header, or ``access_token`` query param for browsers."""
    bearer = extract_bearer_token(websocket.headers.get("authorization"))
    if bearer is not None:
        return bearer
    query_token = websocket.query_params.get("access_token")
    if query_token:
        return query_token
    return None


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
        session_id: uuid.UUID = Query(..., description="Chat session id to attach"),
        use_web_search: bool = Query(default=False),
        use_documents: bool = Query(default=False),
        provider: ProviderName | None = Query(default=None),
        model: str | None = Query(default=None),
        settings: Settings = Depends(get_settings),
        db_session: AsyncSession = Depends(get_db_session),
        session_manager: VoiceSessionManager = Depends(get_voice_session_manager),
        stt_provider: SttProvider = Depends(get_stt_provider),
        tts_provider: TtsProvider = Depends(get_tts_provider),
        interrupt: InterruptController = Depends(get_interrupt_controller),
        unified_service: UnifiedChatService = Depends(get_unified_chat_service),
        chat_service: ChatService = Depends(get_chat_service),
    ) -> None:
        """Bidirectional voice channel for authenticated users."""
        if not settings.voice_enabled:
            await websocket.close(code=1008, reason="voice_disabled")
            return

        voice_config = VoiceConfig.from_settings(settings)
        bridge = VoiceStreamBridge(voice_config, interrupt_controller=interrupt)

        bearer = _resolve_voice_bearer_token(websocket)
        if bearer is None:
            await _send_error_and_close(
                websocket,
                bridge=bridge,
                code="voice_auth_required",
                message="Voice sessions require an authenticated user",
            )
            return

        try:
            user_id = decode_access_token(bearer, settings=settings)
        except InvalidAccessTokenError:
            await _send_error_and_close(
                websocket,
                bridge=bridge,
                code="voice_auth_required",
                message="Voice sessions require an authenticated user",
            )
            return

        caller = CallerContext.for_user(user_id)

        if services_builder is not None:
            services = services_builder(settings, db_session)
        else:
            services = VoiceConnectionServices(
                config=voice_config,
                session_manager=session_manager,
                stt_provider=stt_provider,
                tts_provider=tts_provider,
                interrupt=interrupt,
                unified_service=unified_service,
                chat_service=chat_service,
            )

        handler = VoiceWebSocketHandler(
            websocket=websocket,
            settings=settings,
            session_manager=services.session_manager,
            stt_provider=services.stt_provider,
            tts_provider=services.tts_provider,
            interrupt=services.interrupt,
            unified_service=services.unified_service,
            chat_service=services.chat_service,
            caller=caller,
            chat_session_id=session_id,
            turn_options=VoiceTurnOptions(
                use_web_search=use_web_search,
                use_documents=use_documents,
                provider=provider,
                model=model,
            ),
        )
        await handler.run()

    return router
