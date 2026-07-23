"""Main agent execution loop (Phase 8)."""

from __future__ import annotations

import json

from app.ai.agent.exceptions import AgentError
from app.ai.agent.executor.finalizer import finalize_execution
from app.ai.agent.executor.llm_step import complete_llm_step
from app.ai.agent.executor.result_aggregator import AggregatedToolResults, ToolRunRecord
from app.ai.agent.executor.tool_runner import ToolRunner
from app.ai.agent.interfaces.planner import Planner
from app.ai.agent.interfaces.streaming import StreamPublisher
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEvent, ReflectionDecision
from app.ai.agent.models.plan import ExecutionPlan, PlannedStep, StepAction
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.response import AgentResponse
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.planner.parser import build_iteration_limit_plan
from app.ai.agent.reflection.engine import ReflectionEngine
from app.ai.agent.scratchpad.scratchpad import Scratchpad
from app.ai.agent.scratchpad.store import ScratchpadStore, get_scratchpad_store
from app.ai.agent.state.manager import AgentStateManager
from app.ai.agent.streaming.publisher import NoOpStreamPublisher
from app.ai.tools.schemas import ToolCall, ToolExecutionContext, ToolResult
from app.ai.prompts.manager import PromptManager
from app.core.logging import get_logger
from app.providers.base import LLMProvider

_logger = get_logger(__name__)


