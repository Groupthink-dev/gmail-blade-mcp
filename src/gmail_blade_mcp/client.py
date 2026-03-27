"""Gmail API client wrapper.

Wraps ``googleapiclient`` Gmail service with typed exceptions, pattern-based
error classification, and convenience methods for each tool category. All
methods are synchronous — the server wraps them with ``asyncio.to_thread()``.
"""

from __future__ import annotations

import base64
import email.utils
import logging
import os
import re
import time
from email.mime.text import MIMEText
from typing import Any

from gmail_blade_mcp.auth import get_gmail_service
from gmail_blade_mcp.models import (
    DEFAULT_LIMIT,
    FORMAT_FULL,
    FORMAT_METADATA,
    MAX_BATCH_SIZE,
    METADATA_HEADERS,
    is_write_enabled,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GmailError(Exception):
    """Base exception for Gmail client errors."""

    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(message)
        self.details = details


class AuthError(GmailError):
    """Authentication failed — invalid or expired OAuth token."""


class NotFoundError(GmailError):
    """Requested resource (message, thread, label) not found."""


class RateLimitError(GmailError):
    """Rate limit exceeded — back off and retry."""


class QuotaError(GmailError):
    """Daily quota exceeded."""


class ConnectionError(GmailError):  # noqa: A001
    """Cannot connect to Gmail API."""


class WriteDisabledError(GmailError):
    """Write operation attempted but GMAIL_WRITE_ENABLED is not true."""


class InvalidRequestError(GmailError):
    """Bad request — invalid query, missing fields, etc."""


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_ERROR_PATTERNS: list[tuple[str, type[GmailError]]] = [
    ("unauthorized", AuthError),
    ("invalid credentials", AuthError),
    ("token has been expired", AuthError),
    ("login required", AuthError),
    ("forbidden", AuthError),
    ("not found", NotFoundError),
    ("does not exist", NotFoundError),
    ("no such", NotFoundError),
    ("rate limit", RateLimitError),
    ("too many requests", RateLimitError),
    ("user rate limit", RateLimitError),
    ("quota", QuotaError),
    ("daily limit", QuotaError),
    ("connection", ConnectionError),
    ("timeout", ConnectionError),
    ("unreachable", ConnectionError),
    ("bad request", InvalidRequestError),
    ("invalid", InvalidRequestError),
]

# Regex to scrub OAuth tokens from error messages
_TOKEN_PATTERN = re.compile(r"(ya29\.[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9_.-]+)", re.IGNORECASE)


def _classify_error(message: str) -> GmailError:
    """Map error message to a typed exception."""
    lower = message.lower()
    for pattern, exc_cls in _ERROR_PATTERNS:
        if pattern in lower:
            return exc_cls(_scrub_credentials(message))
    return GmailError(_scrub_credentials(message))


def _scrub_credentials(message: str) -> str:
    """Remove OAuth tokens from error messages."""
    return _TOKEN_PATTERN.sub("[REDACTED]", message)


# ---------------------------------------------------------------------------
# HTML → plaintext
# ---------------------------------------------------------------------------


def strip_html(html: str) -> str:
    """Convert HTML to plaintext. Strips tags, decodes entities, normalises whitespace."""
    from html.parser import HTMLParser
    from io import StringIO

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._out = StringIO()
            self._skip = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in ("script", "style"):
                self._skip = True
            elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
                self._out.write("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style"):
                self._skip = False
            elif tag in ("p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
                self._out.write("\n")

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self._out.write(data)

    stripper = _Stripper()
    stripper.feed(html)
    text = stripper.get_data()

    # Normalise whitespace: collapse runs of blank lines, strip trailing spaces
    lines = [line.rstrip() for line in text.splitlines()]
    # Collapse 3+ consecutive blank lines to 2
    result: list[str] = []
    blank_count = 0
    for line in lines:
        if line == "":
            blank_count += 1
            if blank_count <= 2:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)

    return "\n".join(result).strip()


# ---------------------------------------------------------------------------
# Quoted-reply detection
# ---------------------------------------------------------------------------

# Patterns that start a quoted reply
_QUOTE_PATTERNS = [
    re.compile(r"^On .+ wrote:$", re.MULTILINE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^From:\s+.+@.+", re.MULTILINE),
]


def strip_quoted_reply(text: str) -> str:
    """Remove quoted reply content from an email body.

    Detects ``On ... wrote:`` and ``--- Original Message ---`` patterns,
    returning only the new content above the quote marker.
    """
    for pattern in _QUOTE_PATTERNS:
        match = pattern.search(text)
        if match:
            before = text[: match.start()].rstrip()
            if before:
                return before

    # Fall back: strip lines starting with >
    lines = text.splitlines()
    new_lines: list[str] = []
    hit_quote = False
    for line in lines:
        if line.startswith(">"):
            hit_quote = True
            continue
        if hit_quote and line.strip() == "":
            continue
        if hit_quote:
            # Content after a quote block — keep it (interleaved replies)
            hit_quote = False
        new_lines.append(line)

    return "\n".join(new_lines).rstrip()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GmailClient:
    """Typed wrapper around the Gmail API service."""

    def __init__(self) -> None:
        readonly = not is_write_enabled()
        self._service = get_gmail_service(readonly=readonly)
        self._user = "me"

        # Cache profile on init
        profile = self._service.users().getProfile(userId=self._user).execute()  # type: ignore[union-attr]
        self.email_address: str = profile.get("emailAddress", "")
        self.messages_total: int = profile.get("messagesTotal", 0)
        self.threads_total: int = profile.get("threadsTotal", 0)
        self.history_id: str = profile.get("historyId", "")

    # -- Search / List -------------------------------------------------------

    def search_messages(
        self,
        query: str = "",
        label_ids: list[str] | None = None,
        limit: int = DEFAULT_LIMIT,
        include_details: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search messages. Returns (messages, estimated_total).

        Args:
            query: Gmail search query (same syntax as Gmail search bar).
            label_ids: Filter by label IDs.
            limit: Max messages to return.
            include_details: If True, fetch full message data. If False, metadata only.

        Returns:
            Tuple of (message list, estimated total count).
        """
        kwargs: dict[str, Any] = {"userId": self._user, "maxResults": min(limit, 500)}
        if query:
            kwargs["q"] = query
        if label_ids:
            kwargs["labelIds"] = label_ids

        result = self._execute(self._service.users().messages().list(**kwargs))  # type: ignore[union-attr]
        messages_refs = result.get("messages", [])
        total = result.get("resultSizeEstimate", len(messages_refs))

        if not messages_refs:
            return [], 0

        # Fetch message details
        fmt = FORMAT_FULL if include_details else FORMAT_METADATA
        messages = []
        for ref in messages_refs[:limit]:
            msg = self.get_message(ref["id"], fmt=fmt)
            if msg:
                messages.append(msg)

        return messages, total

    def get_message(self, message_id: str, fmt: str = FORMAT_FULL) -> dict[str, Any]:
        """Get a single message by ID.

        Args:
            message_id: The message ID.
            fmt: Format — 'full', 'metadata', or 'minimal'.

        Returns:
            Message dict from Gmail API.
        """
        kwargs: dict[str, Any] = {"userId": self._user, "id": message_id, "format": fmt}
        if fmt == FORMAT_METADATA:
            kwargs["metadataHeaders"] = METADATA_HEADERS
        return self._execute(self._service.users().messages().get(**kwargs))  # type: ignore[union-attr]

    def get_thread(self, thread_id: str, fmt: str = FORMAT_FULL) -> dict[str, Any]:
        """Get a thread with all its messages.

        Args:
            thread_id: The thread ID.
            fmt: Format for messages within the thread.

        Returns:
            Thread dict with 'messages' list.
        """
        kwargs: dict[str, Any] = {"userId": self._user, "id": thread_id, "format": fmt}
        if fmt == FORMAT_METADATA:
            kwargs["metadataHeaders"] = METADATA_HEADERS
        return self._execute(self._service.users().threads().get(**kwargs))  # type: ignore[union-attr]

    # -- Labels --------------------------------------------------------------

    def list_labels(self) -> list[dict[str, Any]]:
        """List all labels (Gmail's equivalent of mailboxes/folders)."""
        result = self._execute(self._service.users().labels().list(userId=self._user))  # type: ignore[union-attr]
        labels = result.get("labels", [])

        # Fetch details for each label (total/unread counts)
        detailed: list[dict[str, Any]] = []
        for label in labels:
            try:
                detail = self._execute(
                    self._service.users().labels().get(userId=self._user, id=label["id"])  # type: ignore[union-attr]
                )
                detailed.append(detail)
            except GmailError:
                detailed.append(label)

        return detailed

    # -- Profile / State -----------------------------------------------------

    def get_profile(self) -> dict[str, Any]:
        """Get account profile (email, message count, history ID)."""
        return self._execute(self._service.users().getProfile(userId=self._user))  # type: ignore[union-attr]

    def get_history(self, start_history_id: str, label_id: str | None = None) -> dict[str, Any]:
        """Get message history since a given history ID (incremental sync).

        Args:
            start_history_id: History ID from a previous ``get_profile()`` or ``gmail_state`` call.
            label_id: Optional label to filter history events.

        Returns:
            History dict with 'history' list and 'historyId' (new watermark).
        """
        kwargs: dict[str, Any] = {
            "userId": self._user,
            "startHistoryId": start_history_id,
        }
        if label_id:
            kwargs["labelId"] = label_id

        return self._execute(self._service.users().history().list(**kwargs))  # type: ignore[union-attr]

    # -- Send / Reply / Draft ------------------------------------------------

    def send_message(self, to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict[str, Any]:
        """Send an email message.

        Args:
            to: Recipient email address(es), comma-separated.
            subject: Email subject.
            body: Plain text body.
            cc: CC recipients, comma-separated.
            bcc: BCC recipients, comma-separated.

        Returns:
            Sent message dict.
        """
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if bcc:
            message["bcc"] = bcc

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return self._execute(
            self._service.users().messages().send(userId=self._user, body={"raw": raw})  # type: ignore[union-attr]
        )

    def reply_to_message(
        self, message_id: str, body: str, reply_all: bool = False
    ) -> dict[str, Any]:
        """Reply to an existing message.

        Args:
            message_id: ID of the message to reply to.
            body: Plain text reply body.
            reply_all: If True, reply to all recipients.

        Returns:
            Sent reply message dict.
        """
        original = self.get_message(message_id, fmt=FORMAT_METADATA)
        headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}

        reply_to = headers.get("Reply-To", headers.get("From", ""))
        subject = headers.get("Subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        to = reply_to
        cc = ""
        if reply_all:
            all_recipients = set()
            for field in ("To", "Cc"):
                if field in headers:
                    for addr in headers[field].split(","):
                        parsed = email.utils.parseaddr(addr.strip())
                        if parsed[1] and parsed[1].lower() != self.email_address.lower():
                            all_recipients.add(parsed[1])
            # Remove the original sender from CC (they're in To)
            sender_addr = email.utils.parseaddr(reply_to)[1]
            all_recipients.discard(sender_addr.lower())
            cc = ", ".join(all_recipients)

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        message["In-Reply-To"] = headers.get("Message-ID", "")
        message["References"] = headers.get("References", headers.get("Message-ID", ""))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return self._execute(
            self._service.users().messages().send(  # type: ignore[union-attr]
                userId=self._user,
                body={"raw": raw, "threadId": original.get("threadId", "")},
            )
        )

    def create_draft(self, to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict[str, Any]:
        """Create a draft message.

        Args:
            to: Recipient email address(es).
            subject: Email subject.
            body: Plain text body.
            cc: CC recipients.
            bcc: BCC recipients.

        Returns:
            Draft dict with 'id' and 'message'.
        """
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if bcc:
            message["bcc"] = bcc

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return self._execute(
            self._service.users().drafts().create(  # type: ignore[union-attr]
                userId=self._user, body={"message": {"raw": raw}}
            )
        )

    # -- Modify / Move / Delete ----------------------------------------------

    def modify_message(
        self,
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Modify message labels (star, archive, move, etc.).

        Args:
            message_id: Message ID to modify.
            add_labels: Label IDs to add.
            remove_labels: Label IDs to remove.

        Returns:
            Modified message dict.
        """
        body: dict[str, Any] = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        return self._execute(
            self._service.users().messages().modify(userId=self._user, id=message_id, body=body)  # type: ignore[union-attr]
        )

    def batch_modify(
        self,
        message_ids: list[str],
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> str:
        """Batch modify labels on multiple messages.

        Args:
            message_ids: List of message IDs (max MAX_BATCH_SIZE).
            add_labels: Label IDs to add to all messages.
            remove_labels: Label IDs to remove from all messages.

        Returns:
            Success status string.
        """
        if len(message_ids) > MAX_BATCH_SIZE:
            raise InvalidRequestError(f"Batch size {len(message_ids)} exceeds maximum {MAX_BATCH_SIZE}")

        body: dict[str, Any] = {"ids": message_ids}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        self._execute(
            self._service.users().messages().batchModify(userId=self._user, body=body)  # type: ignore[union-attr]
        )
        return f"Modified {len(message_ids)} messages"

    def trash_message(self, message_id: str) -> dict[str, Any]:
        """Move a message to trash."""
        return self._execute(
            self._service.users().messages().trash(userId=self._user, id=message_id)  # type: ignore[union-attr]
        )

    def delete_message(self, message_id: str) -> None:
        """Permanently delete a message. Cannot be undone."""
        self._execute(
            self._service.users().messages().delete(userId=self._user, id=message_id)  # type: ignore[union-attr]
        )

    # -- Filters -------------------------------------------------------------

    def list_filters(self) -> list[dict[str, Any]]:
        """List all Gmail filters."""
        result = self._execute(
            self._service.users().settings().filters().list(userId=self._user)  # type: ignore[union-attr]
        )
        return result.get("filter", [])

    def create_filter(self, criteria: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        """Create a new Gmail filter.

        Args:
            criteria: Filter match criteria (from, to, subject, query, etc.).
            action: Actions to take (addLabelIds, removeLabelIds, forward, etc.).

        Returns:
            Created filter dict.
        """
        body = {"criteria": criteria, "action": action}
        return self._execute(
            self._service.users().settings().filters().create(userId=self._user, body=body)  # type: ignore[union-attr]
        )

    def delete_filter(self, filter_id: str) -> None:
        """Delete a Gmail filter."""
        self._execute(
            self._service.users().settings().filters().delete(userId=self._user, id=filter_id)  # type: ignore[union-attr]
        )

    # -- Identities (send-as) -----------------------------------------------

    def list_send_as(self) -> list[dict[str, Any]]:
        """List send-as aliases (identities)."""
        result = self._execute(
            self._service.users().settings().sendAs().list(userId=self._user)  # type: ignore[union-attr]
        )
        return result.get("sendAs", [])

    # -- Internal helpers ----------------------------------------------------

    def _execute(self, request: Any) -> Any:
        """Execute a Gmail API request with error handling and rate limit backoff."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return request.execute()
            except Exception as e:
                error_msg = str(e)
                classified = _classify_error(error_msg)

                if isinstance(classified, RateLimitError) and attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Rate limited, retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                    time.sleep(wait)
                    continue

                raise classified from e

        raise GmailError("Max retries exceeded")  # pragma: no cover
