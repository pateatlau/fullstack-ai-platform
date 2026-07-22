import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import AsyncIterator, Protocol, cast

from fastapi import Request
from groq import APITimeoutError as GroqAPITimeoutError
from groq import RateLimitError as GroqRateLimitError
from anthropic import APITimeoutError as AnthropicAPITimeoutError
from anthropic import RateLimitError as AnthropicRateLimitError
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.text_utils import derive_session_title
from app.ai.deps import get_prompt_manager
from app.ai.prompts.manager import PromptManager
from app.db.models import ChatMessage, ChatSession, SessionSummary, UsageEvent
from app.providers.base import LLMProvider, ProviderChunk, ProviderCompletion
from app.providers.factory import ProviderFactory
from app.schemas.chat import (
    ChatMessageOut,
    ChatMessageSchema,
    ChatRequestSchema,
    ChatResponseSchema,
    ChatSessionListItem,
    ChatSessionOut,
    DeltaFrame,
    EndFrame,
    ErrorFrame,
    MessageStatus,
    ProviderName,
    RetrievalCompleteFrame,
    Role,
    StartFrame,
    ToolEndFrame,
    ToolStartFrame,
)
from app.services.max_tokens import resolve_max_tokens
from app.services.usage_service import build_usage_record

logger = get_logger(__name__)


class ChatStore(Protocol):
    async def create_session(
        self,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        title: str | None = None,
    ) -> ChatSession: ...

    async def get_owned_session(
        self,
        session_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
    ) -> ChatSession | None: ...

    async def get_default_guest_session(
        self, guest_id: uuid.UUID
    ) -> ChatSession | None: ...

    async def list_sessions_for_owner(
        self,
        *,
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[ChatSession]: ...

    async def delete_session(self, session_id: uuid.UUID) -> bool: ...

    async def allocate_seq(self, session_id: uuid.UUID) -> int: ...

    async def add_message(
        self,
        *,
        session_id: uuid.UUID,
        seq: int,
        role: str,
        content: str,
        provider: str | None = None,
        model: str | None = None,
        status: str = "complete",
        finish_reason: str | None = None,
        client_message_id: str | None = None,
    ) -> ChatMessage: ...

    async def list_messages(self, session_id: uuid.UUID) -> list[ChatMessage]: ...

    async def find_by_client_message_id(
        self, session_id: uuid.UUID, client_message_id: str
    ) -> ChatMessage | None: ...

    async def get_message_by_seq(
        self, session_id: uuid.UUID, seq: int
    ) -> ChatMessage | None: ...

    async def mark_last_message_at(self, session_id: uuid.UUID) -> None: ...

    async def update_title(self, session_id: uuid.UUID, title: str) -> None: ...

    async def list_messages_after_seq(
        self, session_id: uuid.UUID, after_seq: int
    ) -> list[ChatMessage]: ...

    async def get_latest_summary(
        self, session_id: uuid.UUID
    ) -> SessionSummary | None: ...

    async def add_summary(
        self,
        *,
        session_id: uuid.UUID,
        version: int,
        covers_through_seq: int,
        content: str,
        provider: str,
        model: str,
    ) -> SessionSummary: ...


class UsageStore(Protocol):
    async def record(
        self,
        *,
        session_id: uuid.UUID,
        provider: str,
        model: str,
        token_source: str,
        kind: str = "chat",
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        message_id: uuid.UUID | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
        request_id: str | None = None,
    ) -> UsageEvent: ...


class QuotaChecker(Protocol):
    async def check(self, guest_id: uuid.UUID) -> None: ...

    async def record(self, guest_id: uuid.UUID, *, tokens: int = 0) -> None: ...

    async def remaining(self, guest_id: uuid.UUID) -> int: ...


@dataclass(frozen=True)
class _StreamPrep:
    """Pre-flight state for a persisted stream (user message already appended)."""

    session_id: uuid.UUID
    prompt_text: str
    idempotent_reply: str | None = None
    idempotent_finish: str | None = None


class ClosableAsyncIterator(Protocol):
    def __aiter__(self) -> AsyncIterator[ProviderChunk]: ...

    async def __anext__(self) -> ProviderChunk: ...

    async def aclose(self) -> None: ...


class ChatServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ProviderTimeoutError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="provider_timeout",
            message="Upstream provider timed out.",
            status_code=504,
        )


