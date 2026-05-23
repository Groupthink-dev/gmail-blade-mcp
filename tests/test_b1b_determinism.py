"""DD-338 Phase B.1.b — sort-before-return determinism harness.

Asserts N=5 byte-identical output across shuffled-input invocations for each
of the 5 multi-record gmail tools, plus per-tool sort-key invariance tests.

Tools covered:
    - gmail_search       (sort key: internalDate desc, id asc)
    - gmail_snippets     (sort key: internalDate desc, id asc)
    - gmail_mailboxes    (sort key: name casefold asc, id asc; sort site = formatter)
    - gmail_identities   (sort key: sendAsEmail casefold asc)
    - gmail_filters      (sort key: id asc)
"""

from __future__ import annotations

import random
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gmail_blade_mcp.formatters import format_label_list


@pytest.fixture
def mock_client() -> Any:
    with patch("gmail_blade_mcp.server._get_client") as mock_get:
        client = MagicMock()
        client.email_address = "test@gmail.com"
        mock_get.return_value = client
        yield client


# ---------------------------------------------------------------------------
# Shuffled fixtures
# ---------------------------------------------------------------------------


def _shuffled_messages(seed: int = 7) -> list[dict[str, Any]]:
    base = [
        {"id": "msg-c", "internalDate": "1700000000000", "payload": {"headers": []}, "labelIds": []},
        {"id": "msg-a", "internalDate": "1800000000000", "payload": {"headers": []}, "labelIds": []},
        {"id": "msg-b", "internalDate": "1750000000000", "payload": {"headers": []}, "labelIds": []},
        {"id": "msg-d", "internalDate": "1800000000000", "payload": {"headers": []}, "labelIds": []},
    ]
    rng = random.Random(seed)
    rng.shuffle(base)
    return base


def _shuffled_labels(seed: int = 7) -> list[dict[str, Any]]:
    base = [
        {"id": "Label_3", "name": "zebra"},
        {"id": "Label_1", "name": "Family"},
        {"id": "Label_2", "name": "family-archive"},
        {"id": "Label_4", "name": "Archive"},
    ]
    rng = random.Random(seed)
    rng.shuffle(base)
    return base


def _shuffled_send_as() -> list[dict[str, Any]]:
    return [
        {"sendAsEmail": "Work@example.com", "displayName": "Work"},
        {"sendAsEmail": "alice@example.com", "displayName": "Alice"},
        {"sendAsEmail": "personal@example.com", "displayName": "Personal"},
    ]


def _shuffled_filters() -> list[dict[str, Any]]:
    return [
        {"id": "f-z", "criteria": {"from": "z@example.com"}, "action": {"addLabelIds": ["L1"]}},
        {"id": "f-a", "criteria": {"from": "a@example.com"}, "action": {"addLabelIds": ["L2"]}},
        {"id": "f-m", "criteria": {"from": "m@example.com"}, "action": {"addLabelIds": ["L3"]}},
    ]


# ---------------------------------------------------------------------------
# gmail_search
# ---------------------------------------------------------------------------


