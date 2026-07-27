"""MCP integration package — client, registry, discovery, execution.

Public API (stable after Phase 1):
    - McpClient Protocol
    - McpConnectionConfig, McpServerCredentials models
    - resolve_credential_env_vars function
    - McpConnectionError, McpToolExecutionError, McpDiscoveryError,
      McpAuthenticationError, McpPermissionDeniedError exceptions
    - McpPermissionPolicy (Phase 7)

Internal (may evolve in later phases):
    - McpServerRegistry, McpToolDiscovery, McpToolExecutionAdapter implementations
    - StdioMcpClient, StdioTransport
"""

from __future__ import annotations

from app.ai.mcp.auth import McpServerCredentials, resolve_credential_env_vars
from app.ai.mcp.client import McpClient
from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.exceptions import (
    McpAuthenticationError,
    McpConnectionError,
    McpDiscoveryError,
    McpPermissionDeniedError,
    McpToolExecutionError,
)
from app.ai.mcp.permissions import McpPermissionPolicy

__all__ = [
    "McpClient",
    "McpConnectionConfig",
    "McpServerCredentials",
    "resolve_credential_env_vars",
    "McpConnectionError",
    "McpToolExecutionError",
    "McpDiscoveryError",
    "McpAuthenticationError",
    "McpPermissionDeniedError",
    "McpPermissionPolicy",
]
