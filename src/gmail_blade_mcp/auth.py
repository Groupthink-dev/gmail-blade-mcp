"""Authentication for Gmail Blade MCP.

Two auth layers:

1. **Gmail OAuth 2.0** — credential resolution is strictly non-interactive in
   server mode. Precedence:

   1. Env triple ``GMAIL_OAUTH_CLIENT_ID`` / ``GMAIL_OAUTH_CLIENT_SECRET`` /
      ``GMAIL_OAUTH_REFRESH_TOKEN`` (Stallari Connections injection) — built
      in memory, never touches disk. ``GMAIL_OAUTH_REFRESH_TOKEN`` being set
      switches this mode on; a partial triple is a hard error.
   2. ``~/.gmail-blade/token.json`` (written 0600) — standalone/dev use.
   3. Typed :class:`AuthError` with remediation — the server has **no code
      path** that can open a browser. Interactive consent lives only in
      :func:`run_interactive_flow`, called by the ``gmail-blade-mcp auth``
      CLI verb.

   ``GOOGLE_API_KEY`` is the Gemini key (see ``gemini.py``) and is never
   consulted here.

2. **MCP HTTP bearer auth** — Optional bearer token for remote/tunnel access.
   Set ``GMAIL_MCP_API_TOKEN`` env var. If unset, bearer auth is disabled
   (localhost-only setups work without configuration).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from gmail_blade_mcp.errors import AuthError, ScopeError
from gmail_blade_mcp.models import CREDENTIALS_DIR, SCOPES_MODIFY, SCOPES_READONLY

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

ENV_CLIENT_ID = "GMAIL_OAUTH_CLIENT_ID"
ENV_CLIENT_SECRET = "GMAIL_OAUTH_CLIENT_SECRET"  # noqa: S105
ENV_REFRESH_TOKEN = "GMAIL_OAUTH_REFRESH_TOKEN"  # noqa: S105

_REMEDIATION = (
    "Run `gmail-blade-mcp auth` to authorize interactively, or set "
    f"{ENV_CLIENT_ID}/{ENV_CLIENT_SECRET}/{ENV_REFRESH_TOKEN}."
)


# ---------------------------------------------------------------------------
# Gmail OAuth 2.0 — credential resolution (never interactive)
# ---------------------------------------------------------------------------


def _env(name: str) -> str | None:
    """Read an env var, treating empty/whitespace-only values as unset."""
    value = os.environ.get(name, "").strip()
    return value or None


def _token_path() -> str:
    return os.path.join(CREDENTIALS_DIR, "token.json")


def _client_secrets_path() -> str:
    return os.path.join(CREDENTIALS_DIR, "credentials.json")


def _env_credentials(scopes: list[str]) -> Any:
    """Build in-memory credentials from the Stallari-injected env triple.

    Returns None when ``GMAIL_OAUTH_REFRESH_TOKEN`` is unset (not env mode).
    Raises AuthError when the triple is partial — failing loud beats silently
    falling through to a different account's token.json.
    """
    from google.oauth2.credentials import Credentials

    refresh_token = _env(ENV_REFRESH_TOKEN)
    if refresh_token is None:
        return None

    client_id = _env(ENV_CLIENT_ID)
    client_secret = _env(ENV_CLIENT_SECRET)
    missing = [
        name for name, value in ((ENV_CLIENT_ID, client_id), (ENV_CLIENT_SECRET, client_secret)) if value is None
    ]
    if missing:
        raise AuthError(f"{ENV_REFRESH_TOKEN} is set but {' and '.join(missing)} missing. Set the full triple.")

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )


def _token_file_credentials(scopes: list[str]) -> Any:
    """Load credentials from token.json, or None if absent."""
    from google.oauth2.credentials import Credentials

    token_path = _token_path()
    if not os.path.exists(token_path):
        return None
    # Migration: older versions wrote the token with default (0644) perms
    try:
        os.chmod(token_path, 0o600)
    except OSError:  # pragma: no cover - permission oddities are non-fatal
        pass
    return Credentials.from_authorized_user_file(token_path, scopes)


def _refresh(creds: Any) -> None:
    """Refresh an access token, mapping google-auth failures to typed errors."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request

    try:
        creds.refresh(Request())
    except RefreshError as e:
        message = str(e)
        lower = message.lower()
        if "invalid_scope" in lower:
            raise ScopeError(
                "Refresh token grant lacks the requested Gmail scopes. "
                "Re-mint with `gmail-blade-mcp auth --scopes write`."
            ) from e
        if "invalid_grant" in lower:
            raise AuthError(f"Refresh token expired or revoked. {_REMEDIATION}") from e
        if "invalid_client" in lower:
            raise AuthError("OAuth client id/secret rejected by Google. Check the configured client.") from e
        raise AuthError(f"Token refresh failed: {message}") from e


