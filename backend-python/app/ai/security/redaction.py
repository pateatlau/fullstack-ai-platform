"""Consolidated redaction primitives (Epic 11 Phase 4).

Single source of truth for the sensitive-key indicator pattern, secret-value
patterns (Bearer tokens, ``sk-`` API keys, JWTs), and the safe-scalar
heuristics reused by ``app/core/logging.py``, ``app/ai/hitl/models.py``, and
``app/schemas/jobs.py``. Consolidates four independently-maintained redaction
implementations into one shared allowlist/pattern source.
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

from pydantic import BaseModel

REDACTED_PLACEHOLDER = "[REDACTED]"

# Words that, when found in a field/key name, mark its value as sensitive.
_CORE_SENSITIVE_KEY_WORDS = (
    "api[_-]?key",
    "secret",
    "token",
    "password",
    "authorization",
    "id_token",
    "jwt",
    "credential",
)


def build_sensitive_key_pattern(*extra_words: str) -> re.Pattern[str]:
    """Compile a case-insensitive sensitive-key pattern from the core word
    list plus any caller-supplied extra words (e.g. jobs' own structural
    keys such as ``payload``/``file``/``path``)."""
    words = _CORE_SENSITIVE_KEY_WORDS + extra_words
    return re.compile(rf"({'|'.join(words)})", re.IGNORECASE)


# Core sensitive-key pattern (used verbatim by app/core/logging.py).
SENSITIVE_KEY_PATTERN = build_sensitive_key_pattern()

BEARER_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]+\b")
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")

API_KEY_PREFIX = "sk-"
JWT_PREFIX = "eyJ"


def is_sensitive_key(key: str) -> bool:
    """True when ``key`` matches the core sensitive-key indicator pattern."""
    return bool(SENSITIVE_KEY_PATTERN.search(key))


def redact_secret_patterns(text: str) -> str:
    """Replace Bearer tokens, ``sk-`` API keys, and JWTs in free text."""
    redacted = BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    redacted = API_KEY_PATTERN.sub("sk-[REDACTED]", redacted)
    redacted = JWT_PATTERN.sub("[REDACTED-JWT]", redacted)
    return redacted


def looks_like_secret_value(value: str, *, max_len: int = 256) -> bool:
    """Heuristic for oversized/path-like/token-like scalar strings."""
    if len(value) > max_len:
        return True
    if "/" in value or "\\" in value:
        return True
    if value.startswith(JWT_PREFIX) or value.startswith(API_KEY_PREFIX):
        return True
    return False


ModelT = TypeVar("ModelT", bound=BaseModel)


def clear_pii_fields(model: ModelT, **fields: Any) -> ModelT:
    """Return ``model`` with ``fields`` cleared, or unchanged if already clear.

    Shared by HITL's client-audit-field redaction so terminal/retention
    transitions only allocate a new model when a field actually changes.
    """
    if all(getattr(model, name) == value for name, value in fields.items()):
        return model
    return model.model_copy(update=fields)
