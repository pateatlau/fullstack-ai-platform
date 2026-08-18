"""Non-throwing text serialization for guardrail evaluation surfaces."""

from __future__ import annotations

import json


def serialize_guardrail_content(value: object) -> str:
    """Return a scannable representation without propagating serialization errors."""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        try:
            return repr(value)
        except Exception:
            return "<unserializable guardrail content>"
