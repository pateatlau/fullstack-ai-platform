"""Tests for AgentEvalRunner."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.evaluation.datasets import EvalCase
from app.ai.evaluation.runners import AgentEvalRunner
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "llm_provider": "openai",
        "openai_model": "gpt-4o-mini",
        "default_temperature": 0.7,
        "agent_runtime_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _agent_case(**overrides: object) -> EvalCase:
    base = {
        "id": "agent_echo",
        "level": "agent",
        "goal": "Echo hello",
        "instructions": "Use the echo tool.",
        "expected_tool_calls": ("echo",),
        "expected_outcome": "hello",
    }
    base.update(overrides)
    return EvalCase(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_agent_eval_runner_passes_with_expected_tools_and_outcome() -> None:
    runner = AgentEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
    )

    result = await runner.run_case(_agent_case())

    assert result.passed is True
    assert result.skipped is False
    assert result.tool_calls_correct is True
    assert result.model == "gpt-4o-mini"
    assert result.temperature == 0.7
    assert result.model_version is None
    assert result.seed is None
    assert result.prompt_version is None


@pytest.mark.anyio
async def test_agent_eval_runner_rejects_unsupported_expected_tools() -> None:
    runner = AgentEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
    )

    result = await runner.run_case(_agent_case(expected_tool_calls=("web_search",)))

    assert result.passed is False
    assert result.error is not None
    assert "unsupported expected_tool_calls" in result.error
    assert "web_search" in result.error
    assert "echo" in result.error


@pytest.mark.anyio
async def test_agent_eval_runner_fails_when_tool_calls_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai.evaluation import runners as runners_module

    original_build = runners_module._build_agent_eval_provider

    def _short_circuit_provider(case, *, model, temperature):  # type: ignore[no-untyped-def]
        provider = original_build(case, model=model, temperature=temperature)
        provider.tools_invoked = ["echo"]
        return provider

    monkeypatch.setattr(
        runners_module,
        "_build_agent_eval_provider",
        _short_circuit_provider,
    )
    runner = AgentEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
    )

    result = await runner.run_case(_agent_case(expected_tool_calls=("echo", "echo")))

    assert result.passed is False
    assert result.tool_calls_correct is False


@pytest.mark.anyio
async def test_agent_eval_runner_skips_when_flag_off() -> None:
    runner = AgentEvalRunner(
        settings=_settings(agent_runtime_enabled=False),
        prompt_manager=create_prompt_manager(),
    )

    result = await runner.run_case(_agent_case())

    assert result.skipped is True
    assert result.skip_reason == "AGENT_RUNTIME_ENABLED=false"
    assert result.model is None


@pytest.mark.anyio
async def test_agent_eval_runner_uses_case_model_and_temperature_overrides() -> None:
    runner = AgentEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
    )

    result = await runner.run_case(
        _agent_case(model="gpt-4o", temperature=0.2),
    )

    assert result.model == "gpt-4o"
    assert result.temperature == 0.2


@pytest.mark.anyio
async def test_agent_eval_runner_passes_with_multi_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai.evaluation import runners as runners_module

    captured_provider = None
    original_build = runners_module._build_agent_eval_provider

    def _capture_provider(case, *, model, temperature):  # type: ignore[no-untyped-def]
        nonlocal captured_provider
        captured_provider = original_build(case, model=model, temperature=temperature)
        return captured_provider

    monkeypatch.setattr(
        runners_module,
        "_build_agent_eval_provider",
        _capture_provider,
    )
    runner = AgentEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
    )

    result = await runner.run_case(
        _agent_case(
            id="agent_multi_echo",
            goal="Echo twice",
            expected_tool_calls=("echo", "echo"),
            expected_outcome="done twice",
        )
    )

    assert captured_provider is not None
    assert len(captured_provider.tools_invoked) == 2
    assert captured_provider.tools_invoked == ["echo", "echo"]
    assert result.passed is True
    assert result.tool_calls_correct is True


@pytest.mark.anyio
async def test_agent_eval_runner_passes_when_hitl_enabled_without_approval_tools() -> (
    None
):
    runner = AgentEvalRunner(
        settings=_settings(hitl_enabled=True, hitl_required_tool_names=["web_search"]),
        prompt_manager=create_prompt_manager(),
    )

    result = await runner.run_case(_agent_case())

    assert result.passed is True
    assert result.error is None


@pytest.mark.anyio
async def test_agent_eval_runner_records_error_without_fabricated_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AgentEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
    )
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=RuntimeError("agent failed"))

    monkeypatch.setattr(
        "app.ai.evaluation.runners.create_default_agent",
        lambda **_kwargs: agent,
    )

    result = await runner.run_case(_agent_case())

    assert result.passed is False
    assert result.error == "agent failed"
    assert result.model == "gpt-4o-mini"
    assert result.model_version is None
    assert result.seed is None
