"""``SecretResolver`` protocol and its only V2 implementation."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretResolver(Protocol):
    """Indirection contract between code and wherever a secret is stored."""

    def resolve(self, key: str) -> str | None:
        """Return the secret named ``key``, or ``None`` when absent."""
        ...


class EnvSecretResolver:
    """Reads secrets from process environment variables.

    Byte-for-byte identical to the platform's pre-Epic-11 direct
    ``os.environ.get()`` reads. The vault swap point: a future secret-manager
    -backed resolver (AWS Secrets Manager, HashiCorp Vault, GCP Secret
    Manager) implements this same protocol without any call-site changes.
    """

    def resolve(self, key: str) -> str | None:
        return os.environ.get(key)