class ProviderRateLimitedError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="provider_rate_limited",
            message="Upstream rate limit hit, please retry shortly.",
            status_code=429,
        )


class ProviderError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="provider_error",
            message="Upstream provider failed.",
            status_code=502,
        )


class EmptyProviderResponseError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="empty_provider_response",
            message="The model returned an empty response. Please try again.",
            status_code=502,
        )


class SessionNotFoundError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="session_not_found",
            message="Chat session not found.",
            status_code=404,
        )


class ProviderNotAllowedError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="provider_not_allowed",
            message="Guests may only use the default provider and model.",
            status_code=403,
        )


class NewChatForbiddenError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="new_chat_forbidden",
            message="Guests may not create additional chat sessions.",
            status_code=403,
        )


class DbUnavailableError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="database_error",
            message="The database is temporarily unavailable.",
            status_code=503,
        )


def normalize_chat_error(exc: Exception) -> ChatServiceError:
    if isinstance(exc, ChatServiceError):
        return exc

    error_name = exc.__class__.__name__.lower()
    if (
        isinstance(
            exc,
            (
                TimeoutError,
                GroqAPITimeoutError,
                AnthropicAPITimeoutError,
            ),
        )
        or "timeout" in error_name
    ):
        return ProviderTimeoutError()
    if any(
        token in error_name
        for token in (
            "ratelimit",
            "too_many_requests",
            "toomanyrequests",
            "resourceexhausted",
        )
    ) or isinstance(exc, (GroqRateLimitError, AnthropicRateLimitError)):
        return ProviderRateLimitedError()
    return ProviderError()


SseFrame = (
    StartFrame
    | DeltaFrame
    | EndFrame
    | ErrorFrame
    | ToolStartFrame
    | ToolEndFrame
    | RetrievalCompleteFrame
)


def format_sse(event: str, data: SseFrame) -> str:
    return f"event: {event}\ndata: {data.model_dump_json()}\n\n"


def _format_sse(event: str, data: SseFrame) -> str:
    return format_sse(event, data)


