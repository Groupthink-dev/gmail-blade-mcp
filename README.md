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
5. Download the JSON file
6. Save as `~/.gmail-blade/credentials.json`

### 3. First run (authenticate)

```bash
uv run gmail-blade-mcp
```

A browser window opens for OAuth consent. After authorising, the refresh token is saved to `~/.gmail-blade/token.json`.

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
