"""Token-efficient output formatters for Gmail data.

Design principles:
- Concise by default (one line per item)
- Null fields omitted
- Lists capped and annotated with total count
- HTML stripped to plaintext
- Quoted replies deduplicated in thread views
"""

from __future__ import annotations

import email.utils
from typing import Any

from gmail_blade_mcp.client import strip_html, strip_quoted_reply
from gmail_blade_mcp.models import DEFAULT_LIMIT, MAX_BODY_CHARS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_header(message: dict[str, Any], name: str) -> str:
    """Extract a header value from a Gmail message dict."""
    for header in message.get("payload", {}).get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return str(header.get("value", ""))
    return ""


def _get_body_text(message: dict[str, Any]) -> str:
    """Extract body text from a Gmail message, preferring plain text over HTML."""
    payload = message.get("payload", {})

    # Simple single-part message
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        import base64

        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Multipart — walk parts
    parts = payload.get("parts", [])
    plain_text = ""
    html_text = ""

    for part in parts:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if not data:
            # Check nested parts (multipart/alternative inside multipart/mixed)
            for sub in part.get("parts", []):
                sub_mime = sub.get("mimeType", "")
                sub_data = sub.get("body", {}).get("data", "")
                if sub_data:
                    import base64

                    decoded = base64.urlsafe_b64decode(sub_data).decode("utf-8", errors="replace")
                    if sub_mime == "text/plain" and not plain_text:
                        plain_text = decoded
                    elif sub_mime == "text/html" and not html_text:
                        html_text = decoded
            continue

        import base64

        decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if mime == "text/plain" and not plain_text:
            plain_text = decoded
        elif mime == "text/html" and not html_text:
            html_text = decoded

    if plain_text:
        return plain_text
    if html_text:
        return strip_html(html_text)
    return ""


def _format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _truncate_body(text: str, max_chars: int) -> str:
    """Truncate body text at a clean boundary (paragraph or sentence)."""
    if len(text) <= max_chars:
        return text

    # Try to break at paragraph
    cut = text[:max_chars]
    last_para = cut.rfind("\n\n")
    if last_para > max_chars * 0.5:
        return cut[:last_para] + f"\n\n… [truncated, {len(text)} chars total]"

    # Try to break at sentence
    last_period = cut.rfind(". ")
    if last_period > max_chars * 0.5:
        return cut[: last_period + 1] + f"\n\n… [truncated, {len(text)} chars total]"

    # Hard cut
    return cut + f"\n\n… [truncated, {len(text)} chars total]"


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------


