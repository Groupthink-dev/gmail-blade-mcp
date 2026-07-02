"""Typed exception hierarchy for Gmail Blade MCP.

Lives in its own module so both ``auth`` (credential resolution) and
``client`` (API error classification) can raise the same types without a
circular import. Tools import these via ``gmail_blade_mcp.client`` re-exports.
"""

from __future__ import annotations


class GmailError(Exception):
    """Base exception for Gmail client errors."""

    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(message)
        self.details = details


class AuthError(GmailError):
    """Authentication failed — invalid or expired OAuth token."""


class ScopeError(AuthError):
    """Credentials lack a required Gmail scope.

    The refresh token's grant is fixed at mint time — re-mint with
    ``gmail-blade-mcp auth --scopes write`` to add write scopes.
    """


class NotFoundError(GmailError):
    """Requested resource (message, thread, label) not found."""


class RateLimitError(GmailError):
    """Rate limit exceeded — back off and retry."""


class QuotaError(GmailError):
    """Daily quota exceeded."""


class ConnectionError(GmailError):  # noqa: A001
    """Cannot connect to Gmail API."""


class WriteDisabledError(GmailError):
    """Write operation attempted but GMAIL_WRITE_ENABLED is not true."""


class InvalidRequestError(GmailError):
    """Bad request — invalid query, missing fields, etc."""
