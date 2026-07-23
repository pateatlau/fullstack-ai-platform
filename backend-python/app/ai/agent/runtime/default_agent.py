"""Default agent runtime entry point (Phase 10)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

from app.ai.agent.executor.agent_executor import AgentExecutor
from app.ai.agent.executor.tool_runner import ToolRunner
from app.ai.agent.interfaces.streaming import StreamPublisher
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEvent
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.response import AgentResponse
from app.ai.agent.planner.react_planner import ReActPlanner
from app.ai.agent.scratchpad.store import ScratchpadStore, get_scratchpad_store
from app.ai.agent.streaming.publisher import NoOpStreamPublisher, QueueStreamPublisher
from app.ai.prompts.manager import PromptManager
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolExecutionContext
from app.core.caller import CallerContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.factory import ProviderFactory

_logger = get_logger(__name__)


class DefaultAgent:
    """Lifecycle entry point that delegates to :class:`AgentExecutor`."""

    def __init__(
        self,
        *,
        settings: Settings,
        tool_registry: ToolRegistry,
        prompt_manager: PromptManager,
        tool_executor: ToolExecutor,
        scratchpad_store: ScratchpadStore | None = None,
    ) -> None:
        self._settings = settings
        self._tool_registry = tool_registry
        self._prompt_manager = prompt_manager
        self._tool_executor = tool_executor
        self._scratchpad_store = scratchpad_store or get_scratchpad_store()

    async def run(
        self,
        request: AgentRequest,
        context: AgentContext,
    ) -> AgentResponse:
        """Run one agent execution to completion without streaming events."""
        publisher = NoOpStreamPublisher()
        executor = self._create_executor(request, publisher)
        tool_context = self._resolve_tool_context(context)
        response = await executor.run(
            request,
            context,
            tool_context=tool_context,
        )
        self._log_execution_complete(context, response)
        return response

    def stream(
        self,
        request: AgentRequest,
        context: AgentContext,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Yield typed progress events until the execution completes."""
        return self._stream(request, context)

    async def _stream(
        self,
        request: AgentRequest,
        context: AgentContext,
    ) -> AsyncIterator[AgentStreamEvent]:
        publisher = QueueStreamPublisher()
        executor = self._create_executor(request, publisher)
        tool_context = self._resolve_tool_context(context)
        task = asyncio.create_task(
            executor.run(
                request,
                context,
                tool_context=tool_context,
            )
        )
        try:
            while True:
                event = await publisher.queue.get()
                if event is None:
                    break
                yield event
            response = await task
            self._log_execution_complete(context, response)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def _create_executor(
        self,
        request: AgentRequest,
        publisher: StreamPublisher,
    ) -> AgentExecutor:
        config = request.config or AgentConfig()
        provider = ProviderFactory.get_provider(request.provider, self._settings)
        tool_runner = ToolRunner(
            tool_executor=self._tool_executor,
            stream_publisher=publisher,
            parallel_tools_enabled=config.parallel_tools_enabled,
        )
        planner = ReActPlanner(
            provider=provider,
            tool_registry=self._tool_registry,
            prompt_manager=self._prompt_manager,
            scratchpad_store=self._scratchpad_store,
        )
        return AgentExecutor(
            planner=planner,
            provider=provider,
            tool_runner=tool_runner,
            stream_publisher=publisher,
            scratchpad_store=self._scratchpad_store,
            prompt_manager=self._prompt_manager,
        )

    @staticmethod
    def _resolve_tool_context(context: AgentContext) -> ToolExecutionContext:
        caller = context.caller
        if caller is None:
            caller = CallerContext.anonymous(guest_id=uuid.uuid4())
        return ToolExecutionContext(
            caller=caller,
            request_id=context.request_id,
        )

    @staticmethod
    def _log_execution_complete(
        context: AgentContext,
        response: AgentResponse,
    ) -> None:
        _logger.info(
            "Agent execution completed",
            agent_execution_id=context.execution_id,
            agent_iterations=response.iterations,
            agent_tools_used=list(response.tools_used),
        )
