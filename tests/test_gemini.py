"""Tests for Gemini intelligence layer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------


class TestGeminiAvailability:
    """Test Gemini availability gating."""

    def test_available_when_key_set(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key-123"}):
            from gmail_blade_mcp.gemini import is_gemini_available

            assert is_gemini_available() is True

    def test_unavailable_when_key_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            from gmail_blade_mcp.gemini import is_gemini_available

            assert is_gemini_available() is False

    def test_unavailable_when_key_empty(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "  "}):
            from gmail_blade_mcp.gemini import is_gemini_available

            assert is_gemini_available() is False

    def test_require_gemini_returns_none_when_available(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            from gmail_blade_mcp.gemini import require_gemini

            assert require_gemini() is None

    def test_require_gemini_returns_error_when_unavailable(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            from gmail_blade_mcp.gemini import require_gemini

            result = require_gemini()
            assert result is not None
            assert "GOOGLE_API_KEY" in result


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassify:
    """Test Gemini classification."""

    def _make_client(self, mock_sdk_client: MagicMock) -> Any:
        """Create a GeminiClient with a mocked SDK client."""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            with patch("google.genai.Client", return_value=mock_sdk_client):
                from gmail_blade_mcp.gemini import GeminiClient

                return GeminiClient()

    def test_classify_parses_json_response(self) -> None:
        mock_response = MagicMock()
        mock_response.text = (
            '{"category": "work", "priority": "high",'
            ' "action": "reply_needed", "summary": "Meeting rescheduled to Friday"}'
        )

        mock_sdk = MagicMock()
        mock_sdk.models.generate_content.return_value = mock_response
        client = self._make_client(mock_sdk)

        result = client.classify("From: alice@example.com\nSubject: Meeting\n\nLet's reschedule to Friday.")

        assert result["category"] == "work"
        assert result["priority"] == "high"
        assert result["action"] == "reply_needed"
        assert "Friday" in result["summary"]

    def test_classify_strips_markdown_fences(self) -> None:
        mock_response = MagicMock()
        mock_response.text = (
            '```json\n{"category": "personal", "priority": "normal",'
            ' "action": "fyi", "summary": "Birthday party invite"}\n```'
        )

        mock_sdk = MagicMock()
        mock_sdk.models.generate_content.return_value = mock_response
        client = self._make_client(mock_sdk)

        result = client.classify("Birthday party this weekend!")
        assert result["category"] == "personal"

    def test_classify_handles_malformed_response(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "This is not JSON at all"

        mock_sdk = MagicMock()
        mock_sdk.models.generate_content.return_value = mock_response
        client = self._make_client(mock_sdk)

        result = client.classify("Some email")
        assert "error" in result
        assert "raw" in result


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------


class TestSummarise:
    """Test Gemini summarisation."""

    def _make_client(self, mock_sdk_client: MagicMock) -> Any:
        """Create a GeminiClient with a mocked SDK client."""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            with patch("google.genai.Client", return_value=mock_sdk_client):
                from gmail_blade_mcp.gemini import GeminiClient

                return GeminiClient()

    def test_summarise_returns_text(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "Q3 budget approved at $2.1M. ACTION: Submit updated forecasts by Friday."

        mock_sdk = MagicMock()
        mock_sdk.models.generate_content.return_value = mock_response
        client = self._make_client(mock_sdk)

        result = client.summarise("Long email about Q3 budget...")
        assert "Q3 budget" in result
        assert "ACTION:" in result

    def test_summarise_strips_whitespace(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "  Summary with extra whitespace.  \n\n"

        mock_sdk = MagicMock()
        mock_sdk.models.generate_content.return_value = mock_response
        client = self._make_client(mock_sdk)

        result = client.summarise("Some email")
        assert not result.startswith(" ")
        assert not result.endswith("\n")


# ---------------------------------------------------------------------------
# Server tool integration
# ---------------------------------------------------------------------------


class TestGeminiTools:
    """Test gmail_classify and gmail_summarise server tools."""

    @pytest.fixture()
    def mock_gmail_client(self) -> MagicMock:
        client = MagicMock()
        client.get_message.return_value = {
            "id": "msg123",
            "threadId": "thread456",
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "Subject", "value": "Q3 Report"},
                    {"name": "Date", "value": "Thu, 27 Mar 2026 10:00:00 +1100"},
                ],
                "mimeType": "text/plain",
                "body": {"data": ""},
            },
            "snippet": "Please review the Q3 report",
        }
        client.get_thread.return_value = {
            "id": "thread456",
            "messages": [client.get_message.return_value],
        }
        return client

    async def test_classify_returns_formatted_result(self, mock_gmail_client: MagicMock) -> None:
        mock_gemini = MagicMock()
        mock_gemini.classify.return_value = {
            "category": "work",
            "priority": "high",
            "action": "review",
            "summary": "Q3 report ready for review",
        }

        with (
            patch("gmail_blade_mcp.server._get_client", return_value=mock_gmail_client),
            patch("gmail_blade_mcp.server.get_gemini_client", return_value=mock_gemini),
            patch("gmail_blade_mcp.server.require_gemini", return_value=None),
        ):
            from gmail_blade_mcp.server import gmail_classify

            result = await gmail_classify(message_id="msg123")

        assert "Category: work" in result
        assert "Priority: high" in result
        assert "Action: review" in result

    async def test_classify_returns_error_when_no_api_key(self) -> None:
        with patch("gmail_blade_mcp.server.require_gemini", return_value="Error: Gemini not configured."):
            from gmail_blade_mcp.server import gmail_classify

            result = await gmail_classify(message_id="msg123")

        assert "Error" in result
        assert "Gemini" in result

    async def test_summarise_message(self, mock_gmail_client: MagicMock) -> None:
        mock_gemini = MagicMock()
        mock_gemini.summarise.return_value = "Q3 report is ready. ACTION: Review by EOD Friday."

        with (
            patch("gmail_blade_mcp.server._get_client", return_value=mock_gmail_client),
            patch("gmail_blade_mcp.server.get_gemini_client", return_value=mock_gemini),
            patch("gmail_blade_mcp.server.require_gemini", return_value=None),
        ):
            from gmail_blade_mcp.server import gmail_summarise

            result = await gmail_summarise(message_id="msg123")

        assert "Q3 report" in result

    async def test_summarise_thread(self, mock_gmail_client: MagicMock) -> None:
        mock_gemini = MagicMock()
        mock_gemini.summarise.return_value = "Thread about Q3 report with 3 participants."

        with (
            patch("gmail_blade_mcp.server._get_client", return_value=mock_gmail_client),
            patch("gmail_blade_mcp.server.get_gemini_client", return_value=mock_gemini),
            patch("gmail_blade_mcp.server.require_gemini", return_value=None),
        ):
            from gmail_blade_mcp.server import gmail_summarise

            result = await gmail_summarise(thread_id="thread456")

        assert "Q3 report" in result

    async def test_summarise_requires_one_id(self) -> None:
        with patch("gmail_blade_mcp.server.require_gemini", return_value=None):
            from gmail_blade_mcp.server import gmail_summarise

            result = await gmail_summarise()
        assert "Error" in result

    async def test_summarise_rejects_both_ids(self) -> None:
        with patch("gmail_blade_mcp.server.require_gemini", return_value=None):
            from gmail_blade_mcp.server import gmail_summarise

            result = await gmail_summarise(message_id="msg1", thread_id="thread1")
        assert "Error" in result
