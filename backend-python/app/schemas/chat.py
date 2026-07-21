import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import Settings

Role = Literal["system", "user", "assistant"]
ProviderName = Literal["openai", "gemini", "groq", "anthropic"]
MessageStatus = Literal["complete", "stopped", "error", "interrupted"]


def _max_message_length() -> int:
    return Settings().max_message_length


def _allowed_provider_models() -> dict[ProviderName, set[str]]:
    settings = Settings()
    return {
        "openai": {settings.openai_model},
        "gemini": {settings.gemini_model},
        "groq": {settings.groq_model},
        "anthropic": {settings.anthropic_model},
    }


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
    # V1.1 unified chat toggles (default off — plain chat when omitted).
    use_web_search: bool = False
    use_documents: bool = False
    # Additive persistence fields (backward-compatible; older clients omit them).
    # When set, the request appends to an existing owned session; otherwise a new
    # session is started. A supplied ``client_message_id`` makes the append
    # idempotent (plan Sections 2.11, 5.3).
    session_id: uuid.UUID | None = None
    client_message_id: str | None = Field(default=None, max_length=200)

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
    def validate_provider_model_compatibility(self) -> "ChatRequestSchema":
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


class RetrievedChunkMetaSchema(BaseModel):
    """Metadata for document chunks included in the LLM context (debugging only)."""

    chunk_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    chunk_index: int | None = None
    score: float


class ChatResponseSchema(BaseModel):
    id: str
    role: Role = "assistant"
    content: str
    model: str
    provider: ProviderName
    # Populated when persistence is active so the client can continue the session.
    session_id: uuid.UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Optional unified-chat metadata (V1.1b).
    retrieved_chunks: list[RetrievedChunkMetaSchema] | None = None
    tools_used: list[str] | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponseSchema(BaseModel):
    error: ErrorDetail


class StartFrame(BaseModel):
    type: Literal["start"] = "start"
    id: str
    # Populated when persistence is active so streaming clients learn the session.
    session_id: uuid.UUID | None = None
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


class ToolStartFrame(BaseModel):
    type: Literal["tool_start"] = "tool_start"
    id: str
    tool_name: str
    call_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ToolEndFrame(BaseModel):
    type: Literal["tool_end"] = "tool_end"
    id: str
    tool_name: str
    call_id: str
    success: bool
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatMessageOut(BaseModel):
    """A persisted chat message returned when resuming a session."""

    id: uuid.UUID
    seq: int
    role: Role
    content: str
    provider: str | None = None
    model: str | None = None
    status: MessageStatus = "complete"
    finish_reason: str | None = None
    created_at: datetime


class ChatSessionOut(BaseModel):
    """A persisted chat session with its ordered messages (plan Section 5.4)."""

    id: uuid.UUID
    title: str | None = None
    last_message_at: datetime | None = None
    messages: list[ChatMessageOut]


class ChatSessionListItem(BaseModel):
    """Lean session metadata for the sidebar list (plan Section 2.2) — no messages."""

    id: uuid.UUID
    title: str | None = None
    last_message_at: datetime | None = None
    created_at: datetime


ChatActivityPhase = Literal["thinking", "web_search", "document_retrieval"]


class ChatActivityFrame(BaseModel):
    """In-flight activity hint for non-streaming tool chat (NDJSON progress)."""

    type: Literal["activity"] = "activity"
    phase: ChatActivityPhase


class ChatCompleteFrame(BaseModel):
    """Terminal frame wrapping ``ChatResponseSchema`` for NDJSON progress."""

    type: Literal["complete"] = "complete"
    response: ChatResponseSchema
