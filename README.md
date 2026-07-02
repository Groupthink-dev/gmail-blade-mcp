# Gmail Blade MCP

Production Gmail MCP server for AI agents. Token-efficient, write-safe, thread-intelligent.

## Features

| Category | Tools | Description |
|----------|-------|-------------|
| **Read** | `gmail_search`, `gmail_read`, `gmail_snippets`, `gmail_thread`, `gmail_mailboxes` | Search, read messages and threads, list labels |
| **Meta** | `gmail_info`, `gmail_state`, `gmail_changes`, `gmail_identities`, `gmail_filters` | Account info, incremental sync, send-as aliases, filter rules |
| **Write** | `gmail_send`, `gmail_reply`, `gmail_draft`, `gmail_flag`, `gmail_move`, `gmail_bulk`, `gmail_delete` | Send, reply, draft, label, move, batch ops, delete |
| **Filter** | `gmail_filter_create`, `gmail_filter_delete` | Create and delete Gmail filters |
| **AI** | `gmail_classify`, `gmail_summarise` | Gemini-powered classification and summarisation (requires `GOOGLE_API_KEY`) |

### What makes this different

- **Token-efficient by default** — HTML→plaintext stripping, body truncation, quoted-reply deduplication, concise list format
- **Write-safe** — `GMAIL_WRITE_ENABLED=true` env gate + `confirm=true` on destructive operations
- **Thread-intelligent** — `thread_mode=deduped` strips quoted replies; `latest` shows only newest message
- **Incremental sync** — `gmail_state` + `gmail_changes` via Gmail `history.list` API
- **Rate-limit aware** — automatic exponential backoff on 429 errors
- **Gemini AI** — classify and summarise emails using Google Gemini (optional, requires `GOOGLE_API_KEY`)
- **Credential-safe** — OAuth tokens scrubbed from error messages

## Quick Start

### 1. Install

```bash
uv sync
```

### 2. Set up Gmail API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select existing)
3. Enable the Gmail API
4. Create OAuth 2.0 credentials (Desktop application)
5. On the OAuth consent screen, set publishing status to **"In production"** —
   in "Testing" status Google expires refresh tokens after 7 days, which breaks
   any long-running server. (The "unverified app" warning at consent is a
   one-time click-through; if you have a Google Workspace org, marking the app
   **Internal** avoids the warning entirely.)
6. Download the JSON file and save as `~/.gmail-blade/credentials.json`
   (or pass `--client-id`/`--client-secret` to the auth verb instead)

### 3. Authenticate

```bash
uv run gmail-blade-mcp auth            # mints modify+send scopes (default)
uv run gmail-blade-mcp auth --scopes read   # read-only grant
```

A browser window opens for OAuth consent. After authorising, the verb prints a
paste-ready credential block:

```
GMAIL_OAUTH_CLIENT_ID=...
GMAIL_OAUTH_CLIENT_SECRET=...
GMAIL_OAUTH_REFRESH_TOKEN=...
```

and (unless `--no-save-token`) also saves the refresh token to
`~/.gmail-blade/token.json` (mode 0600) for standalone use.

**The server itself never opens a browser.** If no credentials are available it
returns a typed error telling you to run `gmail-blade-mcp auth`. Verify current
credentials any time with:

```bash
uv run gmail-blade-mcp auth --check    # prints account email + granted scopes
```

**Stallari / headless deployments:** set the three `GMAIL_OAUTH_*` env vars
(e.g. paste them into Stallari → Connections → gmail-blade-mcp). When
`GMAIL_OAUTH_REFRESH_TOKEN` is set the server builds credentials in memory and
never touches `~/.gmail-blade/`. Note the scope grant is fixed when the token
is minted — to enable write tools later you need both `GMAIL_WRITE_ENABLED=true`
*and* a token minted with `--scopes write`.

### 4. Configure MCP client

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "gmail-blade": {
      "command": "uv",
      "args": ["--directory", "/path/to/gmail-blade-mcp", "run", "gmail-blade-mcp"],
      "env": {
        "GMAIL_WRITE_ENABLED": "false"
      }
    }
  }
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GMAIL_OAUTH_CLIENT_ID` | _(none)_ | OAuth client ID (in-memory credential mode) |
| `GMAIL_OAUTH_CLIENT_SECRET` | _(none)_ | OAuth client secret |
| `GMAIL_OAUTH_REFRESH_TOKEN` | _(none)_ | OAuth refresh token — presence switches on in-memory credential mode (mint with `gmail-blade-mcp auth`) |
| `GMAIL_WRITE_ENABLED` | `false` | Enable write operations (send, reply, delete, etc.) |
| `GMAIL_MCP_TRANSPORT` | `stdio` | Transport: `stdio` or `http` |
| `GMAIL_MCP_HOST` | `127.0.0.1` | HTTP host (when transport=http) |
| `GMAIL_MCP_PORT` | `8768` | HTTP port (when transport=http) |
| `GMAIL_MCP_API_TOKEN` | _(none)_ | Bearer token for HTTP transport auth |
| `GOOGLE_API_KEY` | _(none)_ | Google AI Studio API key for Gemini classify/summarise tools |

## Security

- **Write operations disabled by default** — set `GMAIL_WRITE_ENABLED=true` to enable
- **Permanent delete requires `confirm=true`** — use `gmail_move` to TRASH for soft delete
- **OAuth tokens never appear in error messages** — regex scrubbing on all error paths
- **The server can never open a browser** — interactive consent exists only in the `auth` CLI verb; headless deployments get typed errors, not hangs
- **`token.json` written 0600** — and in env-credential mode nothing is written to disk at all
- **Bearer auth uses constant-time comparison** — `secrets.compare_digest()`
- **Credentials stored locally** — `~/.gmail-blade/`, never transmitted

## Development

```bash
make install-dev    # Install with dev + test deps
make test           # Unit tests (no Gmail needed)
make check          # Lint + format + type check
make test-cov       # Tests with coverage
```

## email-v1 Contract

This server implements the Sidereal `email-v1` domain contract — the same tool semantics as `fastmail-blade-mcp`. Skills targeting `email-v1` work with either provider.

## License

MIT
