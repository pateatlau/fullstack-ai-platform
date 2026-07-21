"""Generic RAG API request/response DTOs (Phase 11)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import Settings
from app.schemas.chat import ProviderName, _allowed_provider_models


def _max_message_length() -> int:
    return Settings().max_message_length


class RAGAskRequest(BaseModel):
    question: str = Field(min_length=1)
    prompt_template: str | None = None
    instructions: str | None = None
    top_k: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    provider: ProviderName | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("model must not be blank")
        return trimmed

    @model_validator(mode="after")
    def validate_provider_model_compatibility(self) -> "RAGAskRequest":
        if self.provider is None or self.model is None:
            return self

        allowed_models = _allowed_provider_models().get(self.provider)
        if allowed_models is None:
            return self

        if self.model not in allowed_models:
            allowed = ", ".join(sorted(allowed_models))
            raise ValueError(
                f"model '{self.model}' is not valid for provider '{self.provider}'. "
                f"Allowed: {allowed}"
            )

        return self

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("question must not be blank")
        max_message_length = _max_message_length()
        if len(trimmed) > max_message_length:
            raise ValueError(
                f"question must be at most {max_message_length} characters"
            )
        return trimmed


class RetrievedChunkMetaSchema(BaseModel):
    chunk_id: uuid.UUID | None
    document_id: uuid.UUID | None
    chunk_index: int | None
    score: float


class RAGAskResponse(BaseModel):
    answer: str
    retrieved_chunks: list[RetrievedChunkMetaSchema]
    truncated: bool
    model: str
    provider: ProviderName
    retrieval_latency_ms: int | None = None
    llm_latency_ms: int | None = None
