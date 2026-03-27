"""Authentication for Gmail Blade MCP.

Two auth layers:

1. **Gmail OAuth 2.0** — Desktop app credentials with offline refresh tokens.
   Credentials stored in ``~/.gmail-blade/``. First run opens browser for consent.

2. **MCP HTTP bearer auth** — Optional bearer token for remote/tunnel access.
   Set ``GMAIL_MCP_API_TOKEN`` env var. If unset, bearer auth is disabled
   (localhost-only setups work without configuration).
"""

from __future__ import annotations

import json
import logging
import os
import secrets

from starlette.types import ASGIApp, Receive, Scope, Send

from gmail_blade_mcp.models import CREDENTIALS_DIR, SCOPES_MODIFY, SCOPES_READONLY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gmail OAuth 2.0
# ---------------------------------------------------------------------------


def get_gmail_service(readonly: bool = True) -> object:
    """Build and return an authenticated Gmail API service object.

    On first run, opens a browser for OAuth consent and saves the refresh token
    to ``~/.gmail-blade/token.json``. Subsequent runs use the saved token.

    Args:
        readonly: If True, request only read scopes. If False, request full
            modify+send scopes (needed for write operations).

    Returns:
        A ``googleapiclient.discovery.Resource`` for the Gmail API v1.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = SCOPES_READONLY if readonly else SCOPES_MODIFY
    token_path = os.path.join(CREDENTIALS_DIR, "token.json")
    creds_path = os.path.join(CREDENTIALS_DIR, "credentials.json")

    creds: Credentials | None = None

    # Load existing token
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"OAuth credentials not found at {creds_path}. "
                    "Download from Google Cloud Console → APIs & Services → Credentials → "
                    "OAuth 2.0 Client IDs → Download JSON → save as ~/.gmail-blade/credentials.json"
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
            creds = flow.run_local_server(port=0)

        # Save token for next run
        os.makedirs(CREDENTIALS_DIR, exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


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
