"""Tests for interrupt / barge-in handling."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterable, AsyncIterator

import pytest

from app.ai.voice.config import VoiceConfig
from app.ai.voice.interrupt import InterruptController
from app.ai.voice.stt import SttPipeline
from app.ai.voice.streaming import VoiceStreamBridge
from app.ai.voice.tts import TtsPipeline
from app.schemas.voice import AudioInMessage, InterruptMessage

pytestmark = pytest.mark.anyio


def _min_pcm16_chunk() -> bytes:
    """Return ~100ms of PCM16 silence at 24 kHz mono (4800 bytes)."""
    return b"\x00" * 4800


class SlowTtsProvider:
    """TTS provider that yields slowly so cancel can race."""

    def __init__(self, chunks: int = 5, delay: float = 0.05) -> None:
        self._chunks = chunks
        self._delay = delay
        self.cancelled_mid_stream = False

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        async for _text in text_chunks:
            for _ in range(self._chunks):
                await asyncio.sleep(self._delay)
                yield b"audio"


class SlowSttProvider:
    """STT provider that yields slowly so cancel can race."""

    def __init__(self, delay: float = 0.05) -> None:
        self._delay = delay

    async def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        async for _chunk in audio_chunks:
            await asyncio.sleep(self._delay)
            yield "partial"
            await asyncio.sleep(self._delay)
            yield "hello world"


async def _slow_llm_stream() -> AsyncIterator[str]:
    """Simulate a slow upstream LLM text stream."""
    for token in ("The ", "quick ", "brown ", "fox."):
        await asyncio.sleep(0.05)
        yield token


async def _text_chunks(chunks: list[str]) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


async def _audio_chunks(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.fixture
def voice_config() -> VoiceConfig:
    return VoiceConfig()


@pytest.fixture
def controller() -> InterruptController:
    return InterruptController()


async def test_cancel_all_cancels_tts_and_llm_tasks(
    controller: InterruptController,
) -> None:
    voice_session_id = "vs-1"
    controller.set_turn_active(voice_session_id, True)

    llm_started = asyncio.Event()
    llm_cancelled = asyncio.Event()

    async def llm_worker() -> None:
        llm_started.set()
        try:
            async for _ in _slow_llm_stream():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            llm_cancelled.set()
            raise

    llm_task = asyncio.create_task(llm_worker())
    controller.register_llm_task(voice_session_id, llm_task)
    await llm_started.wait()

    await controller.cancel_all(voice_session_id)

    assert llm_task.cancelled() or llm_task.done()
    await asyncio.wait_for(llm_cancelled.wait(), timeout=1.0)
    assert controller.is_turn_active(voice_session_id) is False


async def test_interrupt_during_tts_stops_audio_and_clears_turn(
    controller: InterruptController,
    voice_config: VoiceConfig,
) -> None:
    voice_session_id = "vs-tts"
    provider = SlowTtsProvider(chunks=10, delay=0.03)
    pipeline = TtsPipeline(provider, voice_config)
    controller.register_tts_pipeline(voice_session_id, pipeline)
    controller.set_turn_active(voice_session_id, True)

    audio_received: list[bytes] = []

    async def tts_worker() -> None:
        async for chunk in pipeline.process(_text_chunks(["Hello world. More text."])):
            audio_received.append(chunk)

    tts_task = asyncio.create_task(tts_worker())
    controller.register_tts_task(voice_session_id, tts_task)

    await asyncio.sleep(0.08)
    await controller.cancel_all(voice_session_id)

    with pytest.raises(asyncio.CancelledError):
        await tts_task

    assert len(audio_received) < 10


async def test_interrupt_during_llm_stream_cancels_task(
    controller: InterruptController,
) -> None:
    voice_session_id = "vs-llm"
    controller.set_turn_active(voice_session_id, True)

    tokens: list[str] = []
    cancelled = asyncio.Event()

    async def llm_worker() -> None:
        try:
            async for token in _slow_llm_stream():
                tokens.append(token)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    llm_task = asyncio.create_task(llm_worker())
    controller.register_llm_task(voice_session_id, llm_task)

    await asyncio.sleep(0.08)
    interrupted = await controller.handle_barge_in(voice_session_id, InterruptMessage())

    assert interrupted is not None
    assert interrupted.type == "interrupted"
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    assert len(tokens) < 4
    assert controller.is_turn_active(voice_session_id) is False


async def test_audio_in_during_active_turn_triggers_barge_in(
    controller: InterruptController,
) -> None:
    voice_session_id = "vs-audio-in"
    controller.set_turn_active(voice_session_id, True)

    llm_task = asyncio.create_task(asyncio.sleep(10))
    controller.register_llm_task(voice_session_id, llm_task)

    audio_msg = AudioInMessage(seq=1, payload_b64=base64.b64encode(b"\x00").decode())
    interrupted = await controller.handle_barge_in(voice_session_id, audio_msg)

    assert interrupted is not None
    assert llm_task.cancelled() or llm_task.done()
    assert controller.is_turn_active(voice_session_id) is False


async def test_audio_in_when_turn_inactive_does_not_interrupt(
    controller: InterruptController,
) -> None:
    voice_session_id = "vs-idle"
    audio_msg = AudioInMessage(seq=1, payload_b64=base64.b64encode(b"\x00").decode())

    interrupted = await controller.handle_barge_in(voice_session_id, audio_msg)

    assert interrupted is None


async def test_session_usable_after_interrupt(
    controller: InterruptController,
    voice_config: VoiceConfig,
) -> None:
    voice_session_id = "vs-reuse"
    controller.set_turn_active(voice_session_id, True)

    llm_task = asyncio.create_task(asyncio.sleep(10))
    controller.register_llm_task(voice_session_id, llm_task)

    await controller.cancel_all(voice_session_id)
    assert controller.is_turn_active(voice_session_id) is False

    provider = SlowTtsProvider(chunks=1, delay=0.01)
    pipeline = TtsPipeline(provider, voice_config)
    controller.register_tts_pipeline(voice_session_id, pipeline)
    controller.set_turn_active(voice_session_id, True)

    audio: list[bytes] = []
    async for chunk in pipeline.process(_text_chunks(["After interrupt."])):
        audio.append(chunk)

    assert audio == [b"audio"]
    controller.assert_no_leaked_tasks(voice_session_id)


async def test_tts_pipeline_request_cancel_mid_stream(
    voice_config: VoiceConfig,
) -> None:
    provider = SlowTtsProvider(chunks=8, delay=0.03)
    pipeline = TtsPipeline(provider, voice_config)

    audio: list[bytes] = []

    async def consume() -> None:
        async for chunk in pipeline.process(
            _text_chunks(["First. ", "Second sentence keeps going."])
        ):
            audio.append(chunk)
            if len(audio) == 2:
                pipeline.request_cancel()

    await consume()
    assert len(audio) <= 4


async def test_stt_pipeline_request_cancel_stops_transcription(
    voice_config: VoiceConfig,
) -> None:
    provider = SlowSttProvider(delay=0.05)
    pipeline = SttPipeline(provider, voice_config)

    texts: list[str] = []

    async def consume() -> None:
        async for event in pipeline.process(_audio_chunks([_min_pcm16_chunk()])):
            texts.append(event.text)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.06)
    pipeline.request_cancel()
    await task

    assert "hello world" not in texts


async def test_bridge_decode_and_handle_barge_in_emits_interrupted(
    voice_config: VoiceConfig,
) -> None:
    controller = InterruptController()
    bridge = VoiceStreamBridge(voice_config, controller)
    voice_session_id = "vs-bridge"

    controller.set_turn_active(voice_session_id, True)
    llm_task = asyncio.create_task(asyncio.sleep(10))
    controller.register_llm_task(voice_session_id, llm_task)

    raw = json.dumps({"type": "interrupt"})
    message, interrupted = await bridge.decode_and_handle_barge_in(
        raw, voice_session_id=voice_session_id
    )

    assert isinstance(message, InterruptMessage)
    assert interrupted is not None
    encoded = bridge.encode_message(interrupted)
    assert json.loads(encoded)["type"] == "interrupted"
    assert llm_task.cancelled() or llm_task.done()


async def test_bridge_audio_in_barge_in_when_turn_active(
    voice_config: VoiceConfig,
) -> None:
    controller = InterruptController()
    bridge = VoiceStreamBridge(voice_config, controller)
    voice_session_id = "vs-bridge-audio"

    controller.set_turn_active(voice_session_id, True)
    tts_task = asyncio.create_task(asyncio.sleep(10))
    controller.register_tts_task(voice_session_id, tts_task)

    payload_b64 = base64.b64encode(b"\x00" * 128).decode()
    raw = json.dumps(
        {"type": "audio_in", "seq": 0, "payload_b64": payload_b64, "final": False}
    )
    message, interrupted = await bridge.decode_and_handle_barge_in(
        raw, voice_session_id=voice_session_id
    )

    assert isinstance(message, AudioInMessage)
    assert interrupted is not None
    assert tts_task.cancelled() or tts_task.done()


async def test_cancel_all_is_idempotent(controller: InterruptController) -> None:
    voice_session_id = "vs-idempotent"
    await controller.cancel_all(voice_session_id)
    await controller.cancel_all(voice_session_id)
    assert controller.is_turn_active(voice_session_id) is False


async def test_clear_session_removes_interrupt_state(
    controller: InterruptController,
) -> None:
    voice_session_id = "vs-clear"
    controller.set_turn_active(voice_session_id, True)
    controller.clear_session(voice_session_id)
    assert controller.is_turn_active(voice_session_id) is False
