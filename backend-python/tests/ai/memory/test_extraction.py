"""Tests for memory extraction pipeline (Epic 05 Phase 3)."""

from __future__ import annotations

import json
import uuid
from typing import cast

import pytest

from app.ai.memory.extraction import MemoryExtractor, _parse_extraction_response
from app.ai.memory.models import MemoryType
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings
from app.providers.base import LLMProvider
from app.schemas.chat import ChatMessageSchema
from tests.fakes import FakeProvider


def _extractor() -> MemoryExtractor:
    return MemoryExtractor(
        prompt_manager=create_prompt_manager(),
        settings=Settings(openai_api_key="test-key", memory_enabled=True),
    )


class TestParseExtractionResponse:
    def test_parses_valid_payload(self) -> None:
        payload = {
            "memories": [
                {
                    "memory_type": "user",
                    "title": "Language preference",
                    "content": "User prefers TypeScript.",
                    "confidence": 0.9,
                    "importance": 0.8,
                }
            ]
        }
        candidates = _parse_extraction_response(json.dumps(payload))

        assert len(candidates) == 1
        assert candidates[0].memory_type is MemoryType.USER
        assert candidates[0].content == "User prefers TypeScript."
        assert candidates[0].confidence == 0.9

    def test_parses_json_inside_markdown_fence(self) -> None:
        payload = {"memories": [{"memory_type": "project", "content": "Uses FastAPI."}]}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        candidates = _parse_extraction_response(fenced)

        assert len(candidates) == 1
        assert candidates[0].memory_type is MemoryType.PROJECT

    def test_invalid_json_returns_empty(self) -> None:
        assert _parse_extraction_response("not json") == []


@pytest.mark.anyio
async def test_extract_candidates_returns_parsed_memories() -> None:
    payload = {
        "memories": [
            {
                "memory_type": "user",
                "content": "User is allergic to peanuts.",
                "confidence": 0.95,
                "importance": 0.9,
            }
        ]
    }
    llm = FakeProvider(response=json.dumps(payload))
    extractor = _extractor()

    candidates = await extractor.extract_candidates(
        messages=[
            ChatMessageSchema(role="user", content="I'm allergic to peanuts."),
            ChatMessageSchema(role="assistant", content="I'll remember that."),
        ],
        provider=cast(LLMProvider, llm),
        provider_name="openai",
        model="gpt-4o-mini",
        session_id=uuid.uuid4(),
    )

    assert len(candidates) == 1
    assert candidates[0].content == "User is allergic to peanuts."


@pytest.mark.anyio
async def test_extract_candidates_handles_llm_failure() -> None:
    llm = FakeProvider(response="{}")
    llm.complete_chat = _raising_complete_chat  # type: ignore[method-assign]
    extractor = _extractor()

    candidates = await extractor.extract_candidates(
        messages=[ChatMessageSchema(role="user", content="Hello")],
        provider=cast(LLMProvider, llm),
        provider_name="openai",
        model="gpt-4o-mini",
        session_id=None,
    )

    assert candidates == []


async def _raising_complete_chat(*args, **kwargs):  # noqa: ANN002, ANN003
    del args, kwargs
    raise RuntimeError("provider down")