class ChatService:
    """Validates/normalizes chat requests and orchestrates provider calls.

    When persistence collaborators (chat/usage stores, quota service, session)
    and a resolved caller are supplied, the chat lifecycle is persisted; without
    them the service behaves statelessly, preserving the original contracts.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        chat_store: ChatStore | None = None,
        usage_store: UsageStore | None = None,
        quota_service: QuotaChecker | None = None,
        session: AsyncSession | None = None,
        prompt_manager: PromptManager | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._chat_store = chat_store
        self._usage_store = usage_store
        self._quota_service = quota_service
        self._session = session
        self._prompt_manager = prompt_manager or get_prompt_manager()

    def _resolve_provider(
        self, request: ChatRequestSchema
    ) -> tuple[LLMProvider, str, ProviderName]:
        provider_name_raw = request.provider or self._settings.llm_provider
        allowed_providers: tuple[ProviderName, ...] = (
            "openai",
            "gemini",
            "groq",
            "anthropic",
        )
        if provider_name_raw not in allowed_providers:
            raise ChatServiceError(
                code="validation_error",
                message=(
                    f"Unsupported provider '{provider_name_raw}'. "
                    "Supported providers: openai, gemini, groq, anthropic."
                ),
                status_code=422,
            )

        provider_name = provider_name_raw
        required_key_by_provider: dict[ProviderName, tuple[str, str | None]] = {
            "openai": ("OPENAI_API_KEY", self._settings.openai_api_key),
            "gemini": ("GEMINI_API_KEY", self._settings.gemini_api_key),
            "groq": ("GROQ_API_KEY", self._settings.groq_api_key),
            "anthropic": ("ANTHROPIC_API_KEY", self._settings.anthropic_api_key),
        }
        key_env_name, key_value = required_key_by_provider[provider_name]
        if not key_value:
            raise ChatServiceError(
                code="validation_error",
                message=(
                    f"Provider '{provider_name}' is selected but {key_env_name} "
                    "is not set."
                ),
                status_code=422,
            )

        provider = ProviderFactory.get_provider(provider_name, self._settings)
        model = request.model or self._default_model(provider_name)
        return provider, model, provider_name

    def _default_model(self, provider_name: ProviderName) -> str:
        default_models: dict[ProviderName, str] = {
            "openai": self._settings.openai_model,
            "gemini": self._settings.gemini_model,
            "groq": self._settings.groq_model,
            "anthropic": self._settings.anthropic_model,
        }
        return default_models[provider_name]

    async def _complete_with_timeout(
        self,
        provider: LLMProvider,
        request: ChatRequestSchema,
        model: str,
        *,
        caller: CallerContext | None = None,
        provider_name: ProviderName | None = None,
    ) -> ProviderCompletion:
        max_tokens = resolve_max_tokens(
            caller,
            self._settings,
            provider_name=provider_name,
        )
        return await asyncio.wait_for(
            provider.complete_chat(
                request.messages,
                model,
                request.temperature,
                max_tokens=max_tokens,
            ),
            timeout=self._settings.request_timeout_seconds,
        )

    async def _complete_and_log(
        self,
        provider: LLMProvider,
        request: ChatRequestSchema,
        model: str,
        provider_name: ProviderName,
        *,
        caller: CallerContext | None = None,
        event: str = "Chat completion",
    ) -> ProviderCompletion:
        start = time.perf_counter()
        try:
            return await self._complete_with_timeout(
                provider,
                request,
                model,
                caller=caller,
                provider_name=provider_name,
            )
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                event,
                provider=provider_name,
                model=model,
                latency_ms=latency_ms,
            )

    # ---- persistence helpers ------------------------------------------------

    def _persistence_active(self, caller: CallerContext | None) -> bool:
        return (
            self._settings.chat_persistence_enabled
            and caller is not None
            and self._chat_store is not None
            and self._usage_store is not None
        )

    @staticmethod
    def _last_user_content(request: ChatRequestSchema) -> str:
        last_message = request.messages[-1]
        if last_message.role != "user":
            raise ChatServiceError(
                code="validation_error",
                message=(
                    "The last message must be from role 'user' when submitting "
                    "a chat turn."
                ),
                status_code=422,
            )
        return last_message.content

    async def _maybe_set_session_title(
        self, chat_session: ChatSession, user_content: str
    ) -> None:
        if chat_session.title is not None:
            return
        title = derive_session_title(user_content)
        if title is None:
            return
        assert self._chat_store is not None
        await self._chat_store.update_title(chat_session.id, title)
        chat_session.title = title
        logger.info(
            "Session title auto-generated",
            title_auto_generated_total=True,
            session_id=str(chat_session.id),
        )

    async def _commit(self) -> None:
        if self._session is not None:
            await self._session.commit()

    def _enforce_guest_provider_gating(
        self,
        caller: CallerContext,
        provider_name: ProviderName,
        model: str,
    ) -> None:
        """Reject non-default provider/model for guests (plan Sections 3.2, D3).

        Guests may omit ``provider``/``model`` (the resolved values then equal
        the system default and pass); an explicit value other than the system
        default is rejected before any provider call.
        """
        if caller.kind != "guest":
            return
        default_provider = cast(ProviderName, self._settings.llm_provider)
        default_model = self._default_model(default_provider)
        if provider_name != default_provider or model != default_model:
            raise ProviderNotAllowedError()

    async def _resolve_session(
        self, request: ChatRequestSchema, caller: CallerContext
    ) -> ChatSession:
        """Resolve the session a turn appends to (plan Section 2.3).

        Guests are restricted to a single default session, enforced here at the
        application level (no new DB constraint): addressing a non-default
        ``session_id`` is treated as ownership failure (404), and omitting
        ``session_id`` reuses the guest's existing default session rather than
        creating a new one.
        """
        assert self._chat_store is not None
        is_guest = caller.kind == "guest" and caller.guest_id is not None

        if request.session_id is not None:
            existing = await self._chat_store.get_owned_session(
                request.session_id,
                user_id=caller.user_id,
                guest_id=caller.guest_id,
            )
            if existing is None:
                raise SessionNotFoundError()
            if is_guest:
                assert caller.guest_id is not None
                default = await self._chat_store.get_default_guest_session(
                    caller.guest_id
                )
                if default is not None and default.id != existing.id:
                    raise SessionNotFoundError()
            return existing

        if is_guest:
            assert caller.guest_id is not None
            default = await self._chat_store.get_default_guest_session(caller.guest_id)
            if default is not None:
                return default

        return await self._chat_store.create_session(
            user_id=caller.user_id,
            guest_id=caller.guest_id,
            title=None,
        )

    async def _maybe_check_quota(self, caller: CallerContext) -> None:
        if (
            caller.kind == "guest"
            and caller.guest_id is not None
            and self._quota_service is not None
        ):
            await self._quota_service.check(caller.guest_id)

    async def _maybe_record_quota(
        self, caller: CallerContext, *, tokens: int = 0
    ) -> None:
        if (
            caller.kind == "guest"
            and caller.guest_id is not None
            and self._quota_service is not None
        ):
            await self._quota_service.record(caller.guest_id, tokens=tokens)

    async def guest_quota_remaining(self, caller: CallerContext | None) -> int | None:
        """Remaining guest daily-message quota, for the ``X-Guest-Quota-Remaining``
        response header (plan Section 3.1). ``None`` for authenticated callers or
        when quota tracking is inactive.
        """
        if (
            caller is None
            or caller.kind != "guest"
            or caller.guest_id is None
            or self._quota_service is None
        ):
            return None
        return await self._quota_service.remaining(caller.guest_id)

    async def _record_usage(
        self,
        *,
        caller: CallerContext,
        session_id: uuid.UUID,
        message_id: uuid.UUID,
        provider_name: ProviderName,
        model: str,
        completion: ProviderCompletion,
        prompt_text: str,
    ) -> int:
        record = build_usage_record(
            provider=provider_name,
            model=model,
            provider_usage=completion.usage,
            prompt_text=prompt_text,
            completion_text=completion.content,
        )
        if self._usage_store is None:
            return record.total_tokens or 0

        await self._usage_store.record(
            session_id=session_id,
            user_id=caller.user_id,
            guest_id=caller.guest_id,
            message_id=message_id,
            provider=record.provider,
            model=record.model,
            token_source=record.token_source,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            total_tokens=record.total_tokens,
            kind="chat",
        )
        return record.total_tokens or 0

    # ---- summarization (plan Sections 5.5, 5.6) -----------------------------

    def _build_summary_input(
        self,
        latest_summary: SessionSummary | None,
        pending: list[ChatMessage],
    ) -> list[ChatMessageSchema]:
        lines: list[str] = []
        if latest_summary is not None:
            lines.append(f"Summary so far: {latest_summary.content}")
        for message in pending:
            lines.append(f"{message.role}: {message.content}")
        transcript = "\n".join(lines)
        system_content = self._prompt_manager.render(
            "chat", "summarize_system", "1", {}
        )
        user_content = self._prompt_manager.render(
            "chat", "summarize_user", "1", {"transcript": transcript}
        )
        # model_construct bypasses the max-length validator: summarization input
        # is internal and intentionally may exceed a single user message limit.
        return [
            ChatMessageSchema.model_construct(
                role="system",
                content=system_content,
            ),
            ChatMessageSchema.model_construct(
                role="user",
                content=user_content,
            ),
        ]

    async def build_context_messages(
        self, session_id: uuid.UUID
    ) -> list[ChatMessageSchema]:
        """Deterministic context assembly (plan Sections 2.6, 5.6).

        Latest summary (if any) as a leading system message, followed by every
        message with ``seq > covers_through_seq`` in ``seq`` order. Deterministic
        by construction — exactly one summary combined with only later messages.
        """
        if self._chat_store is None:
            return []
        latest = await self._chat_store.get_latest_summary(session_id)
        covered = latest.covers_through_seq if latest is not None else 0
        pending = await self._chat_store.list_messages_after_seq(session_id, covered)

        assembled: list[ChatMessageSchema] = []
        if latest is not None:
            summary_content = self._prompt_manager.render(
                "chat",
                "context_summary_prefix",
                "1",
                {"summary_content": latest.content},
            )
            assembled.append(
                ChatMessageSchema.model_construct(
                    role="system",
                    content=summary_content,
                )
            )
        for message in pending:
            assembled.append(
                ChatMessageSchema.model_construct(
                    role=message.role, content=message.content
                )
            )
        return assembled

    async def _maybe_summarize(
        self,
        *,
        caller: CallerContext,
        session_id: uuid.UUID,
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
    ) -> None:
        if self._chat_store is None:
            return
        threshold = self._settings.summary_trigger_message_count
        latest = await self._chat_store.get_latest_summary(session_id)
        covered = latest.covers_through_seq if latest is not None else 0
        pending = await self._chat_store.list_messages_after_seq(session_id, covered)
        if len(pending) < threshold:
            return

        logger.info(
            "Summarization triggered",
            session_id=str(session_id),
            pending_messages=len(pending),
            threshold=threshold,
        )
        summary_input = self._build_summary_input(latest, pending)
        max_tokens = resolve_max_tokens(
            caller,
            self._settings,
            provider_name=provider_name,
        )
        try:
            completion = await asyncio.wait_for(
                provider.complete_chat(
                    summary_input,
                    model,
                    0.3,
                    max_tokens=max_tokens,
                ),
                timeout=self._settings.request_timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - summary is best-effort; retry next turn
            logger.warning(
                "Summarization failed",
                session_id=str(session_id),
                exc_info=True,
            )
            return

        version = (latest.version + 1) if latest is not None else 1
        await self._chat_store.add_summary(
            session_id=session_id,
            version=version,
            covers_through_seq=pending[-1].seq,
            content=completion.content,
            provider=provider_name,
            model=model,
        )
        logger.info(
            "Summarization succeeded",
            session_id=str(session_id),
            version=version,
            covers_through_seq=pending[-1].seq,
        )
        if self._usage_store is not None:
            record = build_usage_record(
                provider=provider_name,
                model=model,
                provider_usage=completion.usage,
                prompt_text=summary_input[-1].content,
                completion_text=completion.content,
                kind="summary",
            )
            await self._usage_store.record(
                session_id=session_id,
                user_id=caller.user_id,
                guest_id=caller.guest_id,
                message_id=None,
                provider=record.provider,
                model=record.model,
                token_source=record.token_source,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                total_tokens=record.total_tokens,
                kind="summary",
            )

    # ---- non-streaming completion -------------------------------------------

    async def complete_chat(
        self, request: ChatRequestSchema, caller: CallerContext | None = None
    ) -> ChatResponseSchema:
        provider, model, provider_name = self._resolve_provider(request)

        if not self._persistence_active(caller):
            try:
                completion = await self._complete_and_log(
                    provider,
                    request,
                    model,
                    provider_name,
                    caller=caller,
                )
            except Exception as exc:  # noqa: BLE001 - normalize provider failures
                raise normalize_chat_error(exc) from exc
            return ChatResponseSchema(
                id=f"resp_{uuid.uuid4().hex[:12]}",
                content=completion.content,
                model=model,
                provider=provider_name,
            )

        assert caller is not None and self._chat_store is not None
        self._enforce_guest_provider_gating(caller, provider_name, model)
        prompt_text = self._last_user_content(request)

        try:
            await self._maybe_check_quota(caller)
            chat_session = await self._resolve_session(request, caller)

            if request.client_message_id is not None:
                prior = await self._chat_store.find_by_client_message_id(
                    chat_session.id, request.client_message_id
                )
                if prior is not None:
                    reply = await self._chat_store.get_message_by_seq(
                        chat_session.id, prior.seq + 1
                    )
                    if reply is not None:
                        return ChatResponseSchema(
                            id=f"resp_{uuid.uuid4().hex[:12]}",
                            content=reply.content,
                            model=reply.model or model,
                            provider=cast(
                                ProviderName, reply.provider or provider_name
                            ),
                            session_id=chat_session.id,
                        )

            user_seq = await self._chat_store.allocate_seq(chat_session.id)
            await self._chat_store.add_message(
                session_id=chat_session.id,
                seq=user_seq,
                role="user",
                content=prompt_text,
                client_message_id=request.client_message_id,
            )
            await self._maybe_set_session_title(chat_session, prompt_text)
        except ChatServiceError:
            raise
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        try:
            completion = await self._complete_and_log(
                provider,
                request,
                model,
                provider_name,
                caller=caller,
            )
            if not completion.content.strip():
                raise EmptyProviderResponseError()
        except EmptyProviderResponseError as exc:
            error_seq = await self._chat_store.allocate_seq(chat_session.id)
            await self._chat_store.add_message(
                session_id=chat_session.id,
                seq=error_seq,
                role="assistant",
                content="",
                provider=provider_name,
                model=model,
                status="error",
            )
            await self._chat_store.mark_last_message_at(chat_session.id)
            await self._commit()
            raise exc
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            app_error = normalize_chat_error(exc)
            error_seq = await self._chat_store.allocate_seq(chat_session.id)
            await self._chat_store.add_message(
                session_id=chat_session.id,
                seq=error_seq,
                role="assistant",
                content="",
                provider=provider_name,
                model=model,
                status="error",
            )
            await self._chat_store.mark_last_message_at(chat_session.id)
            await self._commit()
            raise app_error from exc

        assistant_seq = await self._chat_store.allocate_seq(chat_session.id)
        assistant = await self._chat_store.add_message(
            session_id=chat_session.id,
            seq=assistant_seq,
            role="assistant",
            content=completion.content,
            provider=provider_name,
            model=model,
            status="complete",
            finish_reason=completion.finish_reason,
        )
        usage_tokens = await self._record_usage(
            caller=caller,
            session_id=chat_session.id,
            message_id=assistant.id,
            provider_name=provider_name,
            model=model,
            completion=completion,
            prompt_text=prompt_text,
        )
        await self._maybe_record_quota(caller, tokens=usage_tokens)
        await self._chat_store.mark_last_message_at(chat_session.id)
        await self._maybe_summarize(
            caller=caller,
            session_id=chat_session.id,
            provider=provider,
            provider_name=provider_name,
            model=model,
        )

        return ChatResponseSchema(
            id=f"resp_{uuid.uuid4().hex[:12]}",
            content=completion.content,
            model=model,
            provider=provider_name,
            session_id=chat_session.id,
        )

    # ---- resume -------------------------------------------------------------

    async def get_session_transcript(
        self, session_id: uuid.UUID, caller: CallerContext
    ) -> ChatSessionOut:
        if self._chat_store is None:
            raise SessionNotFoundError()
        try:
            chat_session = await self._chat_store.get_owned_session(
                session_id, user_id=caller.user_id, guest_id=caller.guest_id
            )
            if chat_session is None:
                raise SessionNotFoundError()
            messages = await self._chat_store.list_messages(session_id)
        except ChatServiceError:
            raise
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        return ChatSessionOut(
            id=chat_session.id,
            title=chat_session.title,
            last_message_at=chat_session.last_message_at,
            messages=[
                ChatMessageOut(
                    id=message.id,
                    seq=message.seq,
                    role=cast(Role, message.role),
                    content=message.content,
                    provider=message.provider,
                    model=message.model,
                    status=cast(MessageStatus, message.status),
                    finish_reason=message.finish_reason,
                    created_at=message.created_at,
                )
                for message in messages
            ],
        )

    # ---- session list/create (plan Section 2.2) -----------------------------

    async def list_sessions(
        self, caller: CallerContext | None
    ) -> list[ChatSessionListItem]:
        """Owner-scoped, lean session list for the sidebar.

        Returns ``[]`` when persistence is inactive (flag off, or no resolved
        caller/stores) rather than an error (plan Section 2.7).
        """
        if not self._persistence_active(caller):
            return []
        assert caller is not None and self._chat_store is not None
        try:
            sessions = await self._chat_store.list_sessions_for_owner(
                user_id=caller.user_id, guest_id=caller.guest_id
            )
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        return [
            ChatSessionListItem(
                id=session.id,
                title=session.title,
                last_message_at=session.last_message_at,
                created_at=session.created_at,
            )
            for session in sessions
        ]

    async def create_session(self, caller: CallerContext | None) -> ChatSessionOut:
        """Explicit, authenticated-only session create (plan Section 2.2).

        A guest caller — or persistence being inactive, which implies no
        resolved caller — cannot create additional sessions.
        """
        if self._chat_store is None or caller is None:
            raise SessionNotFoundError()
        if caller.kind != "user":
            raise NewChatForbiddenError()

        try:
            chat_session = await self._chat_store.create_session(user_id=caller.user_id)
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc
        await self._commit()

        return ChatSessionOut(
            id=chat_session.id,
            title=chat_session.title,
            last_message_at=chat_session.last_message_at,
            messages=[],
        )

    async def delete_session(
        self, session_id: uuid.UUID, caller: CallerContext | None
    ) -> None:
        """Delete an owned session and cascade child rows (plan Section Phase 2).

        Auth-only mutation — guests receive ``NewChatForbiddenError`` (403),
        matching ``create_session`` policy. Foreign/unknown sessions → 404.
        """
        if self._chat_store is None or caller is None:
            raise SessionNotFoundError()
        if caller.kind != "user":
            raise NewChatForbiddenError()

        try:
            chat_session = await self._chat_store.get_owned_session(
                session_id, user_id=caller.user_id
            )
            if chat_session is None:
                raise SessionNotFoundError()
            await self._chat_store.delete_session(session_id)
        except ChatServiceError:
            raise
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        await self._commit()
        logger.info(
            "Chat session deleted",
            session_delete_total=True,
            session_id=str(session_id),
            user_id=str(caller.user_id),
        )

    # ---- streaming ----------------------------------------------------------

    async def prepare_stream(
        self, request: ChatRequestSchema, caller: CallerContext | None
    ) -> _StreamPrep | None:
        """Pre-flight for a persisted stream (runs before the SSE response starts).

        Performs the quota check, session resolution, and user-message append so
        a 429/404 can be returned as a normal HTTP error. Returns ``None`` when
        persistence is inactive.
        """
        if not self._persistence_active(caller):
            return None
        assert caller is not None and self._chat_store is not None
        _, model, provider_name = self._resolve_provider(
            request
        )  # validate provider/model (may raise 422)
        self._enforce_guest_provider_gating(caller, provider_name, model)
        prompt_text = self._last_user_content(request)

        try:
            await self._maybe_check_quota(caller)
            chat_session = await self._resolve_session(request, caller)

            if request.client_message_id is not None:
                prior = await self._chat_store.find_by_client_message_id(
                    chat_session.id, request.client_message_id
                )
                if prior is not None:
                    reply = await self._chat_store.get_message_by_seq(
                        chat_session.id, prior.seq + 1
                    )
                    return _StreamPrep(
                        session_id=chat_session.id,
                        prompt_text=prompt_text,
                        idempotent_reply=reply.content if reply else "",
                        idempotent_finish=reply.finish_reason if reply else None,
                    )

            user_seq = await self._chat_store.allocate_seq(chat_session.id)
            await self._chat_store.add_message(
                session_id=chat_session.id,
                seq=user_seq,
                role="user",
                content=prompt_text,
                client_message_id=request.client_message_id,
            )
            await self._maybe_set_session_title(chat_session, prompt_text)
        except ChatServiceError:
            raise
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        return _StreamPrep(session_id=chat_session.id, prompt_text=prompt_text)

    async def stream_chat(
        self,
        request: ChatRequestSchema,
        http_request: Request,
        caller: CallerContext | None = None,
        prep: _StreamPrep | None = None,
    ) -> AsyncIterator[str]:
        """SSE event generator: yields start -> delta* -> end (or error).

        When ``prep`` is supplied (persistence active) the user message has
        already been appended; the assistant message + usage are persisted once
        the stream completes.
        """
        provider, model, provider_name = self._resolve_provider(request)
        response_id = f"resp_{uuid.uuid4().hex[:12]}"
        session_id = prep.session_id if prep is not None else None

        yield _format_sse("start", StartFrame(id=response_id, session_id=session_id))

        if prep is not None and prep.idempotent_reply is not None:
            if prep.idempotent_reply:
                yield _format_sse(
                    "delta", DeltaFrame(id=response_id, content=prep.idempotent_reply)
                )
            yield _format_sse(
                "end",
                EndFrame(
                    id=response_id, finish_reason=prep.idempotent_finish or "stop"
                ),
            )
            return

        provider_stream: AsyncIterator[ProviderChunk] | None = None
        closable_provider_stream: ClosableAsyncIterator | None = None
        collected: list[str] = []
        finish_reason = "stop"
        stream_start = time.perf_counter()

        try:
            provider_stream = provider.stream_chat(
                request.messages,
                model,
                request.temperature,
                max_tokens=resolve_max_tokens(
                    caller,
                    self._settings,
                    provider_name=provider_name,
                ),
            ).__aiter__()
            closable_provider_stream = cast(
                ClosableAsyncIterator | None, provider_stream
            )
            while True:
                if await http_request.is_disconnected():
                    logger.info(
                        "Client disconnected, stopping stream",
                        response_id=response_id,
                    )
                    await self._persist_stream_result(
                        caller=caller,
                        prep=prep,
                        provider=provider,
                        provider_name=provider_name,
                        model=model,
                        content="".join(collected),
                        finish_reason="interrupted",
                        status="interrupted",
                    )
                    return

                try:
                    chunk = await asyncio.wait_for(
                        anext(provider_stream),
                        timeout=self._settings.request_timeout_seconds,
                    )
                except StopAsyncIteration:
                    break

                if chunk["content"]:
                    collected.append(chunk["content"])
                    yield _format_sse(
                        "delta", DeltaFrame(id=response_id, content=chunk["content"])
                    )
                if chunk["finish_reason"]:
                    finish_reason = chunk["finish_reason"]

            if not collected:
                empty_error = EmptyProviderResponseError()
                logger.warning(
                    "Provider stream returned no content",
                    provider=provider_name,
                    model=model,
                    response_id=response_id,
                    finish_reason=finish_reason,
                )
                await self._persist_stream_result(
                    caller=caller,
                    prep=prep,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    content="",
                    finish_reason=None,
                    status="error",
                )
                yield _format_sse(
                    "error",
                    ErrorFrame(
                        id=response_id,
                        code=empty_error.code,
                        message=empty_error.message,
                    ),
                )
                return

            await self._persist_stream_result(
                caller=caller,
                prep=prep,
                provider=provider,
                provider_name=provider_name,
                model=model,
                content="".join(collected),
                finish_reason=finish_reason,
                status="complete",
            )
            latency_ms = int((time.perf_counter() - stream_start) * 1000)
            logger.info(
                "Chat stream completed",
                provider=provider_name,
                model=model,
                latency_ms=latency_ms,
                response_id=response_id,
            )
            yield _format_sse(
                "end", EndFrame(id=response_id, finish_reason=finish_reason)
            )
        except Exception as exc:  # noqa: BLE001 - normalize any provider failure into a frame
            app_error = normalize_chat_error(exc)
            logger.exception(
                "Provider stream failed",
                response_id=response_id,
                provider=provider_name,
                model=model,
            )
            try:
                await self._persist_stream_result(
                    caller=caller,
                    prep=prep,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    content="".join(collected),
                    finish_reason=None,
                    status="error",
                )
            except Exception:  # noqa: BLE001 - best-effort error persistence
                logger.exception(
                    "Failed to persist stream error state",
                    response_id=response_id,
                )
            yield _format_sse(
                "error",
                ErrorFrame(
                    id=response_id,
                    code=app_error.code,
                    message=app_error.message,
                ),
            )
        finally:
            if closable_provider_stream is not None:
                close_stream = cast(
                    Callable[[], Awaitable[None]] | None,
                    getattr(closable_provider_stream, "aclose", None),
                )
                if callable(close_stream):
                    await close_stream()

    async def _persist_stream_result(
        self,
        *,
        caller: CallerContext | None,
        prep: _StreamPrep | None,
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
        content: str,
        finish_reason: str | None,
        status: str,
    ) -> None:
        if prep is None or caller is None or self._chat_store is None:
            return
        assistant_seq = await self._chat_store.allocate_seq(prep.session_id)
        assistant = await self._chat_store.add_message(
            session_id=prep.session_id,
            seq=assistant_seq,
            role="assistant",
            content=content,
            provider=provider_name,
            model=model,
            status=status,
            finish_reason=finish_reason,
        )
        if status == "complete" and self._usage_store is not None:
            record = build_usage_record(
                provider=provider_name,
                model=model,
                provider_usage=None,
                prompt_text=prep.prompt_text,
                completion_text=content,
            )
            await self._usage_store.record(
                session_id=prep.session_id,
                user_id=caller.user_id,
                guest_id=caller.guest_id,
                message_id=assistant.id,
                provider=record.provider,
                model=record.model,
                token_source=record.token_source,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                total_tokens=record.total_tokens,
                kind="chat",
            )
            await self._maybe_record_quota(caller, tokens=record.total_tokens or 0)
        await self._chat_store.mark_last_message_at(prep.session_id)
        if status == "complete":
            await self._maybe_summarize(
                caller=caller,
                session_id=prep.session_id,
                provider=provider,
                provider_name=provider_name,
                model=model,
            )
