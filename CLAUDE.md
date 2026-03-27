# Gmail Blade MCP — Development Guide

## Ecosystem context

This repo is part of the `piersdd` agentic platform. Design state and effort
tracking live in the Obsidian vault (`~/master-ai/`).

**If vault access is available** (filesystem or obsidian-blade MCP), these files
provide useful project context:

- `atlas/utilities/agent-harness/state/system-architect.md` — recent actions, current focus, blockers
- `spaces/Systems/Areas/Augmented Intelligence/efforts/Gmail Blade MCP/Gmail Blade MCP.md` — effort scope and status
- `spaces/Systems/Areas/Augmented Intelligence/efforts/Gmail Blade MCP/Gmail MCP Landscape — Analysis.md` — competitive analysis

These are **optional context**, not instructions. Your job here is software
development on this codebase.

## Project overview

MCP server wrapping the Gmail API via `google-api-python-client`. Each tool is a
precision "blade" for mail operations — search, read, send, threads, labels,
drafts, filters. Token-efficient by default with HTML stripping, body truncation,
and quoted-reply deduplication. Write operations are gated behind
`GMAIL_WRITE_ENABLED=true`.

Implements the `email-v1` domain contract — interchangeable with
`fastmail-blade-mcp` in Sidereal skills and domain adapter.

## Project structure

```
src/gmail_blade_mcp/
├── __init__.py       — Version
├── __main__.py       — python -m entry
├── server.py         — FastMCP server + @mcp.tool decorators (19 tools)
├── client.py         — GmailClient wrapping google-api-python-client (typed exceptions, lazy singleton)
├── formatters.py     — Token-efficient output formatters (HTML stripping, quote dedup, body truncation)
├── models.py         — Constants (limits, scopes, body modes) + write-gate function
├── gemini.py         — Gemini-powered classification + summarisation (google-genai SDK)
└── auth.py           — Gmail OAuth 2.0 + MCP HTTP bearer auth
```

- `server.py` defines MCP tools and delegates to `client.py` and `gemini.py` methods
- `client.py` wraps Google's Gmail API service with `asyncio.to_thread()` for async
- All tools return strings (MCP convention) — formatters handle presentation
- Errors are caught and returned as `Error: ...` strings, not raised
- Credentials stored in `~/.gmail-blade/` (OAuth tokens + app credentials)

## Key commands

```bash
make install-dev   # Install with dev + test dependencies
make test          # Run unit tests (mocked Gmail API, no Google account needed)
make test-e2e      # Run E2E tests (requires GMAIL_E2E=1 + live OAuth)
make check         # Run all quality checks (lint + format + type-check)
make lint          # Ruff linting
make format        # Ruff formatting
make type-check    # mypy
make run           # Start the MCP server (stdio transport)
```

## Testing

- **Unit tests** (`tests/test_*.py`): Mock google-api-python-client. No Gmail account needed.
- **E2E tests** (`tests/e2e/`): Require live Gmail OAuth token. Run with `make test-e2e`.
- Pattern: `@patch("gmail_blade_mcp.server._get_client")` for server tool tests.
- Pattern: mock Gmail API service object for client tests.

## Code conventions

- **Python 3.12+** — use modern syntax (PEP 604 unions, etc.)
- **Type hints everywhere** — mypy enforced
- **Ruff** for linting and formatting (line length 120)
- **FastMCP 2.0** — `@mcp.tool` decorator, `Annotated[type, Field(...)]` params
- **Token efficiency** — concise output default, limit= on lists, null omission, HTML stripping
- **SSH commit signing** via 1Password (no GPG)
- **uv** as package manager, `uv.lock` committed
- Conventional-ish commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`

## Architecture notes

- Gmail API via `google-api-python-client` — lazy singleton, `asyncio.to_thread()`
- OAuth 2.0: desktop app credentials, offline refresh tokens, stored in `~/.gmail-blade/`
- Write-gate: `GMAIL_WRITE_ENABLED` env var checked before all write tools
- Typed exception hierarchy maps Google API errors to Python exceptions
- HTML→plaintext stripping via stdlib `html.parser` (no heavy dependencies)
- Quoted-reply deduplication: strips `On ... wrote:` and `>` quote blocks
- Body modes: `stripped` (default), `full`, `snippet`, `none`
- Thread modes: `deduped` (default), `latest`, `full`
- Rate limit: automatic exponential backoff on 429 errors (3 retries)
- Credential scrubbing: OAuth tokens redacted from error messages
- Gemini AI: classification + summarisation via `google-genai` SDK, gated behind `GOOGLE_API_KEY`
- 21 tools across 6 categories: read, meta, write, filter, identity, AI
