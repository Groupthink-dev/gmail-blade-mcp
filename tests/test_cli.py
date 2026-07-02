"""Tests for the CLI dispatch and `auth` verb."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gmail_blade_mcp import cli
from gmail_blade_mcp.errors import AuthError
from gmail_blade_mcp.models import SCOPES_MODIFY, SCOPES_READONLY


def _minted_creds() -> MagicMock:
    creds = MagicMock()
    creds.client_id = "cid"
    creds.client_secret = "csec"
    creds.refresh_token = "rtok"
    return creds


class TestDispatch:
    def test_bare_invocation_runs_server(self):
        with patch("gmail_blade_mcp.server.main") as mock_server:
            cli.main([])
        mock_server.assert_called_once_with()

    def test_serve_runs_server(self):
        with patch("gmail_blade_mcp.server.main") as mock_server:
            cli.main(["serve"])
        mock_server.assert_called_once_with()

    def test_help_prints_usage(self, capsys):
        cli.main(["--help"])
        assert "gmail-blade-mcp auth" in capsys.readouterr().out

    def test_unknown_verb_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["bogus"])
        assert exc.value.code == 2
        assert "Usage" in capsys.readouterr().err


class TestAuthVerb:
    def test_default_scopes_write(self):
        with patch("gmail_blade_mcp.auth.run_interactive_flow", return_value=_minted_creds()) as mock_flow:
            with pytest.raises(SystemExit) as exc:
                cli.main(["auth"])
        assert exc.value.code == 0
        assert mock_flow.call_args[0][0] == SCOPES_MODIFY

    def test_scopes_read(self):
        with patch("gmail_blade_mcp.auth.run_interactive_flow", return_value=_minted_creds()) as mock_flow:
            with pytest.raises(SystemExit):
                cli.main(["auth", "--scopes", "read"])
        assert mock_flow.call_args[0][0] == SCOPES_READONLY

    def test_prints_paste_ready_triple(self, capsys):
        with patch("gmail_blade_mcp.auth.run_interactive_flow", return_value=_minted_creds()):
            with pytest.raises(SystemExit):
                cli.main(["auth"])
        out = capsys.readouterr().out
        assert "GMAIL_OAUTH_CLIENT_ID=cid" in out
        assert "GMAIL_OAUTH_CLIENT_SECRET=csec" in out
        assert "GMAIL_OAUTH_REFRESH_TOKEN=rtok" in out

    def test_no_save_token_passed_through(self, capsys):
        with patch("gmail_blade_mcp.auth.run_interactive_flow", return_value=_minted_creds()) as mock_flow:
            with pytest.raises(SystemExit):
                cli.main(["auth", "--no-save-token"])
        assert mock_flow.call_args.kwargs["save_token"] is False
        assert "NOT written" in capsys.readouterr().out

    def test_client_id_and_secret_flags_forwarded(self):
        with patch("gmail_blade_mcp.auth.run_interactive_flow", return_value=_minted_creds()) as mock_flow:
            with pytest.raises(SystemExit):
                cli.main(["auth", "--client-id", "X", "--client-secret", "Y"])
        assert mock_flow.call_args.kwargs["client_id"] == "X"
        assert mock_flow.call_args.kwargs["client_secret"] == "Y"

    def test_auth_error_exits_1(self, capsys):
        with patch("gmail_blade_mcp.auth.run_interactive_flow", side_effect=AuthError("no client")):
            with pytest.raises(SystemExit) as exc:
                cli.main(["auth"])
        assert exc.value.code == 1
        assert "Error: no client" in capsys.readouterr().err


class TestAuthCheck:
    def test_check_prints_account_and_scopes(self, capsys):
        creds = MagicMock()
        creds.granted_scopes = ["scope-a", "scope-b"]
        service = MagicMock()
        service.users().getProfile(userId="me").execute.return_value = {"emailAddress": "p@example.com"}
        with patch("gmail_blade_mcp.auth.get_credentials", return_value=creds):
            with patch("googleapiclient.discovery.build", return_value=service):
                with pytest.raises(SystemExit) as exc:
                    cli.main(["auth", "--check"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "p@example.com" in out
        assert "scope-a scope-b" in out

    def test_check_falls_back_to_scopes_attr(self, capsys):
        creds = MagicMock()
        creds.granted_scopes = None
        creds.scopes = ["scope-c"]
        service = MagicMock()
        service.users().getProfile(userId="me").execute.return_value = {"emailAddress": "p@example.com"}
        with patch("gmail_blade_mcp.auth.get_credentials", return_value=creds):
            with patch("googleapiclient.discovery.build", return_value=service):
                with pytest.raises(SystemExit) as exc:
                    cli.main(["auth", "--check"])
        assert exc.value.code == 0
        assert "scope-c" in capsys.readouterr().out

    def test_check_auth_error_exits_1(self, capsys):
        with patch("gmail_blade_mcp.auth.get_credentials", side_effect=AuthError("no creds")):
            with pytest.raises(SystemExit) as exc:
                cli.main(["auth", "--check"])
        assert exc.value.code == 1
        assert "no creds" in capsys.readouterr().err
