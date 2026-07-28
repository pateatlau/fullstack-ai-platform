"""OpenAI voice adapter — Whisper STT + OpenAI TTS implementation."""

import io
import wave
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

from openai import AsyncOpenAI

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import SttError, TtsError
from app.core.retry import retry_async

# PCM16 encodes one sample per 2 bytes; audio frames must never split a sample.
_PCM16_SAMPLE_BYTES = 2


class OpenAiVoiceAdapter:
    """Concrete STT/TTS adapter using OpenAI Whisper and TTS APIs."""

    def __init__(self, api_key: str, config: VoiceConfig) -> None:
        """Initialize OpenAI voice adapter.

        Args:
            api_key: OpenAI API key.
            config: Voice configuration.
        """
        self._client = AsyncOpenAI(api_key=api_key)
        self._config = config

    async def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        """Transcribe streaming audio chunks to text using Whisper API.

        Note: OpenAI Whisper API does not support true streaming transcription.
        This implementation accumulates chunks and transcribes when the stream
        ends or when final chunk is indicated.

        Args:
            audio_chunks: Async iterable of raw audio byte chunks (PCM16 24kHz mono).

        Yields:
            Final transcript string when transcription completes.

        Raises:
            SttError: On transcription failure.
        """
        pcm_buffer = io.BytesIO()

        try:
            async for chunk in audio_chunks:
                pcm_buffer.write(chunk)

            pcm_data = pcm_buffer.getvalue()
            if len(pcm_data) == 0:
                raise SttError("Empty audio buffer for transcription")

            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self._config.sample_rate_hz)
                wav_file.writeframes(pcm_data)

            wav_buffer.seek(0)
            wav_buffer.name = "audio.wav"

            async def _transcribe() -> str:
                wav_buffer.seek(0)
                response = await self._client.audio.transcriptions.create(
                    model=self._config.stt_model,
                    file=wav_buffer,
                    response_format="text",
                )
                return response

            transcript = await retry_async(_transcribe)

            if not transcript or not transcript.strip():
                raise SttError("Empty transcript from STT provider")

            yield transcript.strip()

        except SttError:
            raise
        except Exception as exc:
            raise SttError(f"STT transcription failed: {exc}") from exc

    async def _stream_speech(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM16 audio for ``text`` as the TTS response arrives.

        Streaming the HTTP response keeps time-to-first-audio at the provider's
        first byte instead of the full clip, which dominates perceived latency.

        Args:
            text: Non-empty text to synthesize.

        Yields:
            Sample-aligned PCM16 24kHz mono byte frames.
        """
        async with AsyncExitStack() as stack:

            async def _open_response() -> Any:
                # A response context manager is single-use, so each retry
                # attempt needs a freshly built request.
                return await stack.enter_async_context(
                    self._client.audio.speech.with_streaming_response.create(
                        model=self._config.tts_model,
                        voice=self._config.tts_voice,
                        input=text,
                        response_format="pcm",
                    )
                )

            response = await retry_async(_open_response)

            partial_sample = b""
            async for chunk in response.iter_bytes():
                buffered = partial_sample + chunk
                aligned_length = len(buffered) - len(buffered) % _PCM16_SAMPLE_BYTES
                partial_sample = buffered[aligned_length:]
                if aligned_length:
                    yield buffered[:aligned_length]

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        """Synthesize streaming text chunks to audio using OpenAI TTS API.

        Each incoming text chunk is synthesized as one request whose audio is
        forwarded incrementally, so playback can start before the clip is done.

        Args:
            text_chunks: Async iterable of text strings to synthesize.

        Yields:
            Audio byte chunks in PCM16 24kHz mono format.

        Raises:
            TtsError: On synthesis failure.
        """
        try:
            async for text in text_chunks:
                if not text or not text.strip():
                    continue

                emitted_audio = False
                async for audio_chunk in self._stream_speech(text.strip()):
                    emitted_audio = True
                    yield audio_chunk

                if not emitted_audio:
                    raise TtsError("Empty audio data from TTS provider")

        except TtsError:
            raise
        except Exception as exc:
            raise TtsError(f"TTS synthesis failed: {exc}") from exc