def format_message_list(messages: list[dict[str, Any]], total: int | None = None, limit: int = DEFAULT_LIMIT) -> str:
    """Format message list: date | sender | subject | size | labels.

    Example::

        2026-03-07 14:30 | alice@example.com | Meeting notes | 4.2 KB | INBOX
        2026-03-06 09:15 | bob@example.com | Re: Project update | 1.1 KB | INBOX, STARRED
        … 48 more (use limit= to see more)
    """
    if not messages:
        return "No messages found."

    actual_total = total if total is not None else len(messages)
    shown = messages[:limit]
    lines: list[str] = []

    for msg in shown:
        parts: list[str] = []

        # Date
        date_str = _get_header(msg, "Date")
        if date_str:
            parsed = email.utils.parsedate_to_datetime(date_str)
            parts.append(parsed.strftime("%Y-%m-%d %H:%M"))
        else:
            ts = msg.get("internalDate")
            if ts:
                from datetime import UTC, datetime

                parts.append(datetime.fromtimestamp(int(ts) / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M"))
            else:
                parts.append("?")

        # Sender
        from_header = _get_header(msg, "From")
        if from_header:
            _name, addr = email.utils.parseaddr(from_header)
            parts.append(addr or from_header)
        else:
            parts.append("?")

        # Subject
        subject = _get_header(msg, "Subject") or msg.get("snippet", "")[:60]
        parts.append(subject)

        # Size
        size = msg.get("sizeEstimate")
        if size:
            parts.append(_format_size(size))

        # Labels
        label_ids = msg.get("labelIds", [])
        if label_ids:
            parts.append(", ".join(label_ids))

        # Message ID (for reference)
        parts.append(f"id={msg['id']}")

        lines.append(" | ".join(parts))

    if actual_total > limit:
        lines.append(f"… {actual_total - limit} more (use limit= to see more)")

    return "\n".join(lines)


def format_snippets(messages: list[dict[str, Any]], total: int | None = None, limit: int = DEFAULT_LIMIT) -> str:
    """Format message snippets: date | sender | subject | preview.

    Token-efficient preview format — one line per message with Gmail's
    built-in snippet (first ~100 chars of body).
    """
    if not messages:
        return "No messages found."

    actual_total = total if total is not None else len(messages)
    shown = messages[:limit]
    lines: list[str] = []

    for msg in shown:
        parts: list[str] = []

        # Date (compact)
        date_str = _get_header(msg, "Date")
        if date_str:
            parsed = email.utils.parsedate_to_datetime(date_str)
            parts.append(parsed.strftime("%m-%d"))
        else:
            parts.append("?")

        # Sender (short)
        from_header = _get_header(msg, "From")
        if from_header:
            name, addr = email.utils.parseaddr(from_header)
            parts.append(name or addr or "?")
        else:
            parts.append("?")

        # Subject
        parts.append(_get_header(msg, "Subject") or "(no subject)")

        # Snippet preview
        snippet = msg.get("snippet", "")
        if snippet:
            parts.append(snippet[:120])

        parts.append(f"id={msg['id']}")
        lines.append(" | ".join(parts))

    if actual_total > limit:
        lines.append(f"… {actual_total - limit} more")

    return "\n".join(lines)


def format_message_body(
    message: dict[str, Any],
    body_mode: str = "stripped",
    max_body_chars: int = MAX_BODY_CHARS,
) -> str:
    """Format a single message for full reading.

    Args:
        message: Gmail message dict (full format).
        body_mode: 'full' (raw text), 'stripped' (plaintext, truncated),
            'snippet' (first ~200 chars), 'none' (metadata only).
        max_body_chars: Maximum body characters for stripped mode.

    Returns:
        Formatted message string.
    """
    parts: list[str] = []

    # Header block
    parts.append(f"From: {_get_header(message, 'From')}")
    to = _get_header(message, "To")
    if to:
        parts.append(f"To: {to}")
    cc = _get_header(message, "Cc")
    if cc:
        parts.append(f"Cc: {cc}")
    parts.append(f"Date: {_get_header(message, 'Date')}")
    parts.append(f"Subject: {_get_header(message, 'Subject')}")

    label_ids = message.get("labelIds", [])
    if label_ids:
        parts.append(f"Labels: {', '.join(label_ids)}")

    size = message.get("sizeEstimate")
    if size:
        parts.append(f"Size: {_format_size(size)}")

    parts.append(f"ID: {message['id']}")
    parts.append(f"Thread: {message.get('threadId', '?')}")

    # Attachments
    payload = message.get("payload", {})
    attachments = []
    for part in payload.get("parts", []):
        filename = part.get("filename")
        if filename:
            att_size = part.get("body", {}).get("size", 0)
            attachments.append(f"  {filename} ({_format_size(att_size)})")
    if attachments:
        parts.append("Attachments:")
        parts.extend(attachments)

    # Body
    if body_mode == "none":
        pass
    elif body_mode == "snippet":
        snippet = message.get("snippet", "")
        if snippet:
            parts.append("")
            parts.append(snippet[:200])
    elif body_mode == "full":
        body = _get_body_text(message)
        if body:
            parts.append("")
            parts.append(body)
    else:  # stripped (default)
        body = _get_body_text(message)
        if body:
            body = _truncate_body(body, max_body_chars)
            parts.append("")
            parts.append(body)

    return "\n".join(parts)


def format_thread(
    thread: dict[str, Any],
    thread_mode: str = "deduped",
    max_body_chars: int = MAX_BODY_CHARS,
) -> str:
    """Format a thread with all its messages.

    Args:
        thread: Gmail thread dict with 'messages' list.
        thread_mode: 'deduped' (strip quoted replies), 'latest' (newest only),
            'full' (all content verbatim).
        max_body_chars: Maximum body characters per message.

    Returns:
        Formatted thread string.
    """
    messages = thread.get("messages", [])
    if not messages:
        return "Empty thread."

    subject = _get_header(messages[0], "Subject") if messages else "?"
    parts: list[str] = [
        f"Thread: {thread.get('id', '?')}",
        f"Subject: {subject}",
        f"Messages: {len(messages)}",
        "",
    ]

    if thread_mode == "latest":
        # Only show the most recent message
        msg = messages[-1]
        parts.append(format_message_body(msg, body_mode="stripped", max_body_chars=max_body_chars))
    else:
        for i, msg in enumerate(messages):
            if i > 0:
                parts.append("---")

            from_header = _get_header(msg, "From")
            date_header = _get_header(msg, "Date")
            parts.append(f"[{i + 1}/{len(messages)}] {from_header} — {date_header}")

            body = _get_body_text(msg)
            if body:
                if thread_mode == "deduped":
                    body = strip_quoted_reply(body)
                body = _truncate_body(body, max_body_chars)
                parts.append(body)
            parts.append("")

    return "\n".join(parts)


def format_label_list(labels: list[dict[str, Any]]) -> str:
    """Format label list: name (total/unread) [type].

    Example::

        INBOX (1234/56) [system] id=INBOX
        SENT (890/0) [system] id=SENT
        Projects (45/3) [user] id=Label_123
    """
    if not labels:
        return "No labels found."

    lines: list[str] = []
    for label in sorted(labels, key=lambda lb: lb.get("name", "")):
        name = label.get("name", "?")
        total = label.get("messagesTotal")
        unread = label.get("messagesUnread")
        label_type = label.get("type", "")
        label_id = label.get("id", "")

        parts = [name]
        if total is not None:
            parts.append(f"({total}/{unread or 0})")
        if label_type:
            parts.append(f"[{label_type}]")
        if label_id:
            parts.append(f"id={label_id}")
        lines.append(" ".join(parts))

    return "\n".join(lines)


def format_profile(profile: dict[str, Any]) -> str:
    """Format account profile info."""
    lines = [
        f"Email: {profile.get('emailAddress', '?')}",
        f"Messages: {profile.get('messagesTotal', '?')}",
        f"Threads: {profile.get('threadsTotal', '?')}",
        f"History ID: {profile.get('historyId', '?')}",
    ]
    return "\n".join(lines)


def format_changes(history: dict[str, Any]) -> str:
    """Format incremental change history.

    Summarises additions, deletions, and label changes since a given history ID.
    """
    records = history.get("history", [])
    new_id = history.get("historyId", "?")

    if not records:
        return f"No changes since last sync.\nCurrent history ID: {new_id}"

    added = 0
    deleted = 0
    label_changes = 0

    for record in records:
        added += len(record.get("messagesAdded", []))
        deleted += len(record.get("messagesDeleted", []))
        label_changes += len(record.get("labelsAdded", []))
        label_changes += len(record.get("labelsRemoved", []))

    lines = [
        f"Changes since last sync ({len(records)} history records):",
        f"  Messages added: {added}",
        f"  Messages deleted: {deleted}",
        f"  Label changes: {label_changes}",
        f"Current history ID: {new_id}",
    ]
    return "\n".join(lines)


def format_filter_list(filters: list[dict[str, Any]]) -> str:
    """Format filter list concisely."""
    if not filters:
        return "No filters configured."

    lines: list[str] = []
    for f in filters:
        criteria = f.get("criteria", {})
        action = f.get("action", {})
        filter_id = f.get("id", "?")

        # Build criteria description
        crit_parts: list[str] = []
        if criteria.get("from"):
            crit_parts.append(f"from:{criteria['from']}")
        if criteria.get("to"):
            crit_parts.append(f"to:{criteria['to']}")
        if criteria.get("subject"):
            crit_parts.append(f"subject:{criteria['subject']}")
        if criteria.get("query"):
            crit_parts.append(f"query:{criteria['query']}")

        # Build action description
        act_parts: list[str] = []
        if action.get("addLabelIds"):
            act_parts.append(f"+labels:{','.join(action['addLabelIds'])}")
        if action.get("removeLabelIds"):
            act_parts.append(f"-labels:{','.join(action['removeLabelIds'])}")
        if action.get("forward"):
            act_parts.append(f"fwd:{action['forward']}")

        criteria_str = " ".join(crit_parts) if crit_parts else "(any)"
        action_str = " ".join(act_parts) if act_parts else "(none)"

        lines.append(f"{criteria_str} → {action_str} | id={filter_id}")

    return "\n".join(lines)


def format_send_as_list(identities: list[dict[str, Any]]) -> str:
    """Format send-as aliases."""
    if not identities:
        return "No send-as identities configured."

    lines: list[str] = []
    for identity in identities:
        addr = identity.get("sendAsEmail", "?")
        name = identity.get("displayName", "")
        is_default = identity.get("isDefault", False)
        is_primary = identity.get("isPrimary", False)

        parts = [addr]
        if name:
            parts.append(f'"{name}"')
        if is_primary:
            parts.append("[primary]")
        elif is_default:
            parts.append("[default]")

        lines.append(" ".join(parts))

    return "\n".join(lines)
