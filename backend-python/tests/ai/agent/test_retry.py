"""Tests for agent retry framework (Phase 4)."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.ai.agent import RetryPolicy
from app.ai.agent.exceptions import (
    AgentError,
    AgentIterationLimitError,
    AgentTimeoutError,
)
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.retry import (
    LLMRetryPolicy,
    ToolRetryPolicy,
    is_non_retryable_agent_error,
    is_retryable_agent_error,
    llm_retry_policy_from_config,
    retry_operation,
    tool_retry_policy_from_config,
)
from app.ai.agent.scratchpad import ScratchpadNotFoundError
from app.core.retry import retry_async


class _SampleModel(BaseModel):
    value: int


def test_is_retryable_agent_error_matches_part_i_transient_failures() -> None:
    assert is_retryable_agent_error(asyncio.TimeoutError()) is True
    assert is_retryable_agent_error(ConnectionError("temporary failure")) is True

    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(429, request=request)
    assert (
        is_retryable_agent_error(
            httpx.HTTPStatusError("rate limit", request=request, response=response)
        )
        is True
    )


def test_is_non_retryable_agent_error_matches_part_i_permanent_failures() -> None:
    assert (
        is_non_retryable_agent_error(AgentIterationLimitError(max_iterations=5)) is True
    )
    assert is_non_retryable_agent_error(AgentTimeoutError(timeout_seconds=30)) is True
    assert (
        is_non_retryable_agent_error(ValidationError.from_exception_data("Sample", []))
        is True
    )
    assert is_non_retryable_agent_error(PermissionError("forbidden")) is True
    assert is_non_retryable_agent_error(FileNotFoundError("missing.txt")) is True
    assert is_non_retryable_agent_error(ScratchpadNotFoundError("exec-1")) is True

    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(404, request=request)
    not_found = httpx.HTTPStatusError("not found", request=request, response=response)
    assert is_non_retryable_agent_error(not_found) is False
    assert is_retryable_agent_error(not_found) is False


def test_is_retryable_agent_error_rejects_validation_errors() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _SampleModel.model_validate({"value": "not-an-int"})

    assert is_retryable_agent_error(exc_info.value) is False


def test_llm_and_tool_policies_satisfy_retry_policy_protocol() -> None:
    llm_policy: RetryPolicy = LLMRetryPolicy()
    tool_policy: RetryPolicy = ToolRetryPolicy()

    assert llm_policy.max_retries == 3
    assert llm_policy.base_delay_seconds == 1.0
    assert tool_policy.max_retries == 3
    assert tool_policy.base_delay_seconds == 1.0


def test_retry_policies_from_config_use_agent_defaults() -> None:
    config = AgentConfig(max_retries=5, retry_base_delay_seconds=0.5)

    llm_policy = llm_retry_policy_from_config(config)
    tool_policy = tool_retry_policy_from_config(config)

    assert llm_policy.max_retries == 5
    assert llm_policy.base_delay_seconds == 0.5
    assert tool_policy.max_retries == 5
    assert tool_policy.base_delay_seconds == 0.5


@pytest.mark.anyio
async def test_retry_operation_succeeds_without_retry() -> None:
    policy = LLMRetryPolicy()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_operation(operation, policy)

    assert result == "ok"
    assert calls == 1


@pytest.mark.anyio
async def test_retry_operation_retries_transient_failure_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = LLMRetryPolicy(max_retries=3)
    calls = 0
    sleep_calls: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary")
        return "recovered"

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("app.core.retry.asyncio.sleep", fake_sleep)

    result = await retry_operation(operation, policy)

    assert result == "recovered"
    assert calls == 2
    assert sleep_calls


@pytest.mark.anyio
async def test_retry_operation_does_not_retry_non_retryable_errors() -> None:
    policy = LLMRetryPolicy(max_retries=3)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise AgentIterationLimitError(max_iterations=5)

    with pytest.raises(AgentIterationLimitError):
        await retry_operation(operation, policy)

    assert calls == 1


@pytest.mark.anyio
async def test_retry_operation_exhausts_policy_budget_on_persistent_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = LLMRetryPolicy(max_retries=3)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ConnectionError("still failing")

    monkeypatch.setattr("app.core.retry.asyncio.sleep", AsyncMock())

    with pytest.raises(ConnectionError):
        await retry_operation(operation, policy)

    assert calls == 3


@pytest.mark.anyio
async def test_retry_operation_with_zero_retries_runs_once() -> None:
    policy = LLMRetryPolicy(max_retries=0)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ConnectionError("temporary")

    with pytest.raises(ConnectionError):
        await retry_operation(operation, policy)

    assert calls == 1


@pytest.mark.anyio
async def test_retry_operation_delegates_backoff_to_core_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = LLMRetryPolicy(max_retries=2, base_delay_seconds=1.0)
    calls = 0
    sleep_calls: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary")
        return "ok"

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("app.core.retry.asyncio.sleep", fake_sleep)

    await retry_operation(operation, policy)

    assert sleep_calls
    assert calls == 2


@pytest.mark.anyio
async def test_core_retry_async_still_passes_with_agent_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary")
        return "ok"

    monkeypatch.setattr("app.core.retry.asyncio.sleep", AsyncMock())

    result = await retry_async(operation, is_retryable=is_retryable_agent_error)

    assert result == "ok"
    assert calls == 2


def test_retry_modules_have_no_transport_or_domain_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_paths = [
        repo_root / "app/ai/agent/retry/classifier.py",
        repo_root / "app/ai/agent/retry/policies.py",
        repo_root / "app/ai/agent/retry/executor.py",
    ]
    forbidden_roots = ("app.services", "app.db", "app.schemas.chat", "fastapi")

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        for forbidden in forbidden_roots:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in imported_modules
            ), f"{module_path.name} must not import {forbidden}"


def test_generic_agent_error_is_not_retryable() -> None:
    assert is_retryable_agent_error(AgentError("unexpected")) is False
