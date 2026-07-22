"""Non-streaming chat orchestration with LLM tool-calling support."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.ai.prompts.manager import PromptManager
from app.ai.tools.implementations.web_search import WEB_SEARCH_TOOL_NAME
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolExecutionContext
from app.core.caller import CallerContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.middleware.correlation_id import get_request_id
from app.providers.base import (
    ChatMessageInput,
    LLMProvider,
    ProviderCompletion,
    ProviderToolCall,
    ProviderToolCompletion,
    ProviderUsage,
)
from app.providers.capabilities import get_capabilities
from app.schemas.chat import (
    ChatMessageSchema,
    ChatRequestSchema,
    ChatResponseSchema,
    ProviderName,
)
from app.services.chat_service import (
    ChatService,
    ChatServiceError,
    DbUnavailableError,
    normalize_chat_error,
)

logger = get_logger(__name__)

_TOOL_ITERATION_LIMIT_MESSAGE = (
    "I reached the tool-use limit for this request. "
    "Please try a simpler question or ask again."
)
_GUEST_TOOL_DENIED_MESSAGE = (
    "Tool use requires a signed-in account. "
    "Please sign in to search the web or ask a question I can answer directly."
)

ChatActivityCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class _ToolLoopResult:
    content: str
    finish_reason: str | None
    usage: ProviderUsage | None
    tools_used: list[str]


class ToolChatService:
    """Compose ``ChatService`` with a capped non-streaming tool loop."""

    def __init__(
        self,
        chat_service: ChatService,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry,
        prompt_manager: PromptManager,
        settings: Settings,
        max_tool_iterations: int = 3,
    ) -> None:
        self._chat_service = chat_service
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self._prompt_manager = prompt_manager
        self._settings = settings
        self._max_tool_iterations = max_tool_iterations

    async def complete_chat(
        self,
        request: ChatRequestSchema,
        caller: CallerContext | None = None,
        on_activity: ChatActivityCallback | None = None,
        *,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> ChatResponseSchema:
        provider, model, provider_name = self._chat_service._resolve_provider(request)

        if not self._chat_service._persistence_active(caller):
            try:
                completion = await self._run_tool_loop(
                    provider=provider,
                    request=request,
                    model=model,
                    provider_name=provider_name,
                    caller=caller,
                    on_activity=on_activity,
                    allowed_tool_names=allowed_tool_names,
                )
            except NotImplementedError as exc:
                raise normalize_chat_error(exc) from exc
            except Exception as exc:  # noqa: BLE001 - normalize provider failures
                raise normalize_chat_error(exc) from exc
            return ChatResponseSchema(
                id=f"resp_{uuid.uuid4().hex[:12]}",
                content=completion.content,
                model=model,
                provider=provider_name,
                tools_used=completion.tools_used or None,
            )

        assert caller is not None
        chat_store = self._chat_service._chat_store
        assert chat_store is not None
        self._chat_service._enforce_guest_provider_gating(caller, provider_name, model)
        prompt_text = self._chat_service._last_user_content(request)

        try:
            await self._chat_service._maybe_check_quota(caller)
            chat_session = await self._chat_service._resolve_session(request, caller)

            if request.client_message_id is not None:
                prior = await chat_store.find_by_client_message_id(
                    chat_session.id, request.client_message_id
                )
                if prior is not None:
                    reply = await chat_store.get_message_by_seq(
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

            user_seq = await chat_store.allocate_seq(chat_session.id)
            await chat_store.add_message(
                session_id=chat_session.id,
                seq=user_seq,
                role="user",
                content=prompt_text,
                client_message_id=request.client_message_id,
            )
            await self._chat_service._maybe_set_session_title(chat_session, prompt_text)
        except ChatServiceError:
            raise
        except (OperationalError, InterfaceError, DBAPIError) as exc:
            raise DbUnavailableError() from exc

        try:
            completion = await self._run_tool_loop(
                provider=provider,
                request=request,
                model=model,
                provider_name=provider_name,
                caller=caller,
                on_activity=on_activity,
                allowed_tool_names=allowed_tool_names,
            )
        except NotImplementedError as exc:
            app_error = normalize_chat_error(exc)
            error_seq = await chat_store.allocate_seq(chat_session.id)
            await chat_store.add_message(
                session_id=chat_session.id,
                seq=error_seq,
                role="assistant",
                content="",
                provider=provider_name,
                model=model,
                status="error",
            )
            await chat_store.mark_last_message_at(chat_session.id)
            await self._chat_service._commit()
            raise app_error from exc
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            app_error = normalize_chat_error(exc)
            error_seq = await chat_store.allocate_seq(chat_session.id)
            await chat_store.add_message(
                session_id=chat_session.id,
                seq=error_seq,
                role="assistant",
                content="",
                provider=provider_name,
                model=model,
                status="error",
            )
            await chat_store.mark_last_message_at(chat_session.id)
            await self._chat_service._commit()
            raise app_error from exc

        assistant_seq = await chat_store.allocate_seq(chat_session.id)
        assistant = await chat_store.add_message(
            session_id=chat_session.id,
            seq=assistant_seq,
            role="assistant",
            content=completion.content,
            provider=provider_name,
            model=model,
            status="complete",
            finish_reason=completion.finish_reason,
        )
        usage_tokens = await self._chat_service._record_usage(
            caller=caller,
            session_id=chat_session.id,
            message_id=assistant.id,
            provider_name=provider_name,
            model=model,
            completion=ProviderCompletion(
                content=completion.content,
                finish_reason=completion.finish_reason,
                usage=completion.usage,
            ),
            prompt_text=prompt_text,
        )
        await self._chat_service._maybe_record_quota(caller, tokens=usage_tokens)
        await chat_store.mark_last_message_at(chat_session.id)
        await self._chat_service._maybe_summarize(
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
            tools_used=completion.tools_used or None,
        )

    async def _run_tool_loop(
        self,
        *,
        provider: LLMProvider,
        request: ChatRequestSchema,
        model: str,
        provider_name: ProviderName,
        caller: CallerContext | None,
        on_activity: ChatActivityCallback | None = None,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> _ToolLoopResult:
        tools = self._tool_registry.get_schemas_for_llm()
        if allowed_tool_names is not None:
            tools = [
                schema
                for schema in tools
                if schema.get("function", {}).get("name") in allowed_tool_names
            ]
        tools_used: list[str] = []
        if not tools:
            completion = await self._chat_service._complete_and_log(
                provider,
                request,
                model,
                provider_name,
                event="Chat completion (no tools registered)",
            )
            return _ToolLoopResult(
                content=completion.content,
                finish_reason=completion.finish_reason,
                usage=completion.usage,
                tools_used=tools_used,
            )

        if not get_capabilities(provider_name).supports_tool_calling:
            raise ChatServiceError(
                code="validation_error",
                message=(
                    f"Tool calling is not supported for provider '{provider_name}'."
                ),
                status_code=422,
            )

        loop_messages = self._build_loop_messages(request.messages)
        last_completion: ProviderToolCompletion | None = None

        for iteration in range(self._max_tool_iterations):
            try:
                completion = await asyncio.wait_for(
                    provider.complete_chat_with_tools(
                        loop_messages,
                        model,
                        tools,
                        request.temperature,
                    ),
                    timeout=self._settings.request_timeout_seconds,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.exception(
                    "Tool-enabled completion failed",
                    provider=provider_name,
                    model=model,
                    iteration=iteration + 1,
                )
                raise normalize_chat_error(exc) from exc

            last_completion = completion
            if not completion.tool_calls:
                content = completion.content or ""
                return _ToolLoopResult(
                    content=content,
                    finish_reason=completion.finish_reason,
                    usage=completion.usage,
                    tools_used=tools_used,
                )

            assistant_message = _assistant_tool_call_message(completion)
            loop_messages.append(assistant_message)

            guest_denied = False
            for tool_call in completion.tool_calls:
                if tool_call.name not in tools_used:
                    tools_used.append(tool_call.name)
                tool_result_content, denied = await self._execute_tool_call(
                    tool_call=tool_call,
                    caller=caller,
                    on_activity=on_activity,
                )
                if denied:
                    guest_denied = True
                loop_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result_content,
                    }
                )

            if guest_denied and (caller is None or caller.kind == "guest"):
                return _ToolLoopResult(
                    content=_GUEST_TOOL_DENIED_MESSAGE,
                    finish_reason="stop",
                    usage=completion.usage,
                    tools_used=tools_used,
                )

        logger.warning(
            "Tool iteration cap reached",
            provider=provider_name,
            model=model,
            max_iterations=self._max_tool_iterations,
        )
        fallback_content = (
            last_completion.content
            if last_completion is not None and last_completion.content
            else _TOOL_ITERATION_LIMIT_MESSAGE
        )
        return _ToolLoopResult(
            content=fallback_content,
            finish_reason="tool_iteration_cap",
            usage=last_completion.usage if last_completion is not None else None,
            tools_used=tools_used,
        )

    def _build_loop_messages(
        self, request_messages: list[ChatMessageSchema]
    ) -> list[ChatMessageInput]:
        tool_prompt = self._prompt_manager.render("tools", "tool_use_system", "1", {})
        return [
            ChatMessageSchema.model_construct(role="system", content=tool_prompt),
            *request_messages,
        ]

    async def _execute_tool_call(
        self,
        *,
        tool_call: ProviderToolCall,
        caller: CallerContext | None,
        on_activity: ChatActivityCallback | None = None,
    ) -> tuple[str, bool]:
        if caller is None or caller.kind == "guest":
            payload = {
                "success": False,
                "error": _GUEST_TOOL_DENIED_MESSAGE,
                "error_code": "forbidden",
            }
            return json.dumps(payload), True

        if tool_call.name == WEB_SEARCH_TOOL_NAME and on_activity is not None:
            await on_activity("web_search")
        try:
            result = await self._tool_executor.execute(
                ToolCall(
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                    call_id=tool_call.id,
                ),
                ToolExecutionContext(
                    caller=caller,
                    request_id=get_request_id(),
                ),
            )
        finally:
            if tool_call.name == WEB_SEARCH_TOOL_NAME and on_activity is not None:
                await on_activity("thinking")
        payload: dict[str, object] = {
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "error_code": result.error_code,
        }
        denied = result.error_code == "forbidden"
        return json.dumps(payload), denied


def _assistant_tool_call_message(
    completion: ProviderToolCompletion,
) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": completion.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
                **(
                    {"thought_signature": call.thought_signature}
                    if call.thought_signature is not None
                    else {}
                ),
            }
            for call in completion.tool_calls
        ],
    }
