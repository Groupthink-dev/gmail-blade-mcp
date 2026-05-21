"""DD-338 Phase A.1 — scope-tag wrapper + Track 3 _meta envelope tests.

Covers _compose_scoped_query helper plus the four read tools' (gmail_search,
gmail_snippets, gmail_thread, gmail_read) scope= argument behaviour and the
_meta envelope (canonical JSON-tail wire shape per architect amendment
2026-05-21).
"""

from __future__ import annotations

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest

from gmail_blade_mcp.client import InvalidRequestError
from gmail_blade_mcp.server import (
    _compose_scoped_query,
    _format_meta_envelope,
    gmail_read,
    gmail_search,
    gmail_snippets,
    gmail_thread,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> MagicMock:
    """Patch _get_client + clear the label-id cache between tests."""
    with patch("gmail_blade_mcp.server._get_client") as mock_get, patch("gmail_blade_mcp.server._LABEL_ID_CACHE", {}):
        client = MagicMock()
        client.email_address = "test@gmail.com"
        client.list_labels.return_value = [
            {"id": "Label_99", "name": "Family"},
            {"id": "Label_42", "name": "Work"},
            {"id": "INBOX", "name": "INBOX"},
        ]
        mock_get.return_value = client
        yield client


def _parse_meta(payload: str) -> dict:
    """Extract the JSON-tail _meta envelope from a tool payload."""
    match = re.search(r"\n\n_meta: (\{.*\})$", payload, re.DOTALL)
    assert match is not None, f"No _meta envelope found in payload:\n{payload}"
    return json.loads(match.group(1))


def _make_message(msg_id: str, label_ids: list[str], subject: str = "Test") -> dict:
    return {
        "id": msg_id,
        "threadId": f"thread-{msg_id}",
        "labelIds": label_ids,
        "payload": {
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Thu, 27 Mar 2026 10:00:00 +1100"},
            ]
        },
    }


def _make_thread(thread_id: str, msgs_label_ids: list[list[str]]) -> dict:
    return {
        "id": thread_id,
        "messages": [
            _make_message(f"{thread_id}-m{i}", lids, subject=f"Thread {thread_id}")
            for i, lids in enumerate(msgs_label_ids)
        ],
    }


# ---------------------------------------------------------------------------
# _compose_scoped_query helper
# ---------------------------------------------------------------------------