def _save_token(creds: Any) -> None:
    """Write token.json with 0600 perms set at create time (no chmod window)."""
    os.makedirs(CREDENTIALS_DIR, mode=0o700, exist_ok=True)
    fd = os.open(_token_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


def get_credentials(scopes: list[str]) -> Any:
    """Resolve Gmail credentials without ever prompting.

    Note: loading a wider-grant token.json with narrowed ``scopes`` and
    re-saving records the narrowed list in the file; the refresh token's
    actual grant is unaffected.
    """
    creds = _env_credentials(scopes)
    if creds is not None:
        # token=None — always mint a fresh access token; nothing persists.
        _refresh(creds)
        return creds

    creds = _token_file_credentials(scopes)
    if creds is not None:
        if not creds.valid:
            if not creds.refresh_token:
                raise AuthError(f"Saved token has no refresh token. {_REMEDIATION}")
            _refresh(creds)
            _save_token(creds)
        return creds

    raise AuthError(f"No Gmail credentials found. {_REMEDIATION}")


def get_gmail_service(readonly: bool = True) -> Any:
    """Build an authenticated Gmail API service object (never interactive).

    Args:
        readonly: If True, request only read scopes. If False, request full
            modify+send scopes (needed for write operations). Scopes are always
            passed on refresh: narrowing to readonly is least-privilege for
            free; a write request against a readonly grant fails fast with
            ScopeError instead of a confusing 403 mid-tool-call.
    """
    from googleapiclient.discovery import build

    scopes = SCOPES_READONLY if readonly else SCOPES_MODIFY
    return build("gmail", "v1", credentials=get_credentials(scopes))


# ---------------------------------------------------------------------------
# Interactive OAuth consent — CLI only (`gmail-blade-mcp auth`)
# ---------------------------------------------------------------------------


def run_interactive_flow(
    scopes: list[str],
    client_id: str | None = None,
    client_secret: str | None = None,
    save_token: bool = True,
) -> Any:
    """Run the browser consent flow and return minted credentials.

    Client config precedence: explicit args → ``GMAIL_OAUTH_CLIENT_ID``/
    ``GMAIL_OAUTH_CLIENT_SECRET`` env → ``~/.gmail-blade/credentials.json``.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_id = client_id or _env(ENV_CLIENT_ID)
    client_secret = client_secret or _env(ENV_CLIENT_SECRET)

    if client_id and client_secret:
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": GOOGLE_TOKEN_URI,
                    "redirect_uris": ["http://localhost"],
                }
            },
            scopes,
        )
    elif os.path.exists(_client_secrets_path()):
        flow = InstalledAppFlow.from_client_secrets_file(_client_secrets_path(), scopes)
    else:
        raise AuthError(
            "No OAuth client configured. Pass --client-id/--client-secret, set "
            f"{ENV_CLIENT_ID}/{ENV_CLIENT_SECRET}, or download the OAuth client JSON from "
            "Google Cloud Console → APIs & Services → Credentials and save it as "
            f"{_client_secrets_path()}."
        )

    creds = flow.run_local_server(port=0)
    if save_token:
        _save_token(creds)
    return creds


# ---------------------------------------------------------------------------
# MCP HTTP bearer auth
# ---------------------------------------------------------------------------

_BEARER_TOKEN: str | None = None
_BEARER_CHECKED: bool = False


def get_bearer_token() -> str | None:
    """Return the bearer token from env, or None if not configured."""
    global _BEARER_TOKEN, _BEARER_CHECKED  # noqa: PLW0603
    if _BEARER_CHECKED:
        return _BEARER_TOKEN
    _BEARER_CHECKED = True
    token = os.environ.get("GMAIL_MCP_API_TOKEN", "").strip()
    _BEARER_TOKEN = token if token else None
    return _BEARER_TOKEN


class BearerAuthMiddleware:
    """Starlette-compatible ASGI middleware for Bearer token auth.

    When ``GMAIL_MCP_API_TOKEN`` is set, every request must carry a matching
    ``Authorization: Bearer <token>`` header. Requests without a valid token
    receive a ``401 Unauthorized`` JSON response.

    If the env var is unset or empty, this middleware is a transparent pass-through.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        expected = get_bearer_token()
        if expected is None:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_value = headers.get(b"authorization", b"").decode("latin-1")

        provided = ""
        if auth_value.lower().startswith("bearer "):
            provided = auth_value[7:]

        if provided and secrets.compare_digest(provided, expected):
            await self.app(scope, receive, send)
            return

        body = json.dumps({"error": "Unauthorized"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