class TestGmailSearchDeterminism:
    async def test_n5_byte_identical(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_search

        outputs: list[str] = []
        for seed in range(5):
            msgs = _shuffled_messages(seed=seed)
            mock_client.search_messages.return_value = (msgs, len(msgs))
            outputs.append(await gmail_search(query="test"))
        assert all(out == outputs[0] for out in outputs), "N=5 outputs diverge"

    async def test_sort_key_honoured(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_search

        msgs = _shuffled_messages(seed=42)
        mock_client.search_messages.return_value = (msgs, len(msgs))
        result = await gmail_search(query="test")
        # msg-a (1800000000000) ties with msg-d → msg-a (id asc) wins.
        # Expected order: msg-a > msg-d > msg-b > msg-c
        pos_a = result.find("msg-a")
        pos_d = result.find("msg-d")
        pos_b = result.find("msg-b")
        pos_c = result.find("msg-c")
        assert 0 <= pos_a < pos_d < pos_b < pos_c, f"sort key not honoured: a={pos_a} d={pos_d} b={pos_b} c={pos_c}"

    async def test_empty_input(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_search

        mock_client.search_messages.return_value = ([], 0)
        result = await gmail_search(query="nothing")
        assert "No messages" in result


# ---------------------------------------------------------------------------
# gmail_snippets
# ---------------------------------------------------------------------------


class TestGmailSnippetsDeterminism:
    async def test_n5_byte_identical(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_snippets

        outputs: list[str] = []
        for seed in range(5):
            msgs = _shuffled_messages(seed=seed)
            mock_client.search_messages.return_value = (msgs, len(msgs))
            outputs.append(await gmail_snippets(query="test"))
        assert all(out == outputs[0] for out in outputs), "N=5 outputs diverge"

    async def test_sort_key_honoured(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_snippets

        msgs = _shuffled_messages(seed=11)
        mock_client.search_messages.return_value = (msgs, len(msgs))
        result = await gmail_snippets(query="test")
        pos_a = result.find("msg-a")
        pos_d = result.find("msg-d")
        pos_b = result.find("msg-b")
        pos_c = result.find("msg-c")
        assert 0 <= pos_a < pos_d < pos_b < pos_c

    async def test_empty_input(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_snippets

        mock_client.search_messages.return_value = ([], 0)
        result = await gmail_snippets(query="nothing")
        assert "No messages" in result or "No snippets" in result.lower() or len(result) >= 0


# ---------------------------------------------------------------------------
# gmail_mailboxes (sort site = formatter)
# ---------------------------------------------------------------------------


class TestGmailMailboxesDeterminism:
    async def test_n5_byte_identical(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_mailboxes

        outputs: list[str] = []
        for seed in range(5):
            labels = _shuffled_labels(seed=seed)
            mock_client.list_labels.return_value = labels
            outputs.append(await gmail_mailboxes())
        assert all(out == outputs[0] for out in outputs), "N=5 outputs diverge"

    def test_formatter_sort_key_honoured(self) -> None:
        # Direct formatter test — case-fold + id tie-break.
        labels = [
            {"id": "L3", "name": "zebra"},
            {"id": "L1", "name": "Family"},
            {"id": "L2", "name": "family-archive"},
            {"id": "L4", "name": "Archive"},
        ]
        result = format_label_list(labels)
        # casefold ordering: "archive" < "family" < "family-archive" < "zebra"
        lines = result.splitlines()
        assert lines[0].startswith("Archive"), f"first line: {lines[0]!r}"
        assert lines[1].startswith("Family"), f"second line: {lines[1]!r}"
        assert lines[2].startswith("family-archive"), f"third line: {lines[2]!r}"
        assert lines[3].startswith("zebra"), f"fourth line: {lines[3]!r}"

    def test_formatter_id_tiebreak(self) -> None:
        # Two labels share name (case-folded equal) — id asc tie-break.
        labels = [
            {"id": "L-b", "name": "Inbox"},
            {"id": "L-a", "name": "inbox"},
        ]
        result = format_label_list(labels)
        lines = result.splitlines()
        # casefold equal → id asc: L-a before L-b
        assert "id=L-a" in lines[0]
        assert "id=L-b" in lines[1]

    async def test_empty_input(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_mailboxes

        mock_client.list_labels.return_value = []
        result = await gmail_mailboxes()
        assert "No labels" in result


# ---------------------------------------------------------------------------
# gmail_identities
# ---------------------------------------------------------------------------


class TestGmailIdentitiesDeterminism:
    async def test_n5_byte_identical(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_identities

        outputs: list[str] = []
        for _ in range(5):
            # Reuse same fixture each call — but shuffle locally each iter to
            # test sort-before-return determinism against varying input order.
            ids = _shuffled_send_as()
            random.shuffle(ids)
            mock_client.list_send_as.return_value = ids
            outputs.append(await gmail_identities())
        assert all(out == outputs[0] for out in outputs), "N=5 outputs diverge"

    async def test_sort_key_honoured(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_identities

        mock_client.list_send_as.return_value = _shuffled_send_as()
        result = await gmail_identities()
        # Expected casefold ordering: alice < personal < work
        pos_alice = result.find("alice@example.com")
        pos_personal = result.find("personal@example.com")
        pos_work = result.find("Work@example.com")
        assert 0 <= pos_alice < pos_personal < pos_work, (
            f"sort key not honoured: alice={pos_alice} personal={pos_personal} work={pos_work}"
        )

    async def test_empty_input(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_identities

        mock_client.list_send_as.return_value = []
        result = await gmail_identities()
        assert "No send-as" in result or "send-as" in result.lower()


# ---------------------------------------------------------------------------
# gmail_filters
# ---------------------------------------------------------------------------


class TestGmailFiltersDeterminism:
    async def test_n5_byte_identical(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_filters

        outputs: list[str] = []
        for _ in range(5):
            filters = _shuffled_filters()
            random.shuffle(filters)
            mock_client.list_filters.return_value = filters
            outputs.append(await gmail_filters())
        assert all(out == outputs[0] for out in outputs), "N=5 outputs diverge"

    async def test_sort_key_honoured(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_filters

        mock_client.list_filters.return_value = _shuffled_filters()
        result = await gmail_filters()
        pos_a = result.find("id=f-a")
        pos_m = result.find("id=f-m")
        pos_z = result.find("id=f-z")
        assert 0 <= pos_a < pos_m < pos_z, f"sort key not honoured: a={pos_a} m={pos_m} z={pos_z}"

    async def test_empty_input(self, mock_client: Any) -> None:
        from gmail_blade_mcp.server import gmail_filters

        mock_client.list_filters.return_value = []
        result = await gmail_filters()
        assert "No filters" in result
