"""LLM step helpers for the agent execution loop (Phase 8)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

from app.ai.agent.exceptions import AgentTimeoutError
from app.ai.agent.interfaces.streaming import StreamPublisher
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.events import AgentStreamEvent
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.retry import llm_retry_policy_from_config, retry_operation
from app.ai.agent.scratchpad.scratchpad import Scratchpad, ScratchpadMessage
from app.providers.base import LLMProvider, ProviderChunk, ProviderCompletion


async def stream_final_answer(
    provider: LLMProvider,
    *,
    request: AgentRequest,
    scratchpad: Scratchpad,
    execution_id: str,
    publisher: StreamPublisher,
) -> str:
    """Generate and stream the final answer via the provider."""
    messages = _scratchpad_to_chat_messages(scratchpad)
    config = request.config or AgentConfig()
    content_parts: list[str] = []
    stream = provider.stream_chat(
        cast(Any, messages),
        request.model,
        request.temperature,
        max_tokens=request.max_tokens,
    )
    try:
        async for chunk in _iter_stream_with_timeout(
            stream,
            timeout_seconds=config.timeout_seconds,
        ):
            token = chunk.get("content") or ""
            if token:
                await publisher.publish(
                    AgentStreamEvent.token(execution_id, content=token)
                )
                content_parts.append(token)
    finally:
        await _aclose_stream(stream)
    return "".join(content_parts)


async def emit_answer_stream_start(
    execution_id: str,
    publisher: StreamPublisher,
) -> None:
    """Signal that the final answer stream is beginning (Phase 11 SSE parity).

    The chat stream adapter maps the first token event — even when empty — to
    an SSE ``start`` frame so the UI can show the assistant bubble while the
    provider stream warms up, matching ``UnifiedChatService._stream_provider_answer``.
    """
    await publisher.publish(AgentStreamEvent.token(execution_id, content=""))


async def emit_final_content_as_tokens(
    *,
    content: str,
    execution_id: str,
    publisher: StreamPublisher,
) -> None:
    """Publish precomputed final answer text as token events."""
    if not content:
        return

    words = content.split(" ")
    for index, word in enumerate(words):
        token = word if index == len(words) - 1 else f"{word} "
        await publisher.publish(AgentStreamEvent.token(execution_id, content=token))


async def complete_llm_step(
    provider: LLMProvider,
    *,
    request: AgentRequest,
    scratchpad: Scratchpad,
) -> ProviderCompletion:
    """Run a non-streaming LLM completion for an intermediate step."""
    messages = _scratchpad_to_chat_messages(scratchpad)
    config = request.config or AgentConfig()

    async def operation() -> ProviderCompletion:
        call = provider.complete_chat(
            cast(Any, messages),
            request.model,
            request.temperature,
            max_tokens=request.max_tokens,
        )
        if config.timeout_seconds is None:
            return await call
        return await asyncio.wait_for(call, timeout=config.timeout_seconds)

    policy = llm_retry_policy_from_config(config)
    return await retry_operation(operation, policy)


async def _iter_stream_with_timeout(
    stream: AsyncIterator[ProviderChunk],
    *,
    timeout_seconds: int | None,
) -> AsyncIterator[ProviderChunk]:
    """Yield stream chunks, enforcing a total budget including stalls between chunks."""
    if timeout_seconds is None:
        async for chunk in stream:
            yield chunk
        return

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    iterator = stream.__aiter__()
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AgentTimeoutError(timeout_seconds)
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            break
        except TimeoutError as exc:
            raise AgentTimeoutError(timeout_seconds) from exc
        yield chunk


async def _aclose_stream(stream: AsyncIterator[ProviderChunk]) -> None:
    """Best-effort async generator cleanup after normal completion or timeout."""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


def _scratchpad_to_chat_messages(scratchpad: Scratchpad) -> list[ScratchpadMessage]:
    """Convert scratchpad entries for provider chat calls."""
    messages: list[ScratchpadMessage] = []
    for message in scratchpad.to_message_context():
        if isinstance(message, AgentMessage):
            if message.role in ("system", "user", "assistant"):
                messages.append(message)
            continue
        messages.append(message)
    return messages
