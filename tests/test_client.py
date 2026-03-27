"""Tests for Gmail API client wrapper."""

from __future__ import annotations

import pytest

from gmail_blade_mcp.client import (
    AuthError,
    ConnectionError,
    GmailError,
    InvalidRequestError,
    NotFoundError,
    QuotaError,
    RateLimitError,
    _classify_error,
    _scrub_credentials,
    strip_html,
    strip_quoted_reply,
)


class TestErrorClassification:
    def test_auth_errors(self) -> None:
        assert isinstance(_classify_error("Unauthorized access"), AuthError)
        assert isinstance(_classify_error("Invalid credentials provided"), AuthError)
        assert isinstance(_classify_error("Token has been expired or revoked"), AuthError)
        assert isinstance(_classify_error("Login required"), AuthError)

    def test_not_found_errors(self) -> None:
        assert isinstance(_classify_error("Message not found"), NotFoundError)
        assert isinstance(_classify_error("Label does not exist"), NotFoundError)

    def test_rate_limit_errors(self) -> None:
        assert isinstance(_classify_error("Rate limit exceeded"), RateLimitError)
        assert isinstance(_classify_error("Too many requests"), RateLimitError)
        assert isinstance(_classify_error("User rate limit exceeded"), RateLimitError)

    def test_quota_errors(self) -> None:
        assert isinstance(_classify_error("Quota exceeded"), QuotaError)
        assert isinstance(_classify_error("Daily limit reached"), QuotaError)

    def test_connection_errors(self) -> None:
        assert isinstance(_classify_error("Connection refused"), ConnectionError)
        assert isinstance(_classify_error("Request timeout"), ConnectionError)

    def test_invalid_request_errors(self) -> None:
        assert isinstance(_classify_error("Bad request: invalid query"), InvalidRequestError)

    def test_unknown_error(self) -> None:
        err = _classify_error("Something completely unknown happened")
        assert isinstance(err, GmailError)
        assert not isinstance(err, AuthError)


class TestCredentialScrubbing:
    def test_scrubs_oauth_token(self) -> None:
        msg = "Error with token ya29.a0ABCDEF_ghijklmnop"
        result = _scrub_credentials(msg)
        assert "ya29" not in result
        assert "[REDACTED]" in result

    def test_scrubs_bearer_token(self) -> None:
        msg = "Header: Bearer eyJhbGciOiJSUzI1NiIsInR5.payload.signature"
        result = _scrub_credentials(msg)
        assert "eyJ" not in result
        assert "[REDACTED]" in result

    def test_preserves_normal_text(self) -> None:
        msg = "Normal error message without tokens"
        assert _scrub_credentials(msg) == msg
