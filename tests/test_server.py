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


# ===========================================================================
# DD-338 Phase C Wave 3 — gmail_changes _meta envelope
# ===========================================================================


class TestGmailChangesMetaEnvelope:
    """`gmail_changes` audit_surface promotion from minimal -> structured."""

    async def test_emits_canonical_envelope_clean(self, mock_client: MagicMock) -> None:
        import json

        from gmail_blade_mcp.server import gmail_changes

        mock_client.get_history.return_value = {
            "historyId": "9876543210",
            "history": [
                {"messagesAdded": [{"message": {"id": "m1"}}]},
                {"messagesDeleted": [{"message": {"id": "m2"}}]},
                {"labelsAdded": [{"message": {"id": "m3"}}]},
            ],
        }
        result = await gmail_changes(history_id="1234567890abcdef")
        assert "\n\n_meta: " in result
        _, _, tail = result.rpartition("\n\n")
        meta = json.loads(tail[len("_meta: ") :])

        # Option C: matched_total = returned = aggregate delta count
        assert meta["matched_total"] == 3
        assert meta["returned"] == 3
        # history_id truncated to 12 chars
        assert "history_id=1234567890ab" in meta["filtered_by"]
        # No label arg -> key absent
        keys = {f.split("=")[0] for f in meta["filtered_by"]}
        assert "label" not in keys
        # nextPageToken absent -> no redactions key
        assert "redactions" not in meta
        # next_cursor surfaces the new watermark
        assert meta["next_cursor"] == "9876543210"
        assert isinstance(meta["latency_ms"], int)
        assert meta["latency_ms"] >= 0

    async def test_more_changes_available_redaction(self, mock_client: MagicMock) -> None:
        import json

        from gmail_blade_mcp.server import gmail_changes

        mock_client.get_history.return_value = {
            "historyId": "1000",
            "nextPageToken": "page-2-token",
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
        }
        result = await gmail_changes(history_id="abc", label="Label_5")
        _, _, tail = result.rpartition("\n\n")
        meta = json.loads(tail[len("_meta: ") :])
        assert meta["redactions"] == ["more_changes_available"]
        # label key present and value matches resolved label_id
        assert "label=Label_5" in meta["filtered_by"]
        # Sorted alphabetically
        assert meta["filtered_by"] == sorted(meta["filtered_by"])
        assert meta["matched_total"] == 1

    async def test_error_path_no_envelope(self, mock_client: MagicMock) -> None:
        from gmail_blade_mcp.client import GmailError
        from gmail_blade_mcp.server import gmail_changes

        mock_client.get_history.side_effect = GmailError("Invalid history ID")
        result = await gmail_changes(history_id="bad-id")
        assert "Error" in result
        assert "\n\n_meta: " not in result

    async def test_n3_deterministic_after_latency_strip(self, mock_client: MagicMock) -> None:
        import json

        from gmail_blade_mcp.server import gmail_changes

        history_fixture = {
            "historyId": "9876543210",
            "nextPageToken": "page-2",
            "history": [
                {"messagesAdded": [{"message": {"id": "m1"}}, {"message": {"id": "m2"}}]},
                {"labelsRemoved": [{"message": {"id": "m3"}}]},
            ],
        }
        mock_client.get_history.return_value = history_fixture

        stripped: list[tuple[str, dict]] = []
        for _ in range(3):
            result = await gmail_changes(history_id="hid-abc-def-ghi", label="L1")
            payload, _, tail = result.rpartition("\n\n")
            meta = json.loads(tail[len("_meta: ") :])
            meta.pop("latency_ms", None)
            stripped.append((payload, meta))

        assert all(s == stripped[0] for s in stripped[1:])
