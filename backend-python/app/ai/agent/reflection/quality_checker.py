"""Rule-based reflection checks (Part I § Reflection rules)."""

from __future__ import annotations

from app.ai.agent.executor.result_aggregator import AggregatedToolResults
from app.ai.agent.models.events import ReflectionDecision


def evaluate_rule_based(
    *,
    tool_results: AggregatedToolResults | None,
    llm_content: str | None,
) -> ReflectionDecision | None:
    """Return a decision when a rule applies; ``None`` when inconclusive."""
    if llm_content is not None and not llm_content.strip():
        return ReflectionDecision.RETRY_STEP

    if tool_results is not None and tool_results.records:
        if not tool_results.any_succeeded:
            return ReflectionDecision.REPLAN
        if not tool_results.all_succeeded:
            return ReflectionDecision.CONTINUE
        return ReflectionDecision.FINISH

    return None


def rule_reason(
    decision: ReflectionDecision,
    *,
    tool_results: AggregatedToolResults | None,
    llm_content: str | None,
) -> str:
    """Human-readable reason for a rule-based reflection decision."""
    if decision == ReflectionDecision.RETRY_STEP:
        return "Planner or LLM step returned empty content."
    if decision == ReflectionDecision.REPLAN:
        return "All tool calls in the latest step failed."
    if decision == ReflectionDecision.CONTINUE:
        return "Some tool calls failed; continuing with partial results."
    if decision == ReflectionDecision.FINISH:
        return "All tool calls in the latest step succeeded."
    if llm_content is not None or (tool_results and tool_results.records):
        return "Inconclusive step quality; escalating to LLM reflection."
    return "No rule matched the latest step."