class TestComposeScopedQuery:
    def test_none_scope_passes_through(self) -> None:
        eff, applied = _compose_scoped_query("from:alice", None)
        assert eff == "from:alice"
        assert applied == []

    def test_none_scope_empty_query(self) -> None:
        eff, applied = _compose_scoped_query("", None)
        assert eff == ""
        assert applied == []

    def test_work_scope_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            eff, applied = _compose_scoped_query("from:alice", "work")
        assert eff == "(from:alice) (category:work)"
        assert applied == ["scope=work"]

    def test_work_scope_env_override(self) -> None:
        with patch.dict(os.environ, {"GMAIL_WORK_LABEL": "label:Work"}):
            eff, applied = _compose_scoped_query("from:alice", "work")
        assert eff == "(from:alice) (label:Work)"
        assert applied == ["scope=work"]

    def test_personal_scope_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            eff, applied = _compose_scoped_query("subject:hi", "personal")
        assert eff == "(subject:hi) (category:personal)"
        assert applied == ["scope=personal"]

    def test_family_scope_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            eff, applied = _compose_scoped_query("", "family")
        assert eff == "(label:Family)"
        assert applied == ["scope=family"]

    def test_family_scope_env_override(self) -> None:
        with patch.dict(os.environ, {"GMAIL_FAMILY_LABEL": "label:Household"}):
            eff, applied = _compose_scoped_query("", "family")
        assert eff == "(label:Household)"
        assert applied == ["scope=family"]

    def test_public_scope_passthrough(self) -> None:
        eff, applied = _compose_scoped_query("from:alice", "public")
        # public preserves the user query unchanged but records the scope tag
        assert eff == "from:alice"
        assert applied == ["scope=public"]

    def test_invalid_scope_raises(self) -> None:
        with pytest.raises(InvalidRequestError) as exc_info:
            _compose_scoped_query("from:alice", "bogus")
        msg = str(exc_info.value)
        assert "Unknown scope" in msg
        assert "work, personal, family, public, None" in msg

    def test_empty_query_with_work_scope(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            eff, applied = _compose_scoped_query("", "work")
        assert eff == "(category:work)"
        assert applied == ["scope=work"]


# ---------------------------------------------------------------------------
# _format_meta_envelope helper
# ---------------------------------------------------------------------------


class TestFormatMetaEnvelope:
    def test_required_fields(self) -> None:
        line = _format_meta_envelope(matched_total=42, returned=10, filtered_by=["scope=work"], latency_ms=234)
        assert line.startswith("_meta: {")
        data = json.loads(line[len("_meta: ") :])
        assert data["matched_total"] == 42
        assert data["returned"] == 10
        assert data["filtered_by"] == ["scope=work"]
        assert data["latency_ms"] == 234
        assert "redactions" not in data  # absent when empty
        assert "next_cursor" not in data
        assert "error_notes" not in data

    def test_optional_redactions(self) -> None:
        line = _format_meta_envelope(
            matched_total=0,
            returned=0,
            filtered_by=["scope=work"],
            latency_ms=12,
            redactions=["scope_mismatch"],
        )
        data = json.loads(line[len("_meta: ") :])
        assert data["redactions"] == ["scope_mismatch"]


# ---------------------------------------------------------------------------
# gmail_search
# ---------------------------------------------------------------------------


class TestGmailSearchScope:
    async def test_search_no_scope_unchanged(self, mock_client: MagicMock) -> None:
        """Baseline: gmail_search(query=...) with no scope preserves v0.3.0 behaviour."""
        mock_client.search_messages.return_value = ([_make_message("m1", ["INBOX"])], 1)
        result = await gmail_search(query="from:alice")
        # No _meta envelope when include_meta=False (default)
        assert "\n\n_meta: " not in result
        # The raw query was passed through verbatim — no parens, no scope clause
        call_kwargs = mock_client.search_messages.call_args.kwargs
        assert call_kwargs["query"] == "from:alice"

    async def test_search_scope_work_default(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = ([_make_message("m1", ["INBOX"])], 1)
        with patch.dict(os.environ, {}, clear=True):
            await gmail_search(query="from:alice", scope="work")
        call_kwargs = mock_client.search_messages.call_args.kwargs
        assert call_kwargs["query"] == "(from:alice) (category:work)"

    async def test_search_scope_work_env_override(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = ([_make_message("m1", ["INBOX"])], 1)
        with patch.dict(os.environ, {"GMAIL_WORK_LABEL": "label:Work"}):
            await gmail_search(query="", scope="work")
        call_kwargs = mock_client.search_messages.call_args.kwargs
        assert call_kwargs["query"] == "(label:Work)"

    async def test_search_scope_public_passthrough(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = ([_make_message("m1", ["INBOX"])], 1)
        await gmail_search(query="has:attachment", scope="public")
        call_kwargs = mock_client.search_messages.call_args.kwargs
        # public is a no-op on the q= clause — user query unchanged
        assert call_kwargs["query"] == "has:attachment"

    async def test_search_scope_invalid_raises_via_error(self, mock_client: MagicMock) -> None:
        # InvalidRequestError is caught and returned as Error: ... string
        result = await gmail_search(query="from:alice", scope="bogus")
        assert "Error" in result
        assert "Unknown scope" in result

    async def test_search_meta_envelope_shape(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = (
            [_make_message("m1", ["INBOX"]), _make_message("m2", ["INBOX"])],
            137,
        )
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_search(query="from:alice", scope="work", label="INBOX", include_meta=True)
        meta = _parse_meta(result)
        assert meta["matched_total"] == 137
        assert meta["returned"] == 2
        # scope first, then label
        assert meta["filtered_by"] == ["scope=work", "label=INBOX"]
        assert isinstance(meta["latency_ms"], int)
        assert meta["latency_ms"] >= 0

    async def test_search_meta_subset_relation(self, mock_client: MagicMock) -> None:
        """tool(scope=A) ⊂ tool() — DD-333 Scope-filtering invariant."""

        def search_side_effect(**kwargs: object) -> tuple[list[dict], int]:
            q = kwargs.get("query", "") or ""
            if "category:work" in q:
                return ([_make_message("m1", ["CATEGORY_PERSONAL"])], 1)
            return (
                [
                    _make_message("m1", ["CATEGORY_PERSONAL"]),
                    _make_message("m2", ["CATEGORY_PROMOTIONS"]),
                    _make_message("m3", ["CATEGORY_SOCIAL"]),
                ],
                3,
            )

        mock_client.search_messages.side_effect = search_side_effect
        with patch.dict(os.environ, {}, clear=True):
            scoped = await gmail_search(query="", scope="work", include_meta=True)
            unscoped = await gmail_search(query="", include_meta=True)
        scoped_meta = _parse_meta(scoped)
        unscoped_meta = _parse_meta(unscoped)
        assert scoped_meta["returned"] <= unscoped_meta["returned"]
        assert scoped_meta["matched_total"] <= unscoped_meta["matched_total"]


# ---------------------------------------------------------------------------
# gmail_snippets
# ---------------------------------------------------------------------------


class TestGmailSnippetsScope:
    async def test_snippets_no_scope_unchanged(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = ([_make_message("m1", ["INBOX"])], 1)
        result = await gmail_snippets(query="from:alice")
        assert "\n\n_meta: " not in result
        call_kwargs = mock_client.search_messages.call_args.kwargs
        assert call_kwargs["query"] == "from:alice"

    async def test_snippets_scope_personal_default(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = ([_make_message("m1", ["INBOX"])], 1)
        with patch.dict(os.environ, {}, clear=True):
            await gmail_snippets(query="hello", scope="personal")
        call_kwargs = mock_client.search_messages.call_args.kwargs
        assert call_kwargs["query"] == "(hello) (category:personal)"

    async def test_snippets_meta_envelope(self, mock_client: MagicMock) -> None:
        mock_client.search_messages.return_value = (
            [_make_message("m1", ["INBOX"])],
            1,
        )
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_snippets(query="", scope="family", include_meta=True)
        meta = _parse_meta(result)
        assert meta["filtered_by"] == ["scope=family"]
        assert meta["returned"] == 1


# ---------------------------------------------------------------------------
# gmail_read — post-fetch scope verify
# ---------------------------------------------------------------------------


class TestGmailReadScope:
    async def test_read_no_scope_unchanged(self, mock_client: MagicMock) -> None:
        mock_client.get_message.return_value = _make_message("msg1", ["INBOX"])
        result = await gmail_read(message_id="msg1")
        assert "\n\n_meta: " not in result
        assert "alice@example.com" in result

    async def test_read_scope_match_proceeds(self, mock_client: MagicMock) -> None:
        # CATEGORY_PERSONAL label → matches category:work (best-effort post-fetch)
        # Actually no — category:work resolves to CATEGORY_WORK; we test that path.
        # category:work default resolves to CATEGORY_WORK in our impl.
        mock_client.get_message.return_value = _make_message("msg1", ["INBOX", "CATEGORY_WORK"])
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_read(message_id="msg1", scope="work", include_meta=True)
        meta = _parse_meta(result)
        assert meta["matched_total"] == 1
        assert meta["returned"] == 1
        assert "redactions" not in meta
        assert "alice@example.com" in result

    async def test_read_scope_mismatch_redacts(self, mock_client: MagicMock) -> None:
        # No CATEGORY_WORK present → scope=work mismatch
        mock_client.get_message.return_value = _make_message("msg1", ["INBOX", "CATEGORY_PROMOTIONS"])
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_read(message_id="msg1", scope="work", include_meta=True)
        meta = _parse_meta(result)
        assert meta["redactions"] == ["scope_mismatch"]
        assert meta["matched_total"] == 0
        assert meta["returned"] == 0
        # Payload body is the placeholder; original alice@example.com NOT included
        assert "alice@example.com" not in result
        assert "no messages matched scope filter" in result

    async def test_read_scope_env_override_label_lookup(self, mock_client: MagicMock) -> None:
        # Family label resolves to Label_99 via list_labels()
        mock_client.get_message.return_value = _make_message("msg1", ["INBOX", "Label_99"])
        with patch.dict(os.environ, {"GMAIL_FAMILY_LABEL": "label:Family"}):
            result = await gmail_read(message_id="msg1", scope="family", include_meta=True)
        meta = _parse_meta(result)
        assert "redactions" not in meta
        assert meta["matched_total"] == 1

    async def test_read_scope_invalid(self, mock_client: MagicMock) -> None:
        result = await gmail_read(message_id="msg1", scope="bogus")
        assert "Error" in result
        assert "Unknown scope" in result


# ---------------------------------------------------------------------------
# gmail_thread — post-fetch scope verify against union of message labels
# ---------------------------------------------------------------------------


class TestGmailThreadScope:
    async def test_thread_no_scope_unchanged(self, mock_client: MagicMock) -> None:
        mock_client.get_thread.return_value = _make_thread("t1", [["INBOX"]])
        result = await gmail_thread(thread_id="t1")
        assert "\n\n_meta: " not in result

    async def test_thread_scope_match_proceeds(self, mock_client: MagicMock) -> None:
        # Any message in thread carrying CATEGORY_WORK satisfies scope=work
        mock_client.get_thread.return_value = _make_thread("t1", [["INBOX"], ["INBOX", "CATEGORY_WORK"]])
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_thread(thread_id="t1", scope="work", include_meta=True)
        meta = _parse_meta(result)
        assert "redactions" not in meta
        assert meta["matched_total"] == 1

    async def test_thread_scope_mismatch_redacts(self, mock_client: MagicMock) -> None:
        # No CATEGORY_WORK across any thread message → scope=work mismatch
        mock_client.get_thread.return_value = _make_thread("t1", [["INBOX"], ["INBOX", "CATEGORY_PROMOTIONS"]])
        with patch.dict(os.environ, {}, clear=True):
            result = await gmail_thread(thread_id="t1", scope="work", include_meta=True)
        meta = _parse_meta(result)
        assert meta["redactions"] == ["scope_mismatch"]
        assert meta["matched_total"] == 0
        assert "no messages matched scope filter" in result

    async def test_thread_scope_env_override_label_lookup(self, mock_client: MagicMock) -> None:
        mock_client.get_thread.return_value = _make_thread("t1", [["INBOX"], ["INBOX", "Label_99"]])
        with patch.dict(os.environ, {"GMAIL_FAMILY_LABEL": "label:Family"}):
            result = await gmail_thread(thread_id="t1", scope="family", include_meta=True)
        meta = _parse_meta(result)
        assert "redactions" not in meta

    async def test_thread_scope_public_passthrough(self, mock_client: MagicMock) -> None:
        mock_client.get_thread.return_value = _make_thread("t1", [["INBOX"]])
        result = await gmail_thread(thread_id="t1", scope="public", include_meta=True)
        meta = _parse_meta(result)
        assert "redactions" not in meta
        assert meta["filtered_by"] == ["scope=public"]
