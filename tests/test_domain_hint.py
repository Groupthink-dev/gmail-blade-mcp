"""DD-338 A.2.dom.c — domain_hint pattern engine tests.

Covers the pure ``compute_domain_hint`` + ``load_patterns_from_yaml``
primitives plus the Gmail-specific ``_gmail_field_projector`` and the
end-to-end ``_compute_domain_hints_for_records`` integration helper.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from stallari_mcp_helpers import (
    Pattern,
    compute_domain_hint,
    load_patterns_from_yaml,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_message(
    msg_id: str,
    from_addr: str = "alice@example.com",
    label_ids: list[str] | None = None,
    subject: str = "Test",
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": f"thread-{msg_id}",
        "labelIds": label_ids if label_ids is not None else ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": subject},
            ]
        },
    }


# ---------------------------------------------------------------------------
# compute_domain_hint — pure engine
# ---------------------------------------------------------------------------


class TestComputeDomainHint:
    def test_empty_patterns_returns_none(self) -> None:
        rec = {"from": "alice@family.com"}
        assert compute_domain_hint(rec, []) is None

    def test_single_contains_match(self) -> None:
        rec = {"from": "alice@family.com"}
        patterns = [Pattern(field="from", op="contains", value="@family.com", domain="family")]
        assert compute_domain_hint(rec, patterns) == "family"

    def test_first_match_wins(self) -> None:
        rec = {"from": "alice@family.com"}
        patterns = [
            Pattern(field="from", op="contains", value="@family.com", domain="family"),
            Pattern(field="from", op="contains", value="alice", domain="contacts"),
        ]
        assert compute_domain_hint(rec, patterns) == "family"

    def test_glob_wildcard_matches(self) -> None:
        rec = {"from": "alice@acme.corp"}
        patterns = [Pattern(field="from", op="glob", value="*@acme.*", domain="work")]
        assert compute_domain_hint(rec, patterns) == "work"

    def test_field_absent_no_match(self) -> None:
        rec = {"subject": "hi"}
        patterns = [Pattern(field="from", op="contains", value="@anywhere", domain="any")]
        assert compute_domain_hint(rec, patterns) is None

    def test_equals_op_exact_match(self) -> None:
        rec = {"label": "Label_42"}
        patterns = [
            Pattern(field="label", op="equals", value="Label_99", domain="family"),
            Pattern(field="label", op="equals", value="Label_42", domain="work"),
        ]
        assert compute_domain_hint(rec, patterns) == "work"

    def test_list_field_element_match(self) -> None:
        rec = {"labelIds": ["INBOX", "Label_99", "CATEGORY_PERSONAL"]}
        patterns = [Pattern(field="labelIds", op="equals", value="Label_99", domain="family")]
        assert compute_domain_hint(rec, patterns) == "family"

    def test_unknown_op_silently_skipped(self) -> None:
        rec = {"from": "alice@family.com"}
        patterns = [
            Pattern(field="from", op="regex", value=".*", domain="any"),  # unsupported op
            Pattern(field="from", op="contains", value="@family.com", domain="family"),
        ]
        assert compute_domain_hint(rec, patterns) == "family"

    def test_unknown_op_no_fallback_match_returns_none(self) -> None:
        rec = {"from": "alice@family.com"}
        patterns = [Pattern(field="from", op="regex", value=".*", domain="any")]
        assert compute_domain_hint(rec, patterns) is None


# ---------------------------------------------------------------------------
# load_patterns_from_yaml — YAML loader
# ---------------------------------------------------------------------------


class TestLoadPatternsFromYAML:
    def test_empty_string_returns_empty(self) -> None:
        assert load_patterns_from_yaml("") == []

    def test_valid_yaml_two_patterns(self) -> None:
        yaml_str = """
patterns:
  - field: from
    op: contains
    value: "@family.com"
    domain: family
  - field: labelIds
    op: equals
    value: Label_42
    domain: work
"""
        patterns = load_patterns_from_yaml(yaml_str)
        assert len(patterns) == 2
        assert patterns[0] == Pattern(field="from", op="contains", value="@family.com", domain="family")
        assert patterns[1] == Pattern(field="labelIds", op="equals", value="Label_42", domain="work")

    def test_malformed_yaml_returns_empty(self) -> None:
        # Unclosed quote ⇒ yaml.YAMLError ⇒ []
        assert load_patterns_from_yaml('patterns: [{"unclosed') == []

    def test_missing_patterns_key_returns_empty(self) -> None:
        assert load_patterns_from_yaml("other_key: 42") == []

    def test_pattern_missing_required_key_skipped(self) -> None:
        yaml_str = """
