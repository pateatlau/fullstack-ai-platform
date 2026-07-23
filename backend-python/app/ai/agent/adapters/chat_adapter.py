"""Non-streaming chat adapter for the agent runtime (Phase 11)."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.tools.implementations.web_search import WEB_SEARCH_TOOL_NAME
from app.core.caller import CallerContext
from app.core.config import Settings
from app.middleware.correlation_id import get_request_id
from app.providers.base import ProviderCompletion
from app.schemas.chat import (
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
from app.services.max_tokens import resolve_max_tokens
from app.services.tool_chat_service import ChatActivityCallback

# V1.1 chat adapter uses a tighter iteration budget than the core default (5).
CHAT_AGENT_MAX_ITERATIONS = 3


class ChatAgentAdapter:
    """Maps chat requests/responses to the agent runtime for web search."""

    def __init__(
        self,
        *,
        agent: DefaultAgent,
        chat_service: ChatService,
        settings: Settings,
    ) -> None:
        self._agent = agent
        self._chat_service = chat_service
        self._settings = settings

    async def complete_chat(
        self,
        request: ChatRequestSchema,
        caller: CallerContext | None = None,
        on_activity: ChatActivityCallback | None = None,
        *,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> ChatResponseSchema:
        """Run one non-streaming web-search chat turn via the agent runtime."""
        provider, model, provider_name = self._chat_service._resolve_provider(request)
        agent_request = build_agent_request(
            request=request,
            model=model,
            provider_name=provider_name,
            caller=caller,
            settings=self._settings,
            allowed_tool_names=allowed_tool_names,
        )
        agent_context = build_agent_context(
            caller=caller,
            allowed_tool_names=allowed_tool_names,
        )

        if not self._chat_service._persistence_active(caller):
            return await self._complete_stateless(
                agent_request=agent_request,
                agent_context=agent_context,
                model=model,
                provider_name=provider_name,
                on_activity=on_activity,
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
            if on_activity is not None:
                await on_activity("web_search")
            agent_response = await self._agent.run(agent_request, agent_context)
            if on_activity is not None:
                await on_activity("thinking")
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
            content=agent_response.content,
            provider=provider_name,
            model=model,
            status="complete",
            finish_reason=agent_response.finish_reason,
        )
        usage_tokens = await self._chat_service._record_usage(
            caller=caller,
            session_id=chat_session.id,
            message_id=assistant.id,
            provider_name=provider_name,
            model=model,
            completion=ProviderCompletion(
                content=agent_response.content,
                finish_reason=agent_response.finish_reason,
                usage=None,
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
            content=agent_response.content,
            model=model,
            provider=provider_name,
            session_id=chat_session.id,
            tools_used=agent_response.tools_used or None,
        )

    async def _complete_stateless(
        self,
        *,
        agent_request: AgentRequest,
        agent_context: AgentContext,
        model: str,
        provider_name: ProviderName,
        on_activity: ChatActivityCallback | None,
    ) -> ChatResponseSchema:
        try:
            if on_activity is not None:
                await on_activity("web_search")
            agent_response = await self._agent.run(agent_request, agent_context)
            if on_activity is not None:
                await on_activity("thinking")
        except NotImplementedError as exc:
            raise normalize_chat_error(exc) from exc
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            raise normalize_chat_error(exc) from exc

        return ChatResponseSchema(
            id=f"resp_{uuid.uuid4().hex[:12]}",
            content=agent_response.content,
            model=model,
            provider=provider_name,
            tools_used=agent_response.tools_used or None,
        )


def build_agent_request(
    *,
    request: ChatRequestSchema,
    model: str,
    provider_name: ProviderName,
    caller: CallerContext | None,
    settings: Settings,
    allowed_tool_names: frozenset[str] | None,
) -> AgentRequest:
    """Map a chat request to an :class:`AgentRequest`."""
    tool_names = sorted(allowed_tool_names) if allowed_tool_names is not None else None
    return AgentRequest(
        messages=[
            AgentMessage(role=message.role, content=message.content)
            for message in request.messages
        ],
        model=model,
        provider=provider_name,
        temperature=request.temperature,
        max_tokens=resolve_max_tokens(
            caller,
            settings,
            provider_name=provider_name,
        ),
        tool_names=tool_names,
        config=AgentConfig(
            max_iterations=CHAT_AGENT_MAX_ITERATIONS,
            timeout_seconds=settings.request_timeout_seconds,
        ),
    )


def build_agent_context(
    *,
    caller: CallerContext | None,
    allowed_tool_names: frozenset[str] | None,
) -> AgentContext:
    """Build execution-scoped context for one chat-backed agent run."""
    return AgentContext(
        execution_id=uuid.uuid4().hex,
        request_id=get_request_id(),
        caller=caller,
        allowed_tool_names=allowed_tool_names or frozenset({WEB_SEARCH_TOOL_NAME}),
    )
