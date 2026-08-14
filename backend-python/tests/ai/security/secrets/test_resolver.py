from __future__ import annotations

from app.ai.security.secrets.resolver import EnvSecretResolver, SecretResolver


def test_env_secret_resolver_resolves_present_key(monkeypatch) -> None:
    monkeypatch.setenv("MY_SECRET_KEY", "value123")
    resolver = EnvSecretResolver()

    assert resolver.resolve("MY_SECRET_KEY") == "value123"


def test_env_secret_resolver_missing_key_returns_none(monkeypatch) -> None:
    monkeypatch.delenv("DOES_NOT_EXIST_KEY", raising=False)
    resolver = EnvSecretResolver()

    assert resolver.resolve("DOES_NOT_EXIST_KEY") is None


def test_env_secret_resolver_satisfies_protocol() -> None:
    assert isinstance(EnvSecretResolver(), SecretResolver)
