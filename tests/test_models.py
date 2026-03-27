"""Tests for models (constants, write gate)."""

from __future__ import annotations

import os
from unittest.mock import patch

from gmail_blade_mcp.models import is_write_enabled, require_write


class TestWriteGate:
    def test_write_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert not is_write_enabled()
            assert require_write() is not None
            assert "Error" in (require_write() or "")

    def test_write_enabled(self) -> None:
        with patch.dict(os.environ, {"GMAIL_WRITE_ENABLED": "true"}):
            assert is_write_enabled()
            assert require_write() is None

    def test_write_enabled_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"GMAIL_WRITE_ENABLED": "True"}):
            assert is_write_enabled()

    def test_write_disabled_with_wrong_value(self) -> None:
        with patch.dict(os.environ, {"GMAIL_WRITE_ENABLED": "yes"}):
            assert not is_write_enabled()
