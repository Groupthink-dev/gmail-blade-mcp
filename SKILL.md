# Gmail Blade MCP — Skill Guide

## Token Efficiency Rules (MANDATORY)

1. **Use `gmail_snippets` for scanning** — lower token cost than `gmail_search`. Use for triage, overview, and quick checks
2. **Use `body_mode=stripped`** (default) — HTML is converted to plaintext and truncated at paragraph boundaries. Only use `full` when you need exact formatting
3. **Use `gmail_thread` with `thread_mode=deduped`** (default) — strips quoted replies. Use `latest` for just the newest message
4. **Use `gmail_changes` for incremental sync** — call `gmail_state` once, then `gmail_changes` to see only what's new. Never re-scan the entire inbox
5. **Use `gmail_bulk` for batch operations** — one call for up to 50 messages instead of individual `gmail_flag`/`gmail_move` calls
6. **Use `limit=` to control output** — default is 20. Reduce for tighter context, increase only when needed

## Quick Start — 7 Most Common Operations

### 1. Check recent inbox
```
gmail_snippets(label="INBOX", limit=10)
```

### 2. Search for specific emails
```
gmail_search(query="from:alice@example.com subject:project after:2026/03/01")
```

### 3. Read a message
```
gmail_read(message_id="abc123")
```

### 4. Read a conversation thread
```
gmail_thread(thread_id="xyz789")
```

### 5. See what changed since last check
```
gmail_state()  → save history_id
gmail_changes(history_id="12345")
```

### 6. Archive messages in bulk
```
gmail_bulk(message_ids="id1,id2,id3", action="archive")
```

### 7. Send an email
```
gmail_send(to="alice@example.com", subject="Meeting tomorrow", body="Hi Alice, ...")
```

## Workflow Examples

### Email triage
1. `gmail_snippets(label="INBOX", limit=30)` — scan inbox
2. `gmail_read(message_id="...")` — read important ones
3. `gmail_bulk(message_ids="...", action="archive")` — archive processed
4. `gmail_bulk(message_ids="...", action="read")` — mark as read

### Thread investigation
1. `gmail_search(query="subject:quarterly report")` — find the thread
2. `gmail_thread(thread_id="...", thread_mode="deduped")` — read without quoted bloat
3. `gmail_reply(message_id="...", body="...")` — reply to latest

### Draft-first workflow (safe)
1. `gmail_draft(to="...", subject="...", body="...")` — create draft
2. Human reviews draft in Gmail UI
3. Human sends manually

## Gmail Search Syntax

| Query | Matches |
|-------|---------|
| `from:alice@example.com` | From specific sender |
| `to:bob@example.com` | To specific recipient |
| `subject:meeting` | Subject contains "meeting" |
| `has:attachment` | Has attachments |
| `is:unread` | Unread messages |
| `is:starred` | Starred messages |
| `after:2026/03/01` | After date |
| `before:2026/03/15` | Before date |
| `larger:5M` | Larger than 5MB |
| `label:work` | Has label "work" |
| `in:anywhere` | Search all mail (not just inbox) |
| `{from:a OR from:b}` | Multiple senders |