patterns:
  - field: from
    op: contains
    # missing value + domain
  - field: from
    op: equals
    value: alice@x.com
    domain: contacts
"""
        patterns = load_patterns_from_yaml(yaml_str)
        assert len(patterns) == 1
        assert patterns[0].domain == "contacts"

    def test_non_list_patterns_returns_empty(self) -> None:
        assert load_patterns_from_yaml("patterns: not-a-list") == []


# ---------------------------------------------------------------------------
# _gmail_field_projector — Gmail record shape adapter
# ---------------------------------------------------------------------------


class TestGmailFieldProjector:
    def test_from_header_extracted(self) -> None:
        from gmail_blade_mcp.server import _gmail_field_projector

        rec = _make_message("m1", from_addr="alice@family.com")
        assert _gmail_field_projector(rec, "from") == "alice@family.com"

    def test_subject_header_extracted(self) -> None:
        from gmail_blade_mcp.server import _gmail_field_projector

        rec = _make_message("m1", subject="hello world")
        assert _gmail_field_projector(rec, "subject") == "hello world"

    def test_labelids_returned_as_list(self) -> None:
        from gmail_blade_mcp.server import _gmail_field_projector

        rec = _make_message("m1", label_ids=["INBOX", "Label_99"])
        assert _gmail_field_projector(rec, "labelIds") == ["INBOX", "Label_99"]

    def test_id_returned_as_scalar(self) -> None:
        from gmail_blade_mcp.server import _gmail_field_projector

        rec = _make_message("msg-xyz")
        assert _gmail_field_projector(rec, "id") == "msg-xyz"

    def test_threadid_returned_as_scalar(self) -> None:
        from gmail_blade_mcp.server import _gmail_field_projector

        rec = _make_message("msg-xyz")
        assert _gmail_field_projector(rec, "threadId") == "thread-msg-xyz"

    def test_unknown_field_returns_none(self) -> None:
        from gmail_blade_mcp.server import _gmail_field_projector

        rec = _make_message("m1")
        assert _gmail_field_projector(rec, "bcc") is None
        assert _gmail_field_projector(rec, "totallyMadeUp") is None

    def test_case_insensitive_header_match(self) -> None:
        from gmail_blade_mcp.server import _gmail_field_projector

        rec = {
            "id": "m1",
            "payload": {"headers": [{"name": "FROM", "value": "alice@x.com"}]},
        }
        # field name is also case-folded
        assert _gmail_field_projector(rec, "From") == "alice@x.com"

    def test_missing_payload_returns_none(self) -> None:
        from gmail_blade_mcp.server import _gmail_field_projector

        assert _gmail_field_projector({"id": "m1"}, "from") is None


# ---------------------------------------------------------------------------
# _compute_domain_hints_for_records + _load_blade_config integration
# ---------------------------------------------------------------------------


class TestComputeDomainHintsForRecords:
    def test_empty_patterns_returns_empty_dict(self) -> None:
        from gmail_blade_mcp import server

        with patch.object(server, "_PATTERNS", []):
            out = server._compute_domain_hints_for_records([_make_message("m1")])
        assert out == {}

    def test_records_with_match_keyed_by_id(self) -> None:
        from gmail_blade_mcp import server

        patterns = [
            Pattern(field="from", op="contains", value="@family.com", domain="family"),
        ]
        records = [
            _make_message("m1", from_addr="alice@family.com"),
            _make_message("m2", from_addr="bob@example.org"),
            _make_message("m3", from_addr="carol@family.com"),
        ]
        with patch.object(server, "_PATTERNS", patterns):
            out = server._compute_domain_hints_for_records(records)
        assert out == {"m1": "family", "m3": "family"}

    def test_record_without_id_skipped(self) -> None:
        from gmail_blade_mcp import server

        patterns = [Pattern(field="from", op="contains", value="@x.com", domain="any")]
        records: list[dict[str, Any]] = [
            {"payload": {"headers": [{"name": "From", "value": "bob@x.com"}]}},  # no id
        ]
        with patch.object(server, "_PATTERNS", patterns):
            out = server._compute_domain_hints_for_records(records)
        assert out == {}


class TestLoadBladeConfig:
    def test_missing_config_returns_empty(self, tmp_path: Any) -> None:
        from gmail_blade_mcp.server import _load_blade_config

        with patch.dict(os.environ, {"STALLARI_STATE_ROOT": str(tmp_path)}):
            assert _load_blade_config("gmail-blade-mcp") == []

    def test_valid_config_loaded(self, tmp_path: Any) -> None:
        from gmail_blade_mcp.server import _load_blade_config

        cfg_dir = tmp_path / "blade-config" / "gmail-blade-mcp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            "patterns:\n"
            "  - field: from\n"
            "    op: contains\n"
            '    value: "@family.com"\n'
            "    domain: family\n"
        )
        with patch.dict(os.environ, {"STALLARI_STATE_ROOT": str(tmp_path)}):
            patterns = _load_blade_config("gmail-blade-mcp")
        assert len(patterns) == 1
        assert patterns[0].domain == "family"

    def test_blade_id_sanitized(self, tmp_path: Any) -> None:
        """Slashes / mixed case in blade-id are normalised to match Swift writer."""
        from gmail_blade_mcp.server import _load_blade_config, _sanitize_blade_id

        assert _sanitize_blade_id("Gmail-Blade-MCP") == "gmail-blade-mcp"
        assert _sanitize_blade_id("ns/sub") == "ns_sub"
        cfg_dir = tmp_path / "blade-config" / "gmail-blade-mcp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text("patterns: []\n")
        with patch.dict(os.environ, {"STALLARI_STATE_ROOT": str(tmp_path)}):
            # Mixed-case blade-id resolves to lowercase dir
            assert _load_blade_config("Gmail-Blade-MCP") == []


# ---------------------------------------------------------------------------
# Integration: gmail_search emits domain_hints in _meta envelope
# ---------------------------------------------------------------------------


def _parse_meta(payload: str) -> dict[str, Any]:
    match = re.search(r"\n\n_meta: (\{.*\})$", payload, re.DOTALL)
    assert match is not None, f"No _meta envelope found in:\n{payload}"
    return json.loads(match.group(1))


class TestDomainHintsInMetaEnvelope:
    @pytest.fixture
    def mock_client(self) -> Any:
        with patch("gmail_blade_mcp.server._get_client") as mock_get:
            client = MagicMock()
            client.email_address = "test@gmail.com"
            client.list_labels.return_value = []
            mock_get.return_value = client
            yield client

    async def test_search_emits_domain_hints_when_patterns_match(
        self, mock_client: Any
    ) -> None:
        from gmail_blade_mcp import server
        from gmail_blade_mcp.server import gmail_search

        patterns = [Pattern(field="from", op="contains", value="@family.com", domain="family")]
        mock_client.search_messages.return_value = (
            [
                _make_message("m1", from_addr="alice@family.com"),
                _make_message("m2", from_addr="bob@example.org"),
            ],
            2,
        )
        with patch.object(server, "_PATTERNS", patterns):
            result = await gmail_search(query="", include_meta=True)
        meta = _parse_meta(result)
        assert meta["domain_hints"] == {"m1": "family"}

    async def test_search_omits_domain_hints_when_no_match(self, mock_client: Any) -> None:
        from gmail_blade_mcp import server
        from gmail_blade_mcp.server import gmail_search

        patterns = [Pattern(field="from", op="contains", value="@nomatch.tld", domain="any")]
        mock_client.search_messages.return_value = (
            [_make_message("m1", from_addr="alice@family.com")],
            1,
        )
        with patch.object(server, "_PATTERNS", patterns):
            result = await gmail_search(query="", include_meta=True)
        meta = _parse_meta(result)
        assert "domain_hints" not in meta

    async def test_search_omits_domain_hints_when_no_patterns(
        self, mock_client: Any
    ) -> None:
        from gmail_blade_mcp import server
        from gmail_blade_mcp.server import gmail_search

        mock_client.search_messages.return_value = (
            [_make_message("m1", from_addr="alice@family.com")],
            1,
        )
        with patch.object(server, "_PATTERNS", []):
            result = await gmail_search(query="", include_meta=True)
        meta = _parse_meta(result)
        assert "domain_hints" not in meta
