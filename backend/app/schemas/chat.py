from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]
ProviderName = Literal["openai", "gemini"]


class ChatMessageSchema(BaseModel):
    role: Role
    content: str


class ChatRequestSchema(BaseModel):
    messages: list[ChatMessageSchema]
    model: str | None = None
    provider: ProviderName | None = None
    temperature: float = 0.7


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
