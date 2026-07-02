"""Tests for the non-interactive credential resolution chain in auth.py."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

from gmail_blade_mcp import auth
from gmail_blade_mcp.errors import AuthError, ScopeError
from gmail_blade_mcp.models import SCOPES_MODIFY, SCOPES_READONLY

ENV_TRIPLE = {
    auth.ENV_CLIENT_ID: "client-id-123",
    auth.ENV_CLIENT_SECRET: "client-secret-456",
    auth.ENV_REFRESH_TOKEN: "refresh-token-789",
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point CREDENTIALS_DIR at tmp and clear the OAuth env vars."""
    monkeypatch.setattr(auth, "CREDENTIALS_DIR", str(tmp_path))
    for var in ENV_TRIPLE:
        monkeypatch.delenv(var, raising=False)
    yield


def _write_token_file(tmp_path, **overrides) -> str:
    payload = {
        "token": "cached-access-token",
        "refresh_token": "file-refresh-token",
        "token_uri": auth.GOOGLE_TOKEN_URI,
        "client_id": "file-client-id",
        "client_secret": "file-client-secret",
        "scopes": SCOPES_READONLY,
        "expiry": "2099-01-01T00:00:00Z",
    }
    payload.update(overrides)
    path = tmp_path / "token.json"
    path.write_text(json.dumps(payload))
    return str(path)


# ---------------------------------------------------------------------------
# Env-triple mode
# ---------------------------------------------------------------------------


class TestEnvCredentials:
    def test_full_triple_builds_in_memory_credentials(self, monkeypatch, tmp_path):
        for k, v in ENV_TRIPLE.items():
            monkeypatch.setenv(k, v)
        # Decoy token.json must never be consulted in env mode
        _write_token_file(tmp_path, refresh_token="DECOY")

        with patch.object(auth, "_refresh") as mock_refresh:
            creds = auth.get_credentials(SCOPES_READONLY)

        mock_refresh.assert_called_once_with(creds)
        assert creds.refresh_token == "refresh-token-789"
        assert creds.client_id == "client-id-123"
        assert creds.client_secret == "client-secret-456"
        assert creds.token_uri == auth.GOOGLE_TOKEN_URI
        assert list(creds.scopes) == SCOPES_READONLY

    def test_env_mode_never_writes_disk(self, monkeypatch, tmp_path):
        for k, v in ENV_TRIPLE.items():
            monkeypatch.setenv(k, v)
        with patch.object(auth, "_refresh"):
            auth.get_credentials(SCOPES_MODIFY)
        assert not os.path.exists(tmp_path / "token.json")

    def test_values_are_stripped(self, monkeypatch):
        monkeypatch.setenv(auth.ENV_CLIENT_ID, "  client-id-123  ")
        monkeypatch.setenv(auth.ENV_CLIENT_SECRET, "client-secret-456\n")
        monkeypatch.setenv(auth.ENV_REFRESH_TOKEN, "refresh-token-789\n")
        with patch.object(auth, "_refresh"):
            creds = auth.get_credentials(SCOPES_READONLY)
        assert creds.refresh_token == "refresh-token-789"
        assert creds.client_id == "client-id-123"

    @pytest.mark.parametrize("missing", [auth.ENV_CLIENT_ID, auth.ENV_CLIENT_SECRET])
    def test_partial_triple_raises_naming_missing_var(self, monkeypatch, missing):
        for k, v in ENV_TRIPLE.items():
            if k != missing:
                monkeypatch.setenv(k, v)
        with pytest.raises(AuthError, match=missing):
            auth.get_credentials(SCOPES_READONLY)

    def test_empty_refresh_token_is_not_env_mode(self, monkeypatch):
        monkeypatch.setenv(auth.ENV_REFRESH_TOKEN, "   ")
        with pytest.raises(AuthError, match="No Gmail credentials found"):
            auth.get_credentials(SCOPES_READONLY)

    def test_id_and_secret_without_refresh_token_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv(auth.ENV_CLIENT_ID, "client-id-123")
        monkeypatch.setenv(auth.ENV_CLIENT_SECRET, "client-secret-456")
        _write_token_file(tmp_path)
        with patch.object(auth, "_refresh"):
            creds = auth.get_credentials(SCOPES_READONLY)
        assert creds.refresh_token == "file-refresh-token"


# ---------------------------------------------------------------------------
# token.json mode
# ---------------------------------------------------------------------------


class TestTokenFileCredentials:
    def test_loads_and_chmods_existing_token(self, tmp_path):
        path = _write_token_file(tmp_path)
        os.chmod(path, 0o644)
        with patch.object(auth, "_refresh"):
            creds = auth.get_credentials(SCOPES_READONLY)
        assert creds.refresh_token == "file-refresh-token"
        assert os.stat(path).st_mode & 0o777 == 0o600

    def test_invalid_token_is_refreshed_and_saved(self, tmp_path):
        _write_token_file(tmp_path, expiry="2000-01-01T00:00:00Z")

        def fake_refresh(creds):
            creds.token = "new-access-token"

        with patch.object(auth, "_refresh", side_effect=fake_refresh) as mock_refresh:
            with patch.object(auth, "_save_token") as mock_save:
                auth.get_credentials(SCOPES_READONLY)
        mock_refresh.assert_called_once()
        mock_save.assert_called_once()

    def test_expired_token_without_refresh_token_raises(self, tmp_path):
        _write_token_file(tmp_path, refresh_token=None, expiry="2000-01-01T00:00:00Z")
        with pytest.raises(AuthError, match="gmail-blade-mcp auth"):
            auth.get_credentials(SCOPES_READONLY)


