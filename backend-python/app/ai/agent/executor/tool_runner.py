"""Multi-tool execution via :class:`ToolExecutor` (Phase 7)."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from app.ai.agent.executor.dependency_resolver import resolve_step_batches
from app.ai.agent.executor.result_aggregator import (
    AggregatedToolResults,
    ToolRunRecord,
    aggregate_tool_results,
)
from app.ai.agent.interfaces.streaming import StreamPublisher
from app.ai.agent.interfaces.retry import RetryPolicy
from app.ai.agent.models.events import AgentStreamEvent
from app.ai.agent.models.plan import PlannedStep
from app.ai.agent.models.state import AgentExecutionState
from app.ai.agent.retry.classifier import is_retryable_tool_result
from app.ai.agent.retry.executor import retry_operation
from app.ai.agent.retry.policies import ToolRetryPolicy
from app.ai.agent.scratchpad.scratchpad import Scratchpad
from app.ai.agent.streaming.publisher import NoOpStreamPublisher
from app.ai.hitl.exceptions import HitlError
from app.ai.hitl.policy import ApprovalPolicy
from app.ai.hitl.service import AgentApprovalService, raise_pause
from app.ai.observability.tracing.spans import (
    agent_span,
    elapsed_ms_since,
    record_agent_tool_call_attributes,
    reset_tool_retry_count,
    set_tool_retry_count,
)
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolExecutionContext, ToolResult


class ToolExecutionRetryableError(Exception):
    """Raised to trigger :func:`retry_operation` for transient tool failures."""

    def __init__(self, result: ToolResult) -> None:
        self.result = result
        super().__init__(result.error or "retryable tool failure")


@dataclass(frozen=True, slots=True)
class _ToolResultRetryPolicy:
    """Adapter so :func:`retry_operation` can retry normalized tool failures."""

    inner: ToolRetryPolicy

    @property
    def max_retries(self) -> int:
        return self.inner.max_retries

    @property
    def base_delay_seconds(self) -> float:
        return self.inner.base_delay_seconds

    def is_retryable(self, exc: BaseException) -> bool:
        if isinstance(exc, ToolExecutionRetryableError):
            return True
        return self.inner.is_retryable(exc)


class ToolRunner:
    """Run planned tool steps through :class:`ToolExecutor` with streaming and retry."""

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry | None = None,
        stream_publisher: StreamPublisher | None = None,
        retry_policy: ToolRetryPolicy | None = None,
        parallel_tools_enabled: bool = False,
        hitl_enabled: bool = False,
        approval_policy: ApprovalPolicy | None = None,
        approval_service: AgentApprovalService | None = None,
    ) -> None:
        self._executor = tool_executor
        self._registry = tool_registry
        self._publisher = stream_publisher or NoOpStreamPublisher()
        self._retry_policy = retry_policy or ToolRetryPolicy()
        self._parallel_tools_enabled = parallel_tools_enabled
        self._hitl_enabled = hitl_enabled
        self._approval_policy = approval_policy
        self._approval_service = approval_service

    async def run_tool_steps(
        self,
        steps: list[PlannedStep],
        *,
        execution_id: str,
        tool_context: ToolExecutionContext,
        scratchpad: Scratchpad | None = None,
        state: AgentExecutionState | None = None,
        session_id: uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> AggregatedToolResults:
        """Execute tool-call steps respecting dependencies and parallel settings."""
        batches = resolve_step_batches(steps)
        records: list[ToolRunRecord] = []

        for batch in batches:
            batch_records = await self._run_step_batch(
                batch,
                execution_id=execution_id,
                tool_context=tool_context,
                scratchpad=scratchpad,
                state=state,
                session_id=session_id,
                owner_id=owner_id,
                provider=provider,
                model=model,
            )
            records.extend(batch_records)

        return aggregate_tool_results(records)

    async def _run_step_batch(
        self,
        batch: list[PlannedStep],
        *,
        execution_id: str,
        tool_context: ToolExecutionContext,
        scratchpad: Scratchpad | None,
        state: AgentExecutionState | None,
        session_id: uuid.UUID | None,
        owner_id: uuid.UUID | None,
        provider: str | None,
        model: str | None,
    ) -> list[ToolRunRecord]:
        if len(batch) > 1 and self._parallel_tools_enabled:
            nested = await asyncio.gather(
                *[
                    self._run_single_step(
                        step,
                        execution_id=execution_id,
                        tool_context=tool_context,
                        scratchpad=scratchpad,
                        state=state,
                        session_id=session_id,
                        owner_id=owner_id,
                        provider=provider,
                        model=model,
                    )
                    for step in batch
                ]
            )
            return [record for step_records in nested for record in step_records]

        records: list[ToolRunRecord] = []
        for step in batch:
            records.extend(
                await self._run_single_step(
                    step,
                    execution_id=execution_id,
                    tool_context=tool_context,
                    scratchpad=scratchpad,
                    state=state,
                    session_id=session_id,
                    owner_id=owner_id,
                    provider=provider,
                    model=model,
                )
            )
        return records

    async def _run_single_step(
        self,
        step: PlannedStep,
        *,
        execution_id: str,
        tool_context: ToolExecutionContext,
        scratchpad: Scratchpad | None,
        state: AgentExecutionState | None,
        session_id: uuid.UUID | None,
        owner_id: uuid.UUID | None,
        provider: str | None,
        model: str | None,
    ) -> list[ToolRunRecord]:
        if not step.tool_calls:
            return []

        await self._maybe_pause_for_approval(
            step,
            execution_id=execution_id,
            scratchpad=scratchpad,
            state=state,
            session_id=session_id,
            owner_id=owner_id,
            provider=provider,
            model=model,
        )

        if len(step.tool_calls) > 1 and self._parallel_tools_enabled:
            results = await asyncio.gather(
                *[
                    self._run_single_tool(
                        call,
                        step_id=step.step_id,
                        execution_id=execution_id,
                        tool_context=tool_context,
                    )
                    for call in step.tool_calls
                ]
            )
            return list(results)

        records: list[ToolRunRecord] = []
        for call in step.tool_calls:
            records.append(
                await self._run_single_tool(
                    call,
                    step_id=step.step_id,
                    execution_id=execution_id,
                    tool_context=tool_context,
                )
            )
        return records

    async def _maybe_pause_for_approval(
        self,
        step: PlannedStep,
        *,
        execution_id: str,
        scratchpad: Scratchpad | None,
        state: AgentExecutionState | None,
        session_id: uuid.UUID | None,
        owner_id: uuid.UUID | None,
        provider: str | None,
        model: str | None,
    ) -> None:
        if not self._hitl_enabled:
            return
        if self._registry is None or self._approval_policy is None:
            raise HitlError(
                "HITL is enabled but ToolRunner is missing ToolRegistry or ApprovalPolicy."
            )
        if not _step_requires_approval(step, self._registry, self._approval_policy):
            return
        if self._approval_service is None:
            raise HitlError(
                "HITL is enabled but ToolRunner is missing AgentApprovalService."
            )
        if scratchpad is None or state is None:
            raise HitlError(
                "Approval-required tool call cannot pause without scratchpad and state."
            )
        if session_id is None or owner_id is None:
            raise HitlError(
                "Approval-required tool call cannot pause without session_id and owner_id."
            )

        approval = await self._approval_service.pause(
            step,
            scratchpad=scratchpad,
            state=state,
            session_id=session_id,
            owner_id=owner_id,
            execution_id=execution_id,
            stream_publisher=self._publisher,
            provider=provider,
            model=model,
        )
        raise_pause(approval)

    async def _run_single_tool(
        self,
        call: ToolCall,
        *,
        step_id: str,
        execution_id: str,
        tool_context: ToolExecutionContext,
    ) -> ToolRunRecord:
        call_id = _resolve_call_id(call, step_id=step_id)
        normalized_call = call.model_copy(update={"call_id": call_id})

        await self._publisher.publish(
            AgentStreamEvent.tool_start(
                execution_id,
                tool_name=normalized_call.name,
                call_id=call_id,
            )
        )

        dispatch_start = time.perf_counter()
        with agent_span("tool_call") as span:
            try:
                result = await self._execute_with_retry(normalized_call, tool_context)
            except Exception:
                await self._publisher.publish(
                    AgentStreamEvent.tool_end(
                        execution_id,
                        tool_name=normalized_call.name,
                        call_id=call_id,
                        success=False,
                    )
                )
                record_agent_tool_call_attributes(
                    span,
                    tool_name=normalized_call.name,
                    latency_ms=elapsed_ms_since(dispatch_start),
                )
                raise

            record_agent_tool_call_attributes(
                span,
                tool_name=normalized_call.name,
                latency_ms=elapsed_ms_since(dispatch_start),
            )

        await self._publisher.publish(
            AgentStreamEvent.tool_end(
                execution_id,
                tool_name=normalized_call.name,
                call_id=call_id,
                success=result.success,
            )
        )

        return ToolRunRecord(
            step_id=step_id,
            call=normalized_call,
            result=result,
        )

    async def _execute_with_retry(
        self,
        call: ToolCall,
        tool_context: ToolExecutionContext,
    ) -> ToolResult:
        policy: RetryPolicy = _ToolResultRetryPolicy(self._retry_policy)
        attempt = 0

        async def operation() -> ToolResult:
            nonlocal attempt
            retry_count = attempt
            attempt += 1
            token = set_tool_retry_count(retry_count)
            try:
                result = await self._executor.execute(call, tool_context)
            finally:
                reset_tool_retry_count(token)
            if not result.success and is_retryable_tool_result(result):
                raise ToolExecutionRetryableError(result)
            return result

        try:
            return await retry_operation(operation, policy)
        except ToolExecutionRetryableError as exc:
            return exc.result


def _step_requires_approval(
    step: PlannedStep,
    registry: ToolRegistry,
    policy: ApprovalPolicy,
) -> bool:
    for call in step.tool_calls:
        tool = registry.get(call.name)
        if tool is not None and policy.requires_approval(tool):
            return True
    return False


def _resolve_call_id(call: ToolCall, *, step_id: str) -> str:
    if call.call_id is not None:
        return call.call_id
    return f"{step_id}-{uuid.uuid4().hex[:8]}"
