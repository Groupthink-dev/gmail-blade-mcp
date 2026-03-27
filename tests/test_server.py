"""Tests for MCP server tools."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from gmail_blade_mcp.client import AuthError, NotFoundError
from gmail_blade_mcp.server import (
    gmail_bulk,
    gmail_delete,
    gmail_draft,
    gmail_read,
    gmail_reply,
    gmail_search,
    gmail_send,
    gmail_thread,
)


@pytest.fixture
def mock_client() -> MagicMock:
    with patch("gmail_blade_mcp.server._get_client") as mock_get:
        client = MagicMock()
        client.email_address = "test@gmail.com"
        mock_get.return_value = client
        yield client


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


class TestGmailSearch:
    async def test_search_success(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = (
            [{"id": "msg1", "payload": {"headers": []}, "labelIds": []}],
            1,
        )
        result = await gmail_search(query="test")
        assert "msg1" in result

    async def test_search_empty(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = ([], 0)
        result = await gmail_search(query="nonexistent")
        assert "No messages" in result

    async def test_search_error(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.side_effect = AuthError("Invalid token")
        result = await gmail_search(query="test")
        assert "Error" in result


class TestGmailRead:
    async def test_read_success(self, mock_client: MagicMock) -> None:
        mock_client.get_message.return_value = {
            "id": "msg1",
            "threadId": "thread1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "Subject", "value": "Test"},
                    {"name": "Date", "value": "Thu, 27 Mar 2026 10:00:00 +1100"},
                ]
            },
            "labelIds": ["INBOX"],
        }
        result = await gmail_read(message_id="msg1")
        assert "alice@example.com" in result
        assert "Test" in result

    async def test_read_not_found(self, mock_client: MagicMock) -> None:
        mock_client.get_message.side_effect = NotFoundError("Message not found")
        result = await gmail_read(message_id="nonexistent")
        assert "Error" in result


class TestGmailThread:
    async def test_thread_success(self, mock_client: MagicMock) -> None:
        mock_client.get_thread.return_value = {
            "id": "thread1",
            "messages": [
                {
                    "id": "msg1",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "alice@example.com"},
                            {"name": "Subject", "value": "Thread test"},
                            {"name": "Date", "value": "Thu, 27 Mar 2026 10:00:00 +1100"},
                        ]
                    },
                }
            ],
        }
        result = await gmail_thread(thread_id="thread1")
        assert "thread1" in result
        assert "Thread test" in result


# ---------------------------------------------------------------------------
# Write tools (gate checks)
# ---------------------------------------------------------------------------


class TestWriteGate:
    async def test_send_blocked_without_env(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_send(to="a@b.com", subject="test", body="test")
            assert "Error" in result
            assert "GMAIL_WRITE_ENABLED" in result

    async def test_delete_requires_confirm(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {"GMAIL_WRITE_ENABLED": "true"}):
            result = await gmail_delete(message_id="msg1", confirm=False)
            assert "confirm=true" in result

    async def test_send_allowed_with_env(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {"GMAIL_WRITE_ENABLED": "true"}):
            mock_client.send_message.return_value = {"id": "sent1", "threadId": "t1"}
            result = await gmail_send(to="a@b.com", subject="test", body="test")
            assert "Sent" in result

    async def test_reply_blocked_without_env(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_reply(message_id="msg1", body="reply")
            assert "GMAIL_WRITE_ENABLED" in result

    async def test_draft_blocked_without_env(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_draft(to="a@b.com", subject="test", body="test")
            assert "GMAIL_WRITE_ENABLED" in result

    async def test_bulk_blocked_without_env(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_bulk(message_ids="msg1,msg2", action="archive")
            assert "GMAIL_WRITE_ENABLED" in result


class TestBulk:
    async def test_bulk_archive(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {"GMAIL_WRITE_ENABLED": "true"}):
            mock_client.batch_modify.return_value = "Modified 2 messages"
            result = await gmail_bulk(message_ids="msg1,msg2", action="archive")
            assert "Modified" in result

    async def test_bulk_unknown_action(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {"GMAIL_WRITE_ENABLED": "true"}):
            result = await gmail_bulk(message_ids="msg1", action="explode")
            assert "Error" in result
            assert "Unknown action" in result

    async def test_bulk_empty_ids(self, mock_client: MagicMock) -> None:
        with patch.dict(os.environ, {"GMAIL_WRITE_ENABLED": "true"}):
            result = await gmail_bulk(message_ids="", action="archive")
            assert "Error" in result
