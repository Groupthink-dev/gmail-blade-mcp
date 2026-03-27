"""Tests for token-efficient formatters."""

from __future__ import annotations

import pytest

from gmail_blade_mcp.client import strip_html, strip_quoted_reply
from gmail_blade_mcp.formatters import (
    _format_size,
    _get_header,
    _truncate_body,
    format_label_list,
    format_message_list,
    format_snippets,
)


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_plain_text_passthrough(self) -> None:
        assert strip_html("Hello world") == "Hello world"

    def test_strips_tags(self) -> None:
        assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_handles_br_tags(self) -> None:
        result = strip_html("Line 1<br>Line 2")
        assert "Line 1" in result
        assert "Line 2" in result

    def test_strips_script_tags(self) -> None:
        html = "<p>Hello</p><script>alert('xss')</script><p>World</p>"
        result = strip_html(html)
        assert "alert" not in result
        assert "Hello" in result
        assert "World" in result

    def test_strips_style_tags(self) -> None:
        html = "<style>.foo { color: red; }</style><p>Content</p>"
        result = strip_html(html)
        assert "color" not in result
        assert "Content" in result

    def test_handles_empty_string(self) -> None:
        assert strip_html("") == ""

    def test_complex_newsletter_html(self) -> None:
        html = """
        <html>
        <head><style>body { font-family: sans-serif; }</style></head>
        <body>
        <div class="header"><h1>Newsletter</h1></div>
        <p>Welcome to our <strong>weekly</strong> update.</p>
        <ul><li>Item 1</li><li>Item 2</li></ul>
        <script>trackOpen()</script>
        </body></html>
        """
        result = strip_html(html)
        assert "Newsletter" in result
        assert "weekly" in result
        assert "Item 1" in result
        assert "trackOpen" not in result
        assert "font-family" not in result


# ---------------------------------------------------------------------------
# Quote stripping
# ---------------------------------------------------------------------------


class TestStripQuotedReply:
    def test_strips_on_wrote_pattern(self) -> None:
        text = "Thanks for the update.\n\nOn Mon, Mar 27, 2026 at 10:00 AM Alice <alice@ex.com> wrote:\n> Original message"
        result = strip_quoted_reply(text)
        assert "Thanks for the update" in result
        assert "Original message" not in result

    def test_strips_original_message_pattern(self) -> None:
        text = "My reply here.\n\n--- Original Message ---\nFrom: bob@example.com\nOld content"
        result = strip_quoted_reply(text)
        assert "My reply here" in result
        assert "Old content" not in result

    def test_strips_angle_bracket_quotes(self) -> None:
        text = "New content\n\n> Quoted line 1\n> Quoted line 2"
        result = strip_quoted_reply(text)
        assert "New content" in result
        assert "Quoted line 1" not in result

    def test_preserves_unquoted_text(self) -> None:
        text = "Just a normal email with no quotes."
        assert strip_quoted_reply(text) == text


# ---------------------------------------------------------------------------
# Body truncation
# ---------------------------------------------------------------------------


class TestTruncateBody:
    def test_short_text_unchanged(self) -> None:
        text = "Short text"
        assert _truncate_body(text, 100) == text

    def test_truncates_at_paragraph(self) -> None:
        text = "Paragraph one content here.\n\nParagraph two content here.\n\nParagraph three which is extra."
        result = _truncate_body(text, 60)
        assert "Paragraph one" in result
        assert "truncated" in result

    def test_truncates_at_sentence(self) -> None:
        text = "First sentence here. Second sentence here. Third sentence which pushes us over."
        result = _truncate_body(text, 50)
        assert "truncated" in result

    def test_hard_cut_fallback(self) -> None:
        text = "A" * 200
        result = _truncate_body(text, 100)
        assert len(result) > 100  # includes truncation notice
        assert "truncated" in result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_get_header(self) -> None:
        msg = {"payload": {"headers": [{"name": "Subject", "value": "Test"}]}}
        assert _get_header(msg, "Subject") == "Test"
        assert _get_header(msg, "From") == ""

    def test_format_size(self) -> None:
        assert _format_size(500) == "500 B"
        assert "KB" in _format_size(2048)
        assert "MB" in _format_size(2 * 1024 * 1024)


# ---------------------------------------------------------------------------
# List formatters
# ---------------------------------------------------------------------------


class TestFormatMessageList:
    def test_empty_list(self) -> None:
        assert format_message_list([]) == "No messages found."

    def test_single_message(self) -> None:
        msg = {
            "id": "abc123",
            "payload": {
                "headers": [
                    {"name": "Date", "value": "Thu, 27 Mar 2026 10:00:00 +1100"},
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "Subject", "value": "Test subject"},
                ]
            },
            "sizeEstimate": 4096,
            "labelIds": ["INBOX"],
        }
        result = format_message_list([msg])
        assert "alice@example.com" in result
        assert "Test subject" in result
        assert "id=abc123" in result

    def test_limit_annotation(self) -> None:
        msgs = [{"id": f"msg{i}", "payload": {"headers": []}, "labelIds": []} for i in range(5)]
        result = format_message_list(msgs, total=25, limit=5)
        assert "20 more" in result


class TestFormatLabelList:
    def test_empty_list(self) -> None:
        assert format_label_list([]) == "No labels found."

    def test_label_with_counts(self) -> None:
        labels = [
            {"name": "INBOX", "messagesTotal": 100, "messagesUnread": 5, "type": "system", "id": "INBOX"},
        ]
        result = format_label_list(labels)
        assert "INBOX" in result
        assert "(100/5)" in result
        assert "[system]" in result
