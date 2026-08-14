from __future__ import annotations

from app.ai.security.redaction import (
    build_sensitive_key_pattern,
    is_sensitive_key,
    looks_like_secret_value,
    redact_secret_patterns,
)


def test_is_sensitive_key_matches_core_words() -> None:
    for key in (
        "api_key",
        "secret",
        "token",
        "password",
        "authorization",
        "jwt",
        "credential",
    ):
        assert is_sensitive_key(key)


def test_is_sensitive_key_false_for_benign_key() -> None:
    assert not is_sensitive_key("document_id")


def test_redact_secret_patterns_masks_bearer_sk_and_jwt() -> None:
    text = (
        "Authorization: Bearer abc.def-123 key=sk-ABC123xyz "
        "jwt=eyJhbGciOi.eyJzdWIiOi.signature"
    )
    redacted = redact_secret_patterns(text)

    assert "abc.def-123" not in redacted
    assert "sk-ABC123xyz" not in redacted
    assert "Bearer [REDACTED]" in redacted
    assert "sk-[REDACTED]" in redacted
    assert "[REDACTED-JWT]" in redacted


def test_looks_like_secret_value_flags_oversized_and_prefixed_strings() -> None:
    assert looks_like_secret_value("x" * 300)
    assert looks_like_secret_value("sk-abc123")
    assert looks_like_secret_value("eyJhbGciOi.eyJzdWIiOi.sig")
    assert looks_like_secret_value("/etc/passwd")
    assert not looks_like_secret_value("hello world")


def test_build_sensitive_key_pattern_merges_core_and_extra_words() -> None:
    pattern = build_sensitive_key_pattern("metadata", "file")

    assert pattern.search("secret")
    assert pattern.search("metadata")
    assert pattern.search("file")
    assert not pattern.search("document_id")
