from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar


def register(registrar: PluginRegistrar) -> None:
    registrar.register_mcp_server(
        {
            "name": "programmatic-mcp-server",
            "command": "uvx",
            "args": ["mcp-server-echo"],
            "transport": "stdio",
        }
    )
