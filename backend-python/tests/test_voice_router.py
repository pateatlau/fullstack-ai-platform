"""Voice WebSocket router tests (Epic 04 Phase 7)."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterable, AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from app.ai.voice.config import VoiceConfig
from app.ai.voice.session import VoiceSessionManager
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.main import app as main_app
from app.providers.capabilities import capabilities_by_provider
from app.routers.voice import VoiceConnectionServices, create_voice_router
from tests.fakes import FakeChatStore

pytestmark = pytest.mark.anyio


class FakeSttProvider:
    """Deterministic STT fake for router tests."""

    def __init__(self, transcript: str = "hello world") -> None:
        self._transcript = transcript

    async def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        async for _chunk in audio_chunks:
            pass
        yield self._transcript


class FakeTtsProvider:
    """Deterministic TTS fake for router tests."""

    def __init__(self, audio_data: bytes = b"fake_audio_chunk") -> None:
        self._audio_data = audio_data

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        async for _text in text_chunks:
            pass
        yield self._audio_data


def _build_voice_test_app(
    chat_store: FakeChatStore,
    *,
    stt: FakeSttProvider | None = None,
    tts: FakeTtsProvider | None = None,
) -> FastAPI:
    config = VoiceConfig()
    stt_provider = stt or FakeSttProvider()
    tts_provider = tts or FakeTtsProvider()

    def services_builder(
        _settings: Settings, _session: object
    ) -> VoiceConnectionServices:
        return VoiceConnectionServices(
            config=config,
            session_manager=VoiceSessionManager(config, chat_store),
            stt_provider=stt_provider,
            tts_provider=tts_provider,
        )

    test_app = FastAPI()
    test_app.include_router(
        create_voice_router(get_settings(), services_builder=services_builder)
    )
    return test_app


def _auth_header(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


async def test_voice_endpoint_unavailable_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "false")
    get_settings.cache_clear()

    try:
        with TestClient(main_app) as client:
            with pytest.raises(Exception):
                with client.websocket_connect(
                    f"/api/voice/ws?session_id={uuid.uuid4()}"
                ):
                    pass
    finally:
        get_settings.cache_clear()


async def test_guest_receives_voice_auth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "true")
    get_settings.cache_clear()

    chat_store = FakeChatStore()
    test_app = _build_voice_test_app(chat_store)
    session_id = uuid.uuid4()

    try:
        with TestClient(test_app) as client:
            with client.websocket_connect(
                f"/api/voice/ws?session_id={session_id}"
            ) as ws:
                payload = json.loads(ws.receive_text())
    finally:
        get_settings.cache_clear()

    assert payload == {
        "type": "error",
        "code": "voice_auth_required",
        "message": "Voice sessions require an authenticated user",
    }


async def test_handshake_and_stub_turn_with_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "true")
    get_settings.cache_clear()

    user_id = uuid.uuid4()
    chat_store = FakeChatStore()
    chat_session = await chat_store.create_session(user_id=user_id, title="Voice")
    test_app = _build_voice_test_app(chat_store)

    audio_b64_first = base64.b64encode(b"\x00" * 4096).decode("ascii")
    audio_b64_second = base64.b64encode(b"\x00" * 704).decode("ascii")

    try:
        with TestClient(test_app) as client:
            with client.websocket_connect(
                f"/api/voice/ws?session_id={chat_session.id}",
                headers=_auth_header(user_id),
            ) as ws:
                started = json.loads(ws.receive_text())
                assert started["type"] == "session_started"
                assert started["audio_format"] == "pcm16_24k_mono"
                assert started["voice_session_id"]

                ws.send_text(
                    json.dumps(
                        {
                            "type": "audio_in",
                            "seq": 0,
                            "payload_b64": audio_b64_first,
                            "final": False,
                        }
                    )
                )
                ws.send_text(
                    json.dumps(
                        {
                            "type": "audio_in",
                            "seq": 1,
                            "payload_b64": audio_b64_second,
                            "final": True,
                        }
                    )
                )

                transcript = json.loads(ws.receive_text())
                assert transcript == {
                    "type": "transcript_final",
                    "text": "hello world",
                }

                assistant_delta = json.loads(ws.receive_text())
                assert assistant_delta == {
                    "type": "assistant_text_delta",
                    "text": "You said: hello world",
                }

                audio_out = json.loads(ws.receive_text())
                assert audio_out["type"] == "audio_out"
                assert audio_out["seq"] == 1
                assert audio_out["payload_b64"] == base64.b64encode(
                    b"fake_audio_chunk"
                ).decode("ascii")

                turn_complete = json.loads(ws.receive_text())
                assert turn_complete["type"] == "turn_complete"

                ws.send_text(json.dumps({"type": "session_end"}))
                closed = json.loads(ws.receive_text())
                assert closed["type"] == "session_closed"
                assert closed["reason"] == "client_end"
    finally:
        get_settings.cache_clear()


async def test_heartbeat_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "true")
    get_settings.cache_clear()

    user_id = uuid.uuid4()
    chat_store = FakeChatStore()
    chat_session = await chat_store.create_session(user_id=user_id, title="Voice")
    test_app = _build_voice_test_app(chat_store)

    try:
        with TestClient(test_app) as client:
            with client.websocket_connect(
                f"/api/voice/ws?session_id={chat_session.id}",
                headers=_auth_header(user_id),
            ) as ws:
                ws.receive_text()
                ws.send_text(json.dumps({"type": "heartbeat", "ts": 1.0}))
                heartbeat = json.loads(ws.receive_text())
                assert heartbeat == {"type": "heartbeat", "ts": 1.0}
                ws.send_text(json.dumps({"type": "session_end"}))
                ws.receive_text()
    finally:
        get_settings.cache_clear()


async def test_health_includes_voice_enabled_and_audio_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_ENABLED", "true")
    get_settings.cache_clear()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=main_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/health")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["voice_enabled"] is True
    assert body["capabilities"]["by_provider"]["openai"]["supports_audio"] is True
    assert body["capabilities"]["by_provider"]["gemini"]["supports_audio"] is False


async def test_capabilities_audio_off_when_voice_disabled() -> None:
    payload = capabilities_by_provider(voice_enabled=False)
    for provider in ("openai", "gemini", "groq", "anthropic"):
        assert payload[provider]["supports_audio"] is False