# ---------------------------------------------------------------------------
# Headless guard
# ---------------------------------------------------------------------------


class TestHeadlessGuard:
    def test_no_credentials_raises_with_remediation(self):
        with pytest.raises(AuthError, match="gmail-blade-mcp auth"):
            auth.get_credentials(SCOPES_READONLY)

    def test_no_browser_path_reachable_from_get_gmail_service(self):
        # Belt and braces: prove the interactive flow cannot fire in server mode.
        with patch("google_auth_oauthlib.flow.InstalledAppFlow", side_effect=AssertionError("browser opened")):
            with pytest.raises(AuthError):
                auth.get_gmail_service(readonly=True)


# ---------------------------------------------------------------------------
# Refresh error mapping
# ---------------------------------------------------------------------------


class TestRefreshMapping:
    def _creds_raising(self, message: str) -> MagicMock:
        creds = MagicMock()
        creds.refresh.side_effect = RefreshError(message)
        return creds

    def test_invalid_scope_maps_to_scope_error(self):
        with pytest.raises(ScopeError, match="--scopes write"):
            auth._refresh(self._creds_raising("invalid_scope: Bad Request"))

    def test_invalid_grant_maps_to_auth_error(self):
        with pytest.raises(AuthError, match="expired or revoked"):
            auth._refresh(self._creds_raising("invalid_grant: Token has been revoked"))

    def test_invalid_client_maps_to_auth_error(self):
        with pytest.raises(AuthError, match="client id/secret"):
            auth._refresh(self._creds_raising("invalid_client: Unauthorized"))

    def test_other_refresh_error_wrapped(self):
        with pytest.raises(AuthError, match="Token refresh failed"):
            auth._refresh(self._creds_raising("internal_failure"))


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------


class TestSaveToken:
    def test_save_token_writes_0600(self, tmp_path):
        creds = MagicMock()
        creds.to_json.return_value = '{"token": "t"}'
        auth._save_token(creds)
        path = tmp_path / "token.json"
        assert path.read_text() == '{"token": "t"}'
        assert os.stat(path).st_mode & 0o777 == 0o600

    def test_save_token_truncates_existing(self, tmp_path):
        (tmp_path / "token.json").write_text("x" * 500)
        creds = MagicMock()
        creds.to_json.return_value = '{"token": "t"}'
        auth._save_token(creds)
        assert (tmp_path / "token.json").read_text() == '{"token": "t"}'


# ---------------------------------------------------------------------------
# Interactive flow (CLI-only) — client-config precedence
# ---------------------------------------------------------------------------


class TestRunInteractiveFlow:
    def test_explicit_args_take_precedence(self, monkeypatch):
        monkeypatch.setenv(auth.ENV_CLIENT_ID, "env-id")
        monkeypatch.setenv(auth.ENV_CLIENT_SECRET, "env-secret")
        with patch("google_auth_oauthlib.flow.InstalledAppFlow") as mock_flow_cls:
            flow = mock_flow_cls.from_client_config.return_value
            flow.run_local_server.return_value = MagicMock()
            auth.run_interactive_flow(SCOPES_MODIFY, client_id="arg-id", client_secret="arg-secret", save_token=False)
        config = mock_flow_cls.from_client_config.call_args[0][0]
        assert config["installed"]["client_id"] == "arg-id"

    def test_env_client_config_used_when_no_args(self, monkeypatch):
        monkeypatch.setenv(auth.ENV_CLIENT_ID, "env-id")
        monkeypatch.setenv(auth.ENV_CLIENT_SECRET, "env-secret")
        with patch("google_auth_oauthlib.flow.InstalledAppFlow") as mock_flow_cls:
            flow = mock_flow_cls.from_client_config.return_value
            flow.run_local_server.return_value = MagicMock()
            auth.run_interactive_flow(SCOPES_MODIFY, save_token=False)
        config = mock_flow_cls.from_client_config.call_args[0][0]
        assert config["installed"]["client_id"] == "env-id"

    def test_credentials_json_fallback(self, tmp_path):
        secrets_path = tmp_path / "credentials.json"
        secrets_path.write_text('{"installed": {}}')
        with patch("google_auth_oauthlib.flow.InstalledAppFlow") as mock_flow_cls:
            flow = mock_flow_cls.from_client_secrets_file.return_value
            flow.run_local_server.return_value = MagicMock()
            auth.run_interactive_flow(SCOPES_MODIFY, save_token=False)
        mock_flow_cls.from_client_secrets_file.assert_called_once_with(str(secrets_path), SCOPES_MODIFY)

    def test_no_client_config_raises(self):
        with pytest.raises(AuthError, match="No OAuth client configured"):
            auth.run_interactive_flow(SCOPES_MODIFY)

    def test_save_token_persists_minted_credentials(self, tmp_path):
        minted = MagicMock()
        minted.to_json.return_value = '{"token": "minted"}'
        with patch("google_auth_oauthlib.flow.InstalledAppFlow") as mock_flow_cls:
            flow = mock_flow_cls.from_client_config.return_value
            flow.run_local_server.return_value = minted
            auth.run_interactive_flow(SCOPES_MODIFY, client_id="i", client_secret="s")
        assert (tmp_path / "token.json").read_text() == '{"token": "minted"}'
