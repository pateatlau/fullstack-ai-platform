"""Secret resolution abstraction (Epic 11 Phase 4).

``SecretResolver`` is the indirection point between code that needs a named
secret and wherever that secret is actually stored. ``EnvSecretResolver`` is
V2's only implementation (byte-for-byte today's direct ``os.environ`` reads);
a future vault-backed resolver is a pure swap-in behind the same protocol.
"""

from __future__ import annotations

from app.ai.security.secrets.resolver import EnvSecretResolver, SecretResolver

__all__ = ["EnvSecretResolver", "SecretResolver"]
