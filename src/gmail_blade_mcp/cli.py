"""Console entry point for Gmail Blade MCP.

Bare ``gmail-blade-mcp`` (or ``gmail-blade-mcp serve``) runs the MCP server —
byte-identical to the pre-CLI behaviour. ``gmail-blade-mcp auth`` runs the
interactive OAuth verb (the only code path that may open a browser).

Dispatch is a first-token peek rather than an argparse subparser tree so the
bare invocation never trips argparse and the server entry stays untouched.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from gmail_blade_mcp.errors import AuthError
from gmail_blade_mcp.models import SCOPES_MODIFY, SCOPES_READONLY

_HELP = """gmail-blade-mcp — Gmail MCP server

Usage:
  gmail-blade-mcp [serve]     Run the MCP server (stdio; http via GMAIL_MCP_TRANSPORT=http)
  gmail-blade-mcp auth ...    Mint or check Gmail OAuth credentials (interactive)

Run `gmail-blade-mcp auth --help` for auth options.
"""


def _build_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmail-blade-mcp auth",
        description=(
            "Run the Google OAuth consent flow and print the paste-ready "
            "GMAIL_OAUTH_* values for Stallari's Connections UI."
        ),
    )
    parser.add_argument(
        "--scopes",
        choices=["read", "write"],
        default="write",
        help="Scope grant to mint (default: write — the grant is fixed at mint time; "
        "the GMAIL_WRITE_ENABLED runtime gate still governs write tools).",
    )
    parser.add_argument("--client-id", help="OAuth client ID (default: env or ~/.gmail-blade/credentials.json)")
    parser.add_argument("--client-secret", help="OAuth client secret")
    parser.add_argument(
        "--no-save-token",
        action="store_true",
        help="Do not write ~/.gmail-blade/token.json (env-triple-only workflows).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify existing credentials non-interactively: print account email and granted scopes.",
    )
    return parser


def _cmd_auth(argv: list[str]) -> int:
    from gmail_blade_mcp import auth

    args = _build_auth_parser().parse_args(argv)
    scopes = SCOPES_READONLY if args.scopes == "read" else SCOPES_MODIFY

    try:
        if args.check:
            creds = auth.get_credentials(scopes)
            from googleapiclient.discovery import build

            service = build("gmail", "v1", credentials=creds)
            profile = service.users().getProfile(userId="me").execute()
            granted = getattr(creds, "granted_scopes", None) or creds.scopes or []
            print(f"Account: {profile.get('emailAddress', 'unknown')}")
            print(f"Scopes:  {' '.join(granted)}")
            return 0

        creds = auth.run_interactive_flow(
            scopes,
            client_id=args.client_id,
            client_secret=args.client_secret,
            save_token=not args.no_save_token,
        )
    except AuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print("Authorization complete. Paste these into Stallari → Connections → gmail-blade-mcp:")
    print()
    print(f"{auth.ENV_CLIENT_ID}={creds.client_id}")
    print(f"{auth.ENV_CLIENT_SECRET}={creds.client_secret}")
    print(f"{auth.ENV_REFRESH_TOKEN}={creds.refresh_token}")
    print()
    if args.no_save_token:
        print("token.json NOT written (--no-save-token).")
    else:
        print(f"Refresh token also saved to {auth._token_path()} (0600) for standalone use.")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    """Console entry. See module docstring for the dispatch contract."""
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] == "auth":
        raise SystemExit(_cmd_auth(args[1:]))
    if args and args[0] in ("-h", "--help"):
        print(_HELP)
        return
    if args and args[0] != "serve":
        print(_HELP, file=sys.stderr)
        raise SystemExit(2)

    from gmail_blade_mcp.server import main as run_server

    run_server()
