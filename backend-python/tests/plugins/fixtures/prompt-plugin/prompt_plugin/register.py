from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar


def register(registrar: PluginRegistrar) -> None:
    registrar.register_prompt_template(
        name="greeting",
        version="1",
        source="Hello {{ user_name }}!",
    )
    registrar.register_prompt_template(
        name="farewell",
        version="1",
        path="templates/farewell.v1.j2",
    )
