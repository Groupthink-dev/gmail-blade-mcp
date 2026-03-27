"""Gmail Blade MCP Server — search, read, send, threads, labels, drafts, filters.

Wraps the Gmail API via ``google-api-python-client`` as MCP tools. Token-efficient
by default: concise output, capped lists, null-field omission, HTML stripping,
quoted-reply deduplication.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from gmail_blade_mcp.client import GmailClient, GmailError
from gmail_blade_mcp.formatters import (
    format_changes,
    format_filter_list,
    format_label_list,
    format_message_body,
    format_message_list,
    format_profile,
    format_send_as_list,
    format_snippets,
    format_thread,
)
from gmail_blade_mcp.models import DEFAULT_LIMIT, MAX_BATCH_SIZE, MAX_BODY_CHARS, require_write

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transport configuration
# ---------------------------------------------------------------------------

TRANSPORT = os.environ.get("GMAIL_MCP_TRANSPORT", "stdio")
HTTP_HOST = os.environ.get("GMAIL_MCP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("GMAIL_MCP_PORT", "8768"))

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "GmailBlade",
    instructions=(
        "Gmail email operations via Google API. Read, search, send, and manage email. "
        "Thread-intelligent with quoted-reply deduplication. Token-efficient by default. "
        "Write operations require GMAIL_WRITE_ENABLED=true."
    ),
)

# Lazy-initialized client
_client: GmailClient | None = None


def _get_client() -> GmailClient:
    """Get or create the GmailClient singleton."""
    global _client  # noqa: PLW0603
    if _client is None:
        _client = GmailClient()
        logger.info("GmailClient: account=%s", _client.email_address)
    return _client


def _error_response(e: GmailError) -> str:
    """Format a client error as a user-friendly string."""
    return f"Error: {e}"


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking client method in a thread to avoid blocking the event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ===========================================================================
# READ TOOLS
# ===========================================================================


@mcp.tool()
async def gmail_search(
    query: Annotated[str, Field(description="Gmail search query (same syntax as Gmail search bar)")] = "",
    label: Annotated[str, Field(description="Filter by label ID (e.g. INBOX, SENT, Label_123)")] = "",
    limit: Annotated[int, Field(description="Max messages to return (default 20, max 500)")] = DEFAULT_LIMIT,
    include_details: Annotated[
        bool, Field(description="Include full message details (5x token cost)")
    ] = False,
) -> str:
    """Search Gmail messages. Returns concise one-line-per-message format.

    Uses Gmail search syntax: ``from:alice subject:meeting after:2026/03/01``
    """
    try:
        label_ids = [label] if label else None
        messages, total = await _run(
            _get_client().search_messages,
            query=query,
            label_ids=label_ids,
            limit=limit,
            include_details=include_details,
        )
        return format_message_list(messages, total=total, limit=limit)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_read(
    message_id: Annotated[str, Field(description="Gmail message ID")],
    body_mode: Annotated[
        str,
        Field(description="Body format: 'stripped' (plaintext, truncated — default), 'full', 'snippet', 'none'"),
    ] = "stripped",
    max_body_chars: Annotated[
        int, Field(description="Max body characters for stripped mode (default 4000)")
    ] = MAX_BODY_CHARS,
) -> str:
    """Read a single email message with headers and body.

    Body modes control token usage:
    - ``stripped``: HTML→plaintext, truncated at paragraph boundary (default)
    - ``full``: Complete body text (may be very large)
    - ``snippet``: First ~200 chars only
    - ``none``: Headers and metadata only
    """
    try:
        message = await _run(_get_client().get_message, message_id)
        return format_message_body(message, body_mode=body_mode, max_body_chars=max_body_chars)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_snippets(
    query: Annotated[str, Field(description="Gmail search query")] = "",
    label: Annotated[str, Field(description="Filter by label ID")] = "",
    limit: Annotated[int, Field(description="Max messages (default 20)")] = DEFAULT_LIMIT,
) -> str:
    """Token-efficient message previews. One line per message with date, sender, subject, and snippet.

    Lower token cost than ``gmail_search`` — use this for scanning/triage.
    """
    try:
        label_ids = [label] if label else None
        messages, total = await _run(
            _get_client().search_messages,
            query=query,
            label_ids=label_ids,
            limit=limit,
            include_details=False,
        )
        return format_snippets(messages, total=total, limit=limit)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_thread(
    thread_id: Annotated[str, Field(description="Gmail thread ID")],
    thread_mode: Annotated[
        str,
        Field(
            description=(
                "Thread display mode: 'deduped' (strip quoted replies — default), "
                "'latest' (newest message only), 'full' (all content verbatim)"
            )
        ),
    ] = "deduped",
    max_body_chars: Annotated[
        int, Field(description="Max body characters per message (default 4000)")
    ] = MAX_BODY_CHARS,
) -> str:
    """Read a full email thread with all messages.

    Thread modes control token usage:
    - ``deduped``: Strips quoted reply content (``On ... wrote:`` blocks) — recommended
    - ``latest``: Only the most recent message in the thread
    - ``full``: All messages with complete content (high token cost for long threads)
    """
    try:
        thread = await _run(_get_client().get_thread, thread_id)
        return format_thread(thread, thread_mode=thread_mode, max_body_chars=max_body_chars)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_mailboxes() -> str:
    """List all Gmail labels (folders) with message counts.

    Returns: name (total/unread) [type] id=LABEL_ID
    """
    try:
        labels = await _run(_get_client().list_labels)
        return format_label_list(labels)
    except GmailError as e:
        return _error_response(e)


# ===========================================================================
# META TOOLS
# ===========================================================================


@mcp.tool()
async def gmail_info() -> str:
    """Account info: email address, message count, thread count, current history ID."""
    try:
        profile = await _run(_get_client().get_profile)
        return format_profile(profile)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_state() -> str:
    """Get current history ID for incremental sync.

    Save this value and pass it to ``gmail_changes`` later to see only
    what changed since this point.
    """
    try:
        profile = await _run(_get_client().get_profile)
        return f"History ID: {profile.get('historyId', '?')}\nUse this with gmail_changes to track incremental updates."
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_changes(
    history_id: Annotated[str, Field(description="History ID from a previous gmail_state call")],
    label: Annotated[str, Field(description="Filter changes to a specific label")] = "",
) -> str:
    """Incremental sync — show what changed since a given history ID.

    Returns counts of messages added, deleted, and label changes.
    Use ``gmail_state`` first to get a history ID baseline.
    """
    try:
        label_id = label if label else None
        history = await _run(_get_client().get_history, history_id, label_id=label_id)
        return format_changes(history)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_identities() -> str:
    """List send-as aliases (email identities) configured on this account."""
    try:
        identities = await _run(_get_client().list_send_as)
        return format_send_as_list(identities)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_filters() -> str:
    """List all Gmail filters with criteria and actions."""
    try:
        filters = await _run(_get_client().list_filters)
        return format_filter_list(filters)
    except GmailError as e:
        return _error_response(e)


# ===========================================================================
# WRITE TOOLS (gated behind GMAIL_WRITE_ENABLED=true)
# ===========================================================================


@mcp.tool()
async def gmail_send(
    to: Annotated[str, Field(description="Recipient email address(es), comma-separated")],
    subject: Annotated[str, Field(description="Email subject")],
    body: Annotated[str, Field(description="Plain text email body")],
    cc: Annotated[str, Field(description="CC recipients, comma-separated")] = "",
    bcc: Annotated[str, Field(description="BCC recipients, comma-separated")] = "",
) -> str:
    """Send an email. Requires GMAIL_WRITE_ENABLED=true."""
    if err := require_write():
        return err
    try:
        result = await _run(_get_client().send_message, to, subject, body, cc=cc, bcc=bcc)
        return f"Sent. Message ID: {result.get('id', '?')}, Thread: {result.get('threadId', '?')}"
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_reply(
    message_id: Annotated[str, Field(description="ID of the message to reply to")],
    body: Annotated[str, Field(description="Plain text reply body")],
    reply_all: Annotated[bool, Field(description="Reply to all recipients (default: sender only)")] = False,
) -> str:
    """Reply to an email message. Requires GMAIL_WRITE_ENABLED=true."""
    if err := require_write():
        return err
    try:
        result = await _run(_get_client().reply_to_message, message_id, body, reply_all=reply_all)
        return f"Reply sent. Message ID: {result.get('id', '?')}, Thread: {result.get('threadId', '?')}"
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_draft(
    to: Annotated[str, Field(description="Recipient email address(es)")],
    subject: Annotated[str, Field(description="Email subject")],
    body: Annotated[str, Field(description="Plain text email body")],
    cc: Annotated[str, Field(description="CC recipients")] = "",
    bcc: Annotated[str, Field(description="BCC recipients")] = "",
) -> str:
    """Create a draft email (saved, not sent). Requires GMAIL_WRITE_ENABLED=true."""
    if err := require_write():
        return err
    try:
        result = await _run(_get_client().create_draft, to, subject, body, cc=cc, bcc=bcc)
        draft_id = result.get("id", "?")
        msg_id = result.get("message", {}).get("id", "?")
        return f"Draft created. Draft ID: {draft_id}, Message ID: {msg_id}"
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_flag(
    message_id: Annotated[str, Field(description="Message ID to modify")],
    add_labels: Annotated[str, Field(description="Label IDs to add, comma-separated (e.g. STARRED, IMPORTANT)")] = "",
    remove_labels: Annotated[
        str, Field(description="Label IDs to remove, comma-separated (e.g. UNREAD, INBOX)")
    ] = "",
) -> str:
    """Add or remove labels on a message (star, mark read, etc.). Requires GMAIL_WRITE_ENABLED=true."""
    if err := require_write():
        return err
    try:
        add = [l.strip() for l in add_labels.split(",") if l.strip()] if add_labels else None
        remove = [l.strip() for l in remove_labels.split(",") if l.strip()] if remove_labels else None
        if not add and not remove:
            return "Error: Provide at least one of add_labels or remove_labels."
        await _run(_get_client().modify_message, message_id, add_labels=add, remove_labels=remove)
        parts = []
        if add:
            parts.append(f"+{','.join(add)}")
        if remove:
            parts.append(f"-{','.join(remove)}")
        return f"Labels updated: {' '.join(parts)} on {message_id}"
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_move(
    message_id: Annotated[str, Field(description="Message ID to move")],
    to_label: Annotated[str, Field(description="Destination label ID (e.g. Label_123, TRASH)")],
    from_label: Annotated[str, Field(description="Source label ID to remove (e.g. INBOX)")] = "INBOX",
) -> str:
    """Move a message between labels/folders. Requires GMAIL_WRITE_ENABLED=true."""
    if err := require_write():
        return err
    try:
        await _run(
            _get_client().modify_message,
            message_id,
            add_labels=[to_label],
            remove_labels=[from_label] if from_label else None,
        )
        return f"Moved {message_id}: {from_label} → {to_label}"
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_bulk(
    message_ids: Annotated[str, Field(description="Comma-separated message IDs (max 50)")],
    action: Annotated[
        str,
        Field(
            description=(
                "Action: 'archive' (remove INBOX), 'read' (remove UNREAD), "
                "'unread' (add UNREAD), 'star' (add STARRED), 'unstar' (remove STARRED), "
                "'trash' (add TRASH), 'label' (add label — specify add_labels)"
            )
        ),
    ],
    add_labels: Annotated[str, Field(description="Label IDs to add (for 'label' action)")] = "",
    remove_labels: Annotated[str, Field(description="Label IDs to remove")] = "",
) -> str:
    """Batch operation on multiple messages. Requires GMAIL_WRITE_ENABLED=true."""
    if err := require_write():
        return err

    ids = [i.strip() for i in message_ids.split(",") if i.strip()]
    if not ids:
        return "Error: No message IDs provided."
    if len(ids) > MAX_BATCH_SIZE:
        return f"Error: Batch size {len(ids)} exceeds maximum {MAX_BATCH_SIZE}."

    # Map action to label changes
    add: list[str] | None = None
    remove: list[str] | None = None

    match action:
        case "archive":
            remove = ["INBOX"]
        case "read":
            remove = ["UNREAD"]
        case "unread":
            add = ["UNREAD"]
        case "star":
            add = ["STARRED"]
        case "unstar":
            remove = ["STARRED"]
        case "trash":
            add = ["TRASH"]
        case "label":
            add = [l.strip() for l in add_labels.split(",") if l.strip()] if add_labels else None
            remove = [l.strip() for l in remove_labels.split(",") if l.strip()] if remove_labels else None
            if not add and not remove:
                return "Error: 'label' action requires add_labels or remove_labels."
        case _:
            return f"Error: Unknown action '{action}'. Use: archive, read, unread, star, unstar, trash, label."

    try:
        result = await _run(_get_client().batch_modify, ids, add_labels=add, remove_labels=remove)
        return result
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_delete(
    message_id: Annotated[str, Field(description="Message ID to delete")],
    confirm: Annotated[
        bool,
        Field(description="Must be true to confirm permanent deletion. Use gmail_move to TRASH instead for soft delete."),
    ] = False,
) -> str:
    """Permanently delete a message. CANNOT BE UNDONE. Requires GMAIL_WRITE_ENABLED=true and confirm=true.

    For soft delete, use ``gmail_move`` with ``to_label=TRASH`` instead.
    """
    if err := require_write():
        return err
    if not confirm:
        return "Error: Permanent deletion requires confirm=true. Use gmail_move to TRASH for soft delete."
    try:
        await _run(_get_client().delete_message, message_id)
        return f"Permanently deleted message {message_id}."
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_filter_create(
    from_address: Annotated[str, Field(description="Match sender email address")] = "",
    to_address: Annotated[str, Field(description="Match recipient email address")] = "",
    subject: Annotated[str, Field(description="Match subject text")] = "",
    query: Annotated[str, Field(description="Gmail search query to match")] = "",
    add_labels: Annotated[str, Field(description="Label IDs to add, comma-separated")] = "",
    remove_labels: Annotated[str, Field(description="Label IDs to remove, comma-separated")] = "",
    forward_to: Annotated[str, Field(description="Email address to forward matching messages")] = "",
) -> str:
    """Create a Gmail filter. Requires GMAIL_WRITE_ENABLED=true."""
    if err := require_write():
        return err

    criteria: dict[str, Any] = {}
    if from_address:
        criteria["from"] = from_address
    if to_address:
        criteria["to"] = to_address
    if subject:
        criteria["subject"] = subject
    if query:
        criteria["query"] = query

    if not criteria:
        return "Error: At least one criteria (from_address, to_address, subject, query) is required."

    action: dict[str, Any] = {}
    if add_labels:
        action["addLabelIds"] = [l.strip() for l in add_labels.split(",") if l.strip()]
    if remove_labels:
        action["removeLabelIds"] = [l.strip() for l in remove_labels.split(",") if l.strip()]
    if forward_to:
        action["forward"] = forward_to

    if not action:
        return "Error: At least one action (add_labels, remove_labels, forward_to) is required."

    try:
        result = await _run(_get_client().create_filter, criteria, action)
        return f"Filter created. ID: {result.get('id', '?')}"
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_filter_delete(
    filter_id: Annotated[str, Field(description="Filter ID to delete")],
    confirm: Annotated[bool, Field(description="Must be true to confirm deletion")] = False,
) -> str:
    """Delete a Gmail filter. Requires GMAIL_WRITE_ENABLED=true and confirm=true."""
    if err := require_write():
        return err
    if not confirm:
        return "Error: Filter deletion requires confirm=true."
    try:
        await _run(_get_client().delete_filter, filter_id)
        return f"Filter {filter_id} deleted."
    except GmailError as e:
        return _error_response(e)


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    """Run the MCP server."""
    if TRANSPORT == "http":
        from gmail_blade_mcp.auth import BearerAuthMiddleware

        mcp.settings.host = HTTP_HOST
        mcp.settings.port = HTTP_PORT
        mcp.run(transport="sse", middleware=[BearerAuthMiddleware])
    else:
        mcp.run(transport="stdio")
