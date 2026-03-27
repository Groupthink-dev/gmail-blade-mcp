"""Shared constants, types, and write-gate for Gmail Blade MCP server."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Default limit for list operations (token efficiency)
DEFAULT_LIMIT = 20

# Maximum batch size for bulk operations
MAX_BATCH_SIZE = 50

# Email body truncation limit (characters)
MAX_BODY_CHARS = 4000

# Default body mode for read operations
DEFAULT_BODY_MODE = "stripped"

# Gmail API scopes
SCOPES_READONLY = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_MODIFY = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Credential storage directory
CREDENTIALS_DIR = os.path.expanduser("~/.gmail-blade")

# Gmail message format options
FORMAT_FULL = "full"
FORMAT_METADATA = "metadata"
FORMAT_MINIMAL = "minimal"

# Metadata headers to fetch for list views
METADATA_HEADERS = ["From", "To", "Subject", "Date"]


def is_write_enabled() -> bool:
    """Check if write operations are enabled via env var."""
    return os.environ.get("GMAIL_WRITE_ENABLED", "").lower() == "true"


def require_write() -> str | None:
    """Return an error message if writes are disabled, else None."""
    if not is_write_enabled():
        return "Error: Write operations are disabled. Set GMAIL_WRITE_ENABLED=true to enable."
    return None
