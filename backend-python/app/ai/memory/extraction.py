"""Durable memory candidate extraction pipeline (Epic 05 Phase 3)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.ai.memory.models import MemoryType
from app.ai.prompts.manager import PromptManager
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import LLMProvider
from app.schemas.chat import ChatMessageSchema, ProviderName

logger = get_logger(__name__)

_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(\{.*\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


class CandidateMemory(BaseModel):
    """Normalized pre-persistence memory candidate from extraction."""

    memory_type: MemoryType
    title: str | None = None
    content: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    quality_score: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class MemoryExtractor:
    """Extract durable memory candidates from conversation history via LLM."""

    def __init__(
        self,
        *,
        prompt_manager: PromptManager,
        settings: Settings,
    ) -> None:
        self._prompt_manager = prompt_manager
        self._settings = settings

    async def extract_candidates(
        self,
        *,
        messages: list[ChatMessageSchema],
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
        session_id: uuid.UUID | None,
    ) -> list[CandidateMemory]:
        if not messages:
            return []

        extraction_model = self._settings.memory_extraction_model.strip() or model
        prompt = self._prompt_manager.render(
            "memory",
            "extract",
            "1",
            {
                "transcript": _format_transcript(messages),
                "session_id": str(session_id) if session_id is not None else "",
            },
        )
        llm_messages = [
            ChatMessageSchema(role="system", content=prompt),
            ChatMessageSchema(
                role="user",
                content="Extract durable memories from the transcript above.",
            ),
        ]

        try:
            completion = await provider.complete_chat(
                llm_messages,
                extraction_model,
                temperature=0.2,
            )
        except Exception:  # noqa: BLE001 - extraction must not block callers
            logger.warning(
                "Memory extraction LLM call failed",
                provider=provider_name,
                model=extraction_model,
                exc_info=True,
            )
            return []

        return _parse_extraction_response(completion.content)


def _format_transcript(messages: list[ChatMessageSchema]) -> str:
    lines: list[str] = []
    for message in messages:
        lines.append(f"{message.role.upper()}: {message.content}")
    return "\n".join(lines)


def _parse_extraction_response(content: str) -> list[CandidateMemory]:
    payload = _load_json_payload(content)
    if payload is None:
        logger.warning("Memory extraction response was not valid JSON")
        return []

    raw_memories = payload.get("memories")
    if not isinstance(raw_memories, list):
        logger.warning("Memory extraction response missing 'memories' array")
        return []

    candidates: list[CandidateMemory] = []
    for item in raw_memories:
        if not isinstance(item, dict):
            continue
        try:
            candidates.append(_candidate_from_dict(item))
        except ValueError:
            logger.warning("Skipping invalid memory extraction item")
    return candidates


def _load_json_payload(content: str) -> dict[str, Any] | None:
    stripped = content.strip()
    fence_match = _JSON_FENCE_PATTERN.search(stripped)
    if fence_match is not None:
        stripped = fence_match.group(1).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _candidate_from_dict(data: dict[str, Any]) -> CandidateMemory:
    memory_type_raw = data.get("memory_type")
    if memory_type_raw not in {"user", "project"}:
        raise ValueError("Invalid memory_type")

    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Missing content")

    return CandidateMemory(
        memory_type=MemoryType(memory_type_raw),
        title=data.get("title") if isinstance(data.get("title"), str) else None,
        content=content.strip(),
        confidence=_coerce_score(data.get("confidence"), default=0.5),
        importance=_coerce_score(data.get("importance"), default=0.5),
        quality_score=_coerce_score(data.get("quality_score"), default=0.5),
    )


def _coerce_score(value: object, *, default: float) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return default