class AgentExecutor:
    """Orchestrates planning, tool execution, and finalization for one agent run."""

    def __init__(
        self,
        planner: Planner,
        provider: LLMProvider,
        tool_runner: ToolRunner,
        *,
        stream_publisher: StreamPublisher | None = None,
        scratchpad_store: ScratchpadStore | None = None,
        reflection_engine: ReflectionEngine | None = None,
        prompt_manager: PromptManager | None = None,
    ) -> None:
        self._planner = planner
        self._provider = provider
        self._tool_runner = tool_runner
        self._publisher = stream_publisher or NoOpStreamPublisher()
        self._scratchpad_store = scratchpad_store or get_scratchpad_store()
        self._reflection_engine = reflection_engine
        self._prompt_manager = prompt_manager

    async def run(
        self,
        request: AgentRequest,
        context: AgentContext,
        *,
        tool_context: ToolExecutionContext,
    ) -> AgentResponse:
        """Execute the full ReAct loop until finalize or iteration limit."""
        config = request.config or AgentConfig()
        last_planner_content: str | None = None

        try:
            state = AgentStateManager.create_initial_state(
                context,
                config,
                scratchpad_store=self._scratchpad_store,
            )
            scratchpad = self._scratchpad_store.require(context.execution_id)
            if len(scratchpad) == 0:
                scratchpad.extend_messages(request.messages)

            await self._publisher.publish(AgentStreamEvent.start(context.execution_id))

            state = AgentStateManager.transition(state, AgentExecutionStatus.PLANNING)

            while state.has_remaining_iterations():
                state = AgentStateManager.begin_iteration(state)
                iteration_index = state.current_iteration - 1
                await self._publisher.publish(
                    AgentStreamEvent.planning(
                        context.execution_id,
                        iteration=iteration_index,
                    )
                )

                plan = await self._planner.plan_next(
                    request,
                    context,
                    iteration=iteration_index,
                )
                if plan.steps:
                    last_planner_content = plan.steps[0].reasoning

                if plan.is_final:
                    state = AgentStateManager.transition(
                        state,
                        AgentExecutionStatus.EXECUTING,
                    )
                    return await self._finalize_and_complete(
                        plan,
                        request=request,
                        context=context,
                        state=state,
                        scratchpad=scratchpad,
                        last_planner_content=last_planner_content,
                    )

                state = AgentStateManager.transition(
                    state,
                    AgentExecutionStatus.EXECUTING,
                )
                state, tool_results = await self._execute_tool_plan(
                    plan,
                    context=context,
                    state=state,
                    tool_context=tool_context,
                    scratchpad=scratchpad,
                )
                state, should_finalize, retry_plan = await self._maybe_reflect(
                    request=request,
                    context=context,
                    state=state,
                    scratchpad=scratchpad,
                    tool_results=tool_results,
                    llm_content=last_planner_content,
                    last_tool_plan=plan,
                )
                if retry_plan is not None:
                    state, _ = await self._execute_tool_plan(
                        retry_plan,
                        context=context,
                        state=state,
                        tool_context=tool_context,
                        scratchpad=scratchpad,
                    )
                if should_finalize:
                    finalize_plan = ExecutionPlan(
                        steps=[
                            PlannedStep(
                                step_id=f"finalize-reflect-{state.current_iteration}",
                                action=StepAction.FINALIZE,
                                reasoning=last_planner_content,
                            )
                        ],
                        iteration=state.current_iteration,
                        is_final=True,
                    )
                    return await self._finalize_and_complete(
                        finalize_plan,
                        request=request,
                        context=context,
                        state=state,
                        scratchpad=scratchpad,
                        last_planner_content=last_planner_content,
                    )
                if state.status != AgentExecutionStatus.PLANNING:
                    state = AgentStateManager.transition(
                        state,
                        AgentExecutionStatus.PLANNING,
                    )

            limit_plan = build_iteration_limit_plan(iteration=state.current_iteration)
            state = AgentStateManager.mark_iteration_limit_reached(state)
            state = AgentStateManager.transition(
                state,
                AgentExecutionStatus.EXECUTING,
            )
            return await self._finalize_and_complete(
                limit_plan,
                request=request,
                context=context,
                state=state,
                scratchpad=scratchpad,
                last_planner_content=last_planner_content,
            )
        except AgentError as exc:
            await self._publisher.publish(
                AgentStreamEvent.error(
                    context.execution_id,
                    code="agent_error",
                    message=str(exc),
                )
            )
            raise
        except Exception:
            _logger.exception(
                "Agent execution failed",
                execution_id=context.execution_id,
            )
            await self._publisher.publish(
                AgentStreamEvent.error(
                    context.execution_id,
                    code="agent_error",
                    message="Agent execution failed.",
                )
            )
            raise
        finally:
            await self._publisher.close()
            self._scratchpad_store.remove(context.execution_id)

    async def execute_plan(
        self,
        plan: ExecutionPlan,
        request: AgentRequest,
        context: AgentContext,
        *,
        tool_context: ToolExecutionContext | None = None,
    ) -> AgentResponse:
        """Execute a single planner output (one iteration)."""
        config = request.config or AgentConfig()
        state = AgentStateManager.create_initial_state(
            context,
            config,
            scratchpad_store=self._scratchpad_store,
        )
        scratchpad = self._scratchpad_store.require(context.execution_id)
        if len(scratchpad) == 0:
            scratchpad.extend_messages(request.messages)

        state = AgentStateManager.transition(state, AgentExecutionStatus.PLANNING)

        if plan.is_final:
            state = AgentStateManager.transition(
                state,
                AgentExecutionStatus.EXECUTING,
            )
            return await self._finalize_and_complete(
                plan,
                request=request,
                context=context,
                state=state,
                scratchpad=scratchpad,
            )

        if tool_context is None:
            raise ValueError("tool_context is required for non-final plans")

        state = AgentStateManager.transition(state, AgentExecutionStatus.EXECUTING)
        state, _ = await self._execute_tool_plan(
            plan,
            context=context,
            state=state,
            tool_context=tool_context,
            scratchpad=scratchpad,
        )
        return AgentResponse(
            content="",
            tools_used=list(state.tools_used),
            iterations=state.current_iteration,
            finish_reason="continue",
        )

    async def execute_step(
        self,
        step: PlannedStep,
        request: AgentRequest,
        context: AgentContext,
        *,
        tool_context: ToolExecutionContext | None = None,
    ) -> object:
        """Execute one planned step."""
        scratchpad = self._scratchpad_store.get(context.execution_id)
        if scratchpad is None:
            scratchpad = self._scratchpad_store.create(context.execution_id)
            scratchpad.extend_messages(request.messages)

        if step.action == StepAction.TOOL_CALL:
            if tool_context is None:
                raise ValueError("tool_context is required for tool steps")
            if step.tool_calls:
                scratchpad.append_provider_message(
                    _assistant_tool_call_message(step.reasoning, step.tool_calls)
                )
            results = await self._tool_runner.run_tool_steps(
                [step],
                execution_id=context.execution_id,
                tool_context=tool_context,
            )
            _record_tool_results(scratchpad, results.records)
            return results

        if step.action == StepAction.LLM:
            completion = await complete_llm_step(
                self._provider,
                request=request,
                scratchpad=scratchpad,
            )
            if completion.content:
                scratchpad.append_thought(completion.content)
            return completion

        if step.action == StepAction.FINALIZE:
            return await finalize_execution(
                ExecutionPlan(steps=[step], iteration=0, is_final=True),
                request=request,
                scratchpad=scratchpad,
                provider=self._provider,
                execution_id=context.execution_id,
                publisher=self._publisher,
            )

        raise ValueError(f"Unsupported step action: {step.action}")

    async def _maybe_reflect(
        self,
        *,
        request: AgentRequest,
        context: AgentContext,
        state: AgentExecutionState,
        scratchpad: Scratchpad,
        tool_results: AggregatedToolResults | None,
        llm_content: str | None,
        last_tool_plan: ExecutionPlan,
    ) -> tuple[AgentExecutionState, bool, ExecutionPlan | None]:
        """Run optional reflection and map the decision to executor control flow."""
        config = request.config or AgentConfig()
        if not config.reflection_enabled:
            return state, False, None
        if tool_results is None or not tool_results.records:
            return state, False, None
        if state.reflection_count >= config.max_reflections:
            return state, False, None

        engine = self._resolve_reflection_engine()
        state = AgentStateManager.transition(
            state,
            AgentExecutionStatus.REFLECTING,
        )
        result = await engine.reflect(
            request=request,
            context=context,
            scratchpad=scratchpad,
            tool_results=tool_results,
            llm_content=llm_content,
        )
        state = AgentStateManager.record_reflection(state)
        await self._publisher.publish(
            AgentStreamEvent.reflection(
                context.execution_id,
                decision=result.decision,
                reason=result.reason,
            )
        )

        if result.decision == ReflectionDecision.REPLAN:
            state = AgentStateManager.transition(
                state,
                AgentExecutionStatus.PLANNING,
            )
            return state, False, None

        if result.decision == ReflectionDecision.RETRY_STEP:
            state = AgentStateManager.transition(
                state,
                AgentExecutionStatus.EXECUTING,
            )
            return state, False, last_tool_plan

        if result.decision == ReflectionDecision.FINISH:
            state = AgentStateManager.transition(
                state,
                AgentExecutionStatus.EXECUTING,
            )
            return state, True, None

        state = AgentStateManager.transition(state, AgentExecutionStatus.PLANNING)
        return state, False, None

    def _resolve_reflection_engine(self) -> ReflectionEngine:
        if self._reflection_engine is not None:
            return self._reflection_engine
        if self._prompt_manager is None:
            raise ValueError(
                "prompt_manager is required when reflection is enabled "
                "and no reflection_engine is provided"
            )
        self._reflection_engine = ReflectionEngine(
            provider=self._provider,
            prompt_manager=self._prompt_manager,
        )
        return self._reflection_engine

    async def _execute_tool_plan(
        self,
        plan: ExecutionPlan,
        *,
        context: AgentContext,
        state: AgentExecutionState,
        tool_context: ToolExecutionContext,
        scratchpad: Scratchpad,
    ) -> tuple[AgentExecutionState, AggregatedToolResults | None]:
        tool_steps = [
            step for step in plan.steps if step.action == StepAction.TOOL_CALL
        ]
        if not tool_steps:
            return state, None

        for step in tool_steps:
            if step.tool_calls:
                scratchpad.append_provider_message(
                    _assistant_tool_call_message(step.reasoning, step.tool_calls)
                )

        results = await self._tool_runner.run_tool_steps(
            tool_steps,
            execution_id=context.execution_id,
            tool_context=tool_context,
        )
        _record_tool_results(scratchpad, results.records)

        for tool_name in results.tools_used:
            state = AgentStateManager.record_tool_used(state, tool_name)
        return state, results

    async def _finalize_and_complete(
        self,
        plan: ExecutionPlan,
        *,
        request: AgentRequest,
        context: AgentContext,
        state: AgentExecutionState,
        scratchpad: Scratchpad,
        last_planner_content: str | None = None,
    ) -> AgentResponse:
        result = await finalize_execution(
            plan,
            request=request,
            scratchpad=scratchpad,
            provider=self._provider,
            execution_id=context.execution_id,
            publisher=self._publisher,
            last_planner_content=last_planner_content,
        )
        state = AgentStateManager.transition(state, AgentExecutionStatus.COMPLETED)
        await self._publisher.publish(
            AgentStreamEvent.complete(
                context.execution_id,
                finish_reason=result.finish_reason,
                tools_used=list(state.tools_used),
            )
        )
        return AgentResponse(
            content=result.content,
            tools_used=list(state.tools_used),
            iterations=state.current_iteration,
            finish_reason=result.finish_reason,
        )


def _record_tool_results(
    scratchpad: Scratchpad,
    records: list[ToolRunRecord],
) -> None:
    for record in records:
        call_id = record.call.call_id or record.step_id
        scratchpad.append_tool_result(
            tool_call_id=call_id,
            content=_format_tool_result(record.result),
        )


def _format_tool_result(result: ToolResult) -> str:
    payload: dict[str, object] = {
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "error_code": result.error_code,
    }
    return json.dumps(payload)


def _assistant_tool_call_message(
    content: str | None,
    tool_calls: list[ToolCall],
) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call.call_id or call.name,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in tool_calls
        ],
    }
