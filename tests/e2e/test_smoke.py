"""Live E2E smoke — requires GMAIL_E2E=1 and working credentials.

Uses the same credential resolution chain as the server (env triple or
~/.gmail-blade/token.json); never opens a browser. Run via `make test-e2e`.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("GMAIL_E2E") != "1", reason="GMAIL_E2E=1 + live Gmail credentials required"),
]


@pytest.fixture(scope="module")
def client():
    from gmail_blade_mcp.client import GmailClient

    return GmailClient()


def test_profile_resolves(client) -> None:
    assert "@" in client.email_address
    assert client.messages_total >= 0


def test_search_returns_messages(client) -> None:
    messages, total = client.search_messages(query="", limit=3)
    assert isinstance(messages, list)
    assert total >= len(messages)
    for message in messages:
        assert message.get("id")


def test_labels_include_inbox(client) -> None:
    labels = client.list_labels()
    assert any(label.get("id") == "INBOX" for label in labels)
