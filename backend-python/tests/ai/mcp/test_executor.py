"""Tests for MCP tool execution adapter (Phase 5).

Tests cover:
- Success path: MCP tool returns result → ToolResult(success=True)
- MCP error path: remote error → ToolResult(success=False, error_code="mcp_error")
- Connection error path → error_code="mcp_connection_error"
- Timeout error path → error_code="timeout"
- Unknown error path → error_code="unknown_error"
- ToolHandler Protocol compliance (type-checking)
- Structured logging (no raw arguments/responses)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai.mcp.exceptions import (
    McpConnectionError,
    McpToolExecutionError,
)
from app.ai.mcp.executor import McpToolExecutionAdapter
from app.ai.tools.schemas import ToolExecutionContext, ToolResult
from app.core.caller import CallerContext


@pytest.fixture
def mock_client() -> AsyncMock:
    """Fake MCP client for testing."""
    client = AsyncMock()
    return client


@pytest.fixture
def execution_context() -> ToolExecutionContext:
    """Test execution context."""
    return ToolExecutionContext(
        caller=CallerContext(kind="user", user_id=uuid.uuid4()),
        request_id="test-request-456",
    )


@pytest.fixture
def adapter(mock_client: AsyncMock) -> McpToolExecutionAdapter:
    """Create test adapter with fake client."""
    return McpToolExecutionAdapter(
        server_name="test-server",
        tool_name="test_tool",
        client=mock_client,
        metadata={
            "source": "mcp",
            "server_name": "test-server",
            "transport": "stdio",
            "original_name": "test_tool",
        },
    )


class TestMcpToolExecutionAdapter:
    """Test suite for McpToolExecutionAdapter."""

    def test_adapter_initialization(self, mock_client: AsyncMock) -> None:
        """Test adapter constructor stores server_name, tool_name, client, metadata."""
        adapter = McpToolExecutionAdapter(
            server_name="filesystem",
            tool_name="read_file",
            client=mock_client,
            metadata={"source": "mcp", "transport": "stdio"},
        )

        assert adapter.server_name == "filesystem"
        assert adapter.tool_name == "read_file"
        assert adapter.client is mock_client
        assert adapter.metadata == {"source": "mcp", "transport": "stdio"}

    def test_adapter_initialization_default_metadata(
        self, mock_client: AsyncMock
    ) -> None:
        """Test adapter initializes with empty metadata dict by default."""
        adapter = McpToolExecutionAdapter(
            server_name="github",
            tool_name="create_issue",
            client=mock_client,
        )

        assert adapter.metadata == {}

    @pytest.mark.anyio
    async def test_execute_success(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test successful MCP tool execution returns ToolResult(success=True)."""
        mock_mcp_result = {"status": "ok", "data": {"file_content": "Hello, world!"}}
        mock_client.call_tool.return_value = mock_mcp_result

        result = await adapter.execute(
            args={"path": "/tmp/test.txt"},
            context=execution_context,
        )

        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.data == mock_mcp_result
        assert result.error is None
        assert result.error_code is None
        assert result.metadata["server_name"] == "test-server"
        assert result.metadata["tool_name"] == "test_tool"
        assert result.metadata["source"] == "mcp"

        mock_client.call_tool.assert_awaited_once_with(
            "test_tool", {"path": "/tmp/test.txt"}
        )

    @pytest.mark.anyio
    async def test_execute_mcp_tool_execution_error(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test MCP remote error returns ToolResult(success=False, error_code="mcp_error")."""
        mock_client.call_tool.side_effect = McpToolExecutionError(
            "Tool execution failed on remote server"
        )

        result = await adapter.execute(
            args={"query": "test"},
            context=execution_context,
        )

        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.data is None
        assert result.error_code == "mcp_error"
        assert result.error is not None
        assert "MCP tool execution error" in result.error
        assert result.metadata["server_name"] == "test-server"
        assert result.metadata["tool_name"] == "test_tool"
        assert result.metadata["source"] == "mcp"

    @pytest.mark.anyio
    async def test_execute_connection_error(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test connection error returns ToolResult(error_code="mcp_connection_error")."""
        mock_client.call_tool.side_effect = McpConnectionError(
            "Connection to MCP server lost"
        )

        result = await adapter.execute(
            args={"action": "test"},
            context=execution_context,
        )

        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.data is None
        assert result.error_code == "mcp_connection_error"
        assert result.error is not None
        assert "MCP connection error" in result.error
        assert result.metadata["server_name"] == "test-server"
        assert result.metadata["tool_name"] == "test_tool"
        assert result.metadata["source"] == "mcp"

    @pytest.mark.anyio
    async def test_execute_timeout_error(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test timeout returns ToolResult(error_code="timeout")."""
        mock_client.call_tool.side_effect = asyncio.TimeoutError("Tool call timed out")

        result = await adapter.execute(
            args={"slow_operation": True},
            context=execution_context,
        )

        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.data is None
        assert result.error_code == "timeout"
        assert result.error is not None
        assert "timed out" in result.error.lower()
        assert result.metadata["server_name"] == "test-server"
        assert result.metadata["tool_name"] == "test_tool"
        assert result.metadata["source"] == "mcp"

    @pytest.mark.anyio
    async def test_execute_unknown_error(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test unexpected exception returns ToolResult(error_code="unknown_error")."""
        mock_client.call_tool.side_effect = ValueError("Unexpected validation error")

        result = await adapter.execute(
            args={"bad_input": "invalid"},
            context=execution_context,
        )

        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.data is None
        assert result.error_code == "unknown_error"
        assert result.error is not None
        assert "Unexpected error" in result.error
        assert result.metadata["server_name"] == "test-server"
        assert result.metadata["tool_name"] == "test_tool"
        assert result.metadata["source"] == "mcp"

    @pytest.mark.anyio
    async def test_execute_logs_success(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test successful execution logs server_name, tool_name, latency_ms, success."""
        import logging

        caplog.set_level(logging.INFO, logger="app.ai.mcp.executor")
        mock_client.call_tool.return_value = {"result": "ok"}

        await adapter.execute(args={}, context=execution_context)

        assert any(
            "MCP tool execution succeeded" in record.message
            for record in caplog.records
        ), (
            f"Expected log not found. Captured logs: {[r.message for r in caplog.records]}"
        )

        success_record = next(
            r for r in caplog.records if "succeeded" in r.message.lower()
        )
        assert success_record.server_name == "test-server"  # type: ignore[attr-defined]
        assert success_record.tool_name == "test_tool"  # type: ignore[attr-defined]
        assert success_record.tool_source == "mcp"  # type: ignore[attr-defined]
        assert success_record.success is True  # type: ignore[attr-defined]
        assert hasattr(success_record, "latency_ms")
        assert success_record.latency_ms >= 0  # type: ignore[attr-defined]
        assert success_record.request_id == "test-request-456"  # type: ignore[attr-defined]

    @pytest.mark.anyio
    async def test_execute_logs_timeout(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test timeout execution logs server_name, tool_name, error_code, no raw data."""
        mock_client.call_tool.side_effect = asyncio.TimeoutError()

        await adapter.execute(args={"test": "value"}, context=execution_context)

        assert any(
            "MCP tool execution timeout" in record.message for record in caplog.records
        )

        timeout_record = next(
            r for r in caplog.records if "timeout" in r.message.lower()
        )
        assert timeout_record.server_name == "test-server"  # type: ignore[attr-defined]
        assert timeout_record.tool_name == "test_tool"  # type: ignore[attr-defined]
        assert timeout_record.tool_source == "mcp"  # type: ignore[attr-defined]
        assert timeout_record.success is False  # type: ignore[attr-defined]
        assert timeout_record.error_code == "timeout"  # type: ignore[attr-defined]
        assert hasattr(timeout_record, "latency_ms")
        assert timeout_record.request_id == "test-request-456"  # type: ignore[attr-defined]

        assert "test" not in str(caplog.records)
        assert "value" not in str(caplog.records)

    @pytest.mark.anyio
    async def test_execute_logs_connection_error(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test connection error logs server_name, tool_name, error_code."""
        mock_client.call_tool.side_effect = McpConnectionError("Connection lost")

        await adapter.execute(args={}, context=execution_context)

        assert any(
            "connection error" in record.message.lower() for record in caplog.records
        )

        error_record = next(
            r for r in caplog.records if "connection error" in r.message.lower()
        )
        assert error_record.server_name == "test-server"  # type: ignore[attr-defined]
        assert error_record.tool_name == "test_tool"  # type: ignore[attr-defined]
        assert error_record.tool_source == "mcp"  # type: ignore[attr-defined]
        assert error_record.success is False  # type: ignore[attr-defined]
        assert error_record.error_code == "mcp_connection_error"  # type: ignore[attr-defined]
        assert hasattr(error_record, "latency_ms")

    @pytest.mark.anyio
    async def test_execute_logs_no_raw_arguments(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test execute does not log raw tool arguments (security/privacy)."""
        sensitive_args = {
            "api_key": "secret-123",
            "password": "hunter2",
            "user_data": {"email": "user@example.com"},
        }
        mock_client.call_tool.return_value = {"status": "ok"}

        await adapter.execute(args=sensitive_args, context=execution_context)

        log_text = " ".join([record.message for record in caplog.records])
        assert "secret-123" not in log_text
        assert "hunter2" not in log_text
        assert "user@example.com" not in log_text

    @pytest.mark.anyio
    async def test_execute_logs_no_raw_response(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test execute does not log raw MCP response data (security/privacy)."""
        sensitive_response = {
            "user_data": {"ssn": "123-45-6789", "credit_card": "1234-5678-9012-3456"}
        }
        mock_client.call_tool.return_value = sensitive_response

        await adapter.execute(args={}, context=execution_context)

        log_text = " ".join([record.message for record in caplog.records])
        assert "123-45-6789" not in log_text
        assert "1234-5678-9012-3456" not in log_text

    @pytest.mark.anyio
    async def test_execute_preserves_metadata(
        self,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test adapter preserves tool origin metadata in ToolResult."""
        metadata = {
            "source": "mcp",
            "server_name": "github",
            "transport": "stdio",
            "original_name": "create_issue",
        }
        adapter = McpToolExecutionAdapter(
            server_name="github",
            tool_name="create_issue",
            client=mock_client,
            metadata=metadata,
        )
        mock_client.call_tool.return_value = {"issue_id": 42}

        result = await adapter.execute(args={}, context=execution_context)

        assert result.metadata["server_name"] == "github"
        assert result.metadata["tool_name"] == "create_issue"
        assert result.metadata["source"] == "mcp"

    @pytest.mark.anyio
    async def test_execute_latency_tracking(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test execute tracks latency_ms for all execution paths."""
        import logging

        caplog.set_level(logging.INFO, logger="app.ai.mcp.executor")

        async def slow_call_tool(
            name: str, arguments: dict[str, Any]
        ) -> dict[str, Any]:
            await asyncio.sleep(0.01)
            return {"result": "ok"}

        mock_client.call_tool.side_effect = slow_call_tool

        await adapter.execute(args={}, context=execution_context)

        success_records = [
            r for r in caplog.records if "succeeded" in r.message.lower()
        ]
        assert len(success_records) > 0, (
            f"Expected success log. Captured logs: {[r.message for r in caplog.records]}"
        )

        success_record = success_records[0]
        assert hasattr(success_record, "latency_ms")
        assert success_record.latency_ms >= 10  # type: ignore[attr-defined]

    def test_adapter_implements_tool_handler_protocol(
        self, adapter: McpToolExecutionAdapter
    ) -> None:
        """Test adapter implements ToolHandler Protocol (type-check)."""
        assert hasattr(adapter, "execute")
        assert callable(adapter.execute)

        import inspect

        sig = inspect.signature(adapter.execute)
        assert "args" in sig.parameters
        assert "context" in sig.parameters

    @pytest.mark.anyio
    async def test_execute_empty_arguments(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test execute handles empty arguments dict."""
        mock_client.call_tool.return_value = {"result": "ok"}

        result = await adapter.execute(args={}, context=execution_context)

        assert result.success is True
        mock_client.call_tool.assert_awaited_once_with("test_tool", {})

    @pytest.mark.anyio
    async def test_execute_complex_arguments(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test execute forwards complex nested arguments correctly."""
        complex_args = {
            "nested": {"key": "value", "list": [1, 2, 3]},
            "bool_flag": True,
            "number": 42,
        }
        mock_client.call_tool.return_value = {"status": "processed"}

        result = await adapter.execute(args=complex_args, context=execution_context)

        assert result.success is True
        mock_client.call_tool.assert_awaited_once_with("test_tool", complex_args)

    @pytest.mark.anyio
    async def test_execute_client_raises_exception_during_call(
        self,
        adapter: McpToolExecutionAdapter,
        mock_client: AsyncMock,
        execution_context: ToolExecutionContext,
    ) -> None:
        """Test execute handles client exceptions gracefully."""
        mock_client.call_tool.side_effect = RuntimeError("Client internal error")

        result = await adapter.execute(args={}, context=execution_context)

        assert result.success is False
        assert result.error_code == "unknown_error"
        assert result.error is not None
        assert "Unexpected error" in result.error
