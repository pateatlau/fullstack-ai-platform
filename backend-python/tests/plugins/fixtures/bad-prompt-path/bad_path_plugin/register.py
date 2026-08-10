from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar


def register(registrar: PluginRegistrar) -> None:
    registrar.register_prompt_template(
        name="escape",
        version="1",
        path="../outside.v1.j2",
    )
