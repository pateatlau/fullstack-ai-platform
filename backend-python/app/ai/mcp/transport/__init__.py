"""MCP transport implementations (stdio primary; SSE deferred).

Phase 2 will implement stdio.py for subprocess-based JSON-RPC transport.
"""

from __future__ import annotations

# TODO(phase-2): Implement StdioTransport in transport/stdio.py
# - Subprocess spawn via command + args
# - JSON-RPC stdin/stdout protocol wrapper
# - Timeout enforcement for connect/list/call operations
# - Graceful shutdown with SIGTERM/SIGKILL fallback
