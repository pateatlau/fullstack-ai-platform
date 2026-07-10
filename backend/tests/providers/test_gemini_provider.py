import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.providers.gemini_provider import GeminiProvider
from app.schemas.chat import ChatMessageSchema


class _FakeChunk:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def generate_content(
        self, model: str, contents: str, config: dict
    ) -> _FakeResponse:
        assert model
        assert contents
        assert "temperature" in config
        return _FakeResponse("Gemini full response")

    def generate_content_stream(self, model: str, contents: str, config: dict):
        assert model
        assert contents
        assert "temperature" in config
        yield _FakeChunk("Gemini ")
        yield _FakeChunk("stream")


class _FakeClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def test_complete_chat_returns_text() -> None:
    provider = GeminiProvider(api_key="test-key")
    provider._client = _FakeClient()  # type: ignore[assignment]

    result = asyncio.run(
        provider.complete_chat(
            messages=[ChatMessageSchema(role="user", content="hello")],
            model="gemini-1.5-flash",
            temperature=0.7,
        )
    )

    assert result == "Gemini full response"


def test_stream_chat_yields_incremental_chunks() -> None:
    provider = GeminiProvider(api_key="test-key")
    provider._client = _FakeClient()  # type: ignore[assignment]

    async def gather_chunks() -> list[dict]:
        chunks = []
        async for chunk in provider.stream_chat(
            messages=[ChatMessageSchema(role="user", content="hello")],
            model="gemini-1.5-flash",
            temperature=0.7,
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(gather_chunks())

    assert chunks == [
        {"content": "Gemini ", "finish_reason": None},
        {"content": "stream", "finish_reason": None},
    ]
