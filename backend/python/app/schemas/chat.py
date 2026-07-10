from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings

Role = Literal["system", "user", "assistant"]
ProviderName = Literal["openai", "gemini"]


def _max_message_length() -> int:
    return Settings().max_message_length


class ChatMessageSchema(BaseModel):
    role: Role
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("message content must not be blank")
        max_message_length = _max_message_length()
        if len(trimmed) > max_message_length:
            raise ValueError(
                f"message content must be at most {max_message_length} characters"
            )
        return trimmed


class ChatRequestSchema(BaseModel):
    messages: list[ChatMessageSchema] = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    provider: ProviderName | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("model must not be blank")
        return trimmed


class ChatResponseSchema(BaseModel):
    id: str
    role: Role = "assistant"
    content: str
    model: str
    provider: ProviderName
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponseSchema(BaseModel):
    error: ErrorDetail


class StartFrame(BaseModel):
    type: Literal["start"] = "start"
    id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DeltaFrame(BaseModel):
    type: Literal["delta"] = "delta"
    id: str
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EndFrame(BaseModel):
    type: Literal["end"] = "end"
    id: str
    finish_reason: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorFrame(BaseModel):
    type: Literal["error"] = "error"
    id: str
    code: str
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
