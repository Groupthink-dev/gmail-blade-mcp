"""Gmail Blade MCP Server — search, read, send, threads, labels, drafts, filters.

Wraps the Gmail API via ``google-api-python-client`` as MCP tools. Token-efficient
by default: concise output, capped lists, null-field omission, HTML stripping,
quoted-reply deduplication.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from gmail_blade_mcp.client import GmailClient, GmailError, InvalidRequestError
from gmail_blade_mcp.domain_hint import Pattern, compute_domain_hint, load_patterns_from_yaml
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
from gmail_blade_mcp.gemini import get_gemini_client, require_gemini
from gmail_blade_mcp.models import DEFAULT_LIMIT, MAX_BATCH_SIZE, MAX_BODY_CHARS, require_write

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DD-338 Phase A.1 — Scope-tag wrapper + Track 3 _meta envelope
# ---------------------------------------------------------------------------

# [[DD-278]] scope vocabulary → Gmail q= clause defaults.
# Each entry: (env-var name, default q= fragment). public is a no-op pass-through.
_SCOPE_ENV_DEFAULTS: dict[str, tuple[str | None, str | None]] = {
    "work": ("GMAIL_WORK_LABEL", "category:work"),
    "personal": ("GMAIL_PERSONAL_LABEL", "category:personal"),
    "family": ("GMAIL_FAMILY_LABEL", "label:Family"),
    "public": (None, None),  # explicit no-op — DD-278 public means "no scope restriction"
}

_VALID_SCOPES: frozenset[str | None] = frozenset({"work", "personal", "family", "public", None})


def _resolve_scope_expr(scope: str) -> str | None:
    """Resolve a scope value to its Gmail q= fragment via env-var override or default.

    Returns None when the scope is a no-op (e.g. ``public``).
    """
    env_var, default = _SCOPE_ENV_DEFAULTS[scope]
    if env_var is None:
        return None
    # Empty-string env var also collapses to default — defends against unset/empty drift
    return os.environ.get(env_var) or default


def _compose_scoped_query(query: str, scope: str | None) -> tuple[str, list[str]]:
    """Compose user query + DD-278 scope tag into effective Gmail q= clause.

    Args:
        query: User-supplied Gmail search query (verbatim ``q=`` syntax).
        scope: DD-278 scope tag — one of ``work``/``personal``/``family``/``public``
            or ``None``. ``None`` and ``public`` are no-ops (return query unchanged
            and an empty filters_applied list).

    Returns:
        ``(effective_query, filters_applied)`` where ``filters_applied`` is the
        ordered list of scope tags applied (for the ``_meta.filtered_by`` envelope).

    Raises:
        InvalidRequestError: When ``scope`` is not in the DD-278 vocabulary.
    """
    if scope not in _VALID_SCOPES:
        raise InvalidRequestError(f"Unknown scope {scope!r}. Valid: work, personal, family, public, None.")

    if scope is None:
        # Backwards-compatible no-op: user query passes through verbatim
        return query, []

    scope_expr = _resolve_scope_expr(scope)
    if scope_expr is None:
        # public — DD-278 "no scope restriction"; pass through unchanged
        # but record the scope tag so the envelope shows the dispatcher's intent
        return query, [f"scope={scope}"]

    filters_applied = [f"scope={scope}"]
    if query:
        # Wrap both clauses in parens to defend against operator-precedence surprises
        effective = f"({query}) ({scope_expr})"
    else:
        effective = f"({scope_expr})"
    return effective, filters_applied


def _format_meta_envelope(
    matched_total: int,
    returned: int,
    filtered_by: list[str],
    latency_ms: int,
    redactions: list[str] | None = None,
    next_cursor: str | None = None,
    error_notes: list[str] | None = None,
    domain_hints: dict[str, str] | None = None,
) -> str:
    r"""Build the DD-338 Track 3 ``_meta`` envelope line.

    Wire shape (architect amendment 2026-05-21 — JSON tail block):

        _meta: {"matched_total": 42, "returned": 10, ...}

    Single JSON line. Callers prepend ``\n\n`` when appending to an existing
    payload (assembler regex ``\n\n_meta: (\{.*\})$``).

    DD-338 A.2.dom.c — when ``domain_hints`` is non-empty, an additional
    ``domain_hints: {record_id: domain}`` entry is emitted. Empty / None ⇒
    key omitted entirely (Convention #22 graceful degradation).
    """
    meta: dict[str, Any] = {
        "matched_total": matched_total,
        "returned": returned,
        "filtered_by": filtered_by,
        "latency_ms": latency_ms,
    }
    if redactions:
        meta["redactions"] = redactions
    if next_cursor is not None:
        meta["next_cursor"] = next_cursor
    if error_notes:
        meta["error_notes"] = error_notes
    if domain_hints:
        meta["domain_hints"] = domain_hints
    return "_meta: " + json.dumps(meta, separators=(", ", ": "))


def _append_meta(payload: str, meta_line: str) -> str:
    """Append a ``_meta`` envelope line to a tool payload using the canonical separator."""
    return f"{payload}\n\n{meta_line}"


# Gmail label-ID cache for env-var label-name resolution.
# Maps logical scope tag → resolved Gmail label ID (e.g. ``family`` → ``Label_99``).
# Populated lazily on first scope-verify call against a label-based scope.
_LABEL_ID_CACHE: dict[str, str | None] = {}


def _scope_label_name(scope: str) -> str | None:
    """Extract the Gmail label name from a scope's resolved expression, if any.

    For ``label:Family`` returns ``Family``; for ``category:work`` returns None
    (category operators map to system label IDs, not user labels).
    """
    expr = _resolve_scope_expr(scope)
    if expr is None:
        return None
    if expr.startswith("label:"):
        return expr[len("label:") :]
    return None


def _scope_category_id(scope: str) -> str | None:
    """Map a ``category:X`` scope expression to its Gmail system label ID.

    Gmail's category labels are documented constants:
    https://developers.google.com/gmail/api/guides/labels
    """
    expr = _resolve_scope_expr(scope)
    if expr is None or not expr.startswith("category:"):
        return None
    category = expr[len("category:") :].lower()
    # Gmail system label IDs are uppercased CATEGORY_<NAME>
    return f"CATEGORY_{category.upper()}"


def _resolve_label_id(label_name: str, client: GmailClient) -> str | None:
    """Look up a Gmail label ID by its human-readable name.

    Caches results for the process lifetime. Returns None if the label does not exist.
    """
    if label_name in _LABEL_ID_CACHE:
        return _LABEL_ID_CACHE[label_name]
    try:
        labels = client.list_labels()
    except GmailError:
        return None
    found: str | None = None
    for label in labels:
        if label.get("name") == label_name:
            found = label.get("id")
            break
    _LABEL_ID_CACHE[label_name] = found
    return found


def _message_label_ids(message: dict[str, Any]) -> list[str]:
    """Extract the ``labelIds`` array from a Gmail message dict (defensive empty default)."""
    ids = message.get("labelIds", [])
    return list(ids) if isinstance(ids, list) else []


def _thread_label_ids(thread: dict[str, Any]) -> list[str]:
    """Aggregate labelIds across all messages in a thread (union)."""
    seen: set[str] = set()
    for msg in thread.get("messages", []):
        for lid in _message_label_ids(msg):
            seen.add(lid)
    return sorted(seen)


def _scope_matches(scope: str, label_ids: list[str], client: GmailClient) -> bool:
    """Post-fetch scope verification — check if a record's labelIds satisfy the scope.

    For ``label:Foo`` scopes: requires the resolved label ID to be present.
    For ``category:work`` scopes: best-effort — checks the CATEGORY_<NAME> label ID
        is present. Gmail's auto-categorisation may not always set the explicit
        system label, so this is documented as best-effort in the spec.
    For ``public`` / no-op scopes: always matches (no restriction).

    Returns True when the scope was a no-op (None / public) or when the record's
    labelIds include the expected label.
    """
    if scope == "public":
        return True
    # Try label-name resolution first
    label_name = _scope_label_name(scope)
    if label_name is not None:
        resolved = _resolve_label_id(label_name, client)
        if resolved is None:
            return False
        return resolved in label_ids
    # Fall through to category-ID match
    category_id = _scope_category_id(scope)
    if category_id is not None:
        return category_id in label_ids
    # Unknown shape (env-var override could be anything) — accept the record
    # rather than burn the work item. Documented best-effort in spec §3.3.
    return True


# ---------------------------------------------------------------------------
# DD-338 A.2.dom.c — BladeConfigStore reader + Gmail field projector
# ---------------------------------------------------------------------------

_BLADE_ID = "gmail-blade-mcp"


def _state_root() -> str:
    """Resolve Stallari state root.

    Honours ``STALLARI_STATE_ROOT`` env var (used in tests + non-standard
    deployments); falls back to the macOS Application Support default per
    Convention #27 / StallariPaths.
    """
    override = os.environ.get("STALLARI_STATE_ROOT")
    if override:
        return override
    return os.path.expanduser("~/Library/Application Support/Stallari")


def _sanitize_blade_id(blade_id: str) -> str:
    """Mirror the Swift writer's blade-id directory naming.

    Lower-case + ``/`` ⇒ ``_`` — kept in lockstep with BladeConfigStore.swift
    (Convention #23: reader and writer agree on the on-disk shape).
    """
    return blade_id.lower().replace("/", "_")


def _load_blade_config(blade_id: str) -> list[Pattern]:
    """Read this blade's domain_hint patterns from the BladeConfigStore.

    Convention #22 graceful degradation: missing / unreadable / malformed
    config returns ``[]`` — the blade still runs, simply without per-record
    ``domain_hints`` emission.

    Convention #23 reader-side compliance: resolves via state-root +
    ``blade-config/<sanitized-blade>/config.yaml`` in lockstep with the
    Swift writer's path layout.
    """
    config_path = os.path.join(
        _state_root(),
        "blade-config",
        _sanitize_blade_id(blade_id),
        "config.yaml",
    )
    try:
        with open(config_path, encoding="utf-8") as f:
            yaml_str = f.read()
    except OSError:
        return []
    return load_patterns_from_yaml(yaml_str)


# Cached at module load; re-launch the blade to pick up config edits at v1.
_PATTERNS: list[Pattern] = _load_blade_config(_BLADE_ID)


def _gmail_field_projector(record: dict[str, Any], field: str) -> Any:
    """Project a Gmail message record onto a logical ``Pattern.field`` name.

    Gmail Messages API record shape::

        {
          "id": "...",
          "threadId": "...",
          "labelIds": [...],
          "payload": {"headers": [{"name": "From", "value": "..."}, ...]}
        }

    Supported field names: ``from``, ``to``, ``cc``, ``subject`` (extracted
    from ``payload.headers[*]`` by case-insensitive header-name match),
    ``labelIds`` (list returned directly), ``id`` and ``threadId`` (scalar
    string). Unknown field ⇒ ``None`` (no match).
    """
    if not isinstance(record, dict):
        return None
    f = field.lower()
    if f == "id":
        return record.get("id")
    if f == "threadid":
        return record.get("threadId")
    if f == "labelids":
        v = record.get("labelIds")
        return v if isinstance(v, list) else None
    if f in {"from", "to", "cc", "subject"}:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        headers = payload.get("headers")
        if not isinstance(headers, list):
            return None
        target = f
        values: list[str] = []
        for h in headers:
            if not isinstance(h, dict):
                continue
            name = h.get("name")
            if isinstance(name, str) and name.lower() == target:
                val = h.get("value")
                if isinstance(val, str):
                    values.append(val)
        if not values:
            return None
        # Single header common-case ⇒ scalar; multi-header (rare) ⇒ list
        return values[0] if len(values) == 1 else values
    return None


def _compute_domain_hints_for_records(records: list[dict[str, Any]]) -> dict[str, str]:
    """Apply ``_PATTERNS`` to each record; return ``{record_id: domain}`` map.

    Records lacking a domain match are omitted. Empty pattern list ⇒ empty
    dict ⇒ caller suppresses the ``domain_hints`` envelope key.
    """
    if not _PATTERNS:
        return {}
    out: dict[str, str] = {}
    for rec in records:
        rec_id = rec.get("id") if isinstance(rec, dict) else None
        if not isinstance(rec_id, str):
            continue
        hint = compute_domain_hint(rec, _PATTERNS, _gmail_field_projector)
        if hint is not None:
            out[rec_id] = hint
    return out


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
    scope: Annotated[
        str | None,
        Field(
            description=(
                "DD-278 scope tag: 'work', 'personal', 'family', 'public', or None (default unscoped). "
                "Maps to Gmail category:/label: clause via GMAIL_{WORK,PERSONAL,FAMILY}_LABEL env vars. "
                "Defaults: category:work / category:personal / label:Family. "
                "public is a no-op pass-through (no scope restriction). Server-side filter (DD-278 scope-tag wrapper)."
            )
        ),
    ] = None,
    limit: Annotated[int, Field(description="Max messages to return (default 20, max 500)")] = DEFAULT_LIMIT,
    include_details: Annotated[bool, Field(description="Include full message details (5x token cost)")] = False,
    include_meta: Annotated[
        bool,
        Field(
            description=(
                "Append structured _meta envelope (matched_total, returned, filtered_by, latency_ms) — "
                "DD-338 Track 3. Default False (backwards-compatible)."
            )
        ),
    ] = False,
) -> str:
    """Search Gmail messages. Returns concise one-line-per-message format.

    Uses Gmail search syntax: ``from:alice subject:meeting after:2026/03/01``.
    Layers DD-278 scope-tag wrapper via ``scope=`` argument — see argument docstring.
    """
    start = time.monotonic()
    try:
        effective_query, filters_applied = _compose_scoped_query(query, scope)
        if label:
            filters_applied.append(f"label={label}")
        if limit != DEFAULT_LIMIT:
            filters_applied.append(f"limit={limit}")
        label_ids = [label] if label else None
        messages, total = await _run(
            _get_client().search_messages,
            query=effective_query,
            label_ids=label_ids,
            limit=limit,
            include_details=include_details,
        )
        # DD-338 Phase B.1.b — stable sort: internalDate desc, id asc tie-break.
        messages = sorted(
            messages,
            key=lambda m: (-int(m.get("internalDate", "0") or "0"), m.get("id", "")),
        )
        payload = format_message_list(messages, total=total, limit=limit)
        if not include_meta:
            return payload
        latency_ms = int((time.monotonic() - start) * 1000)
        domain_hints = _compute_domain_hints_for_records(messages)
        meta = _format_meta_envelope(
            matched_total=total,
            returned=len(messages),
            filtered_by=filters_applied,
            latency_ms=latency_ms,
            domain_hints=domain_hints or None,
        )
        return _append_meta(payload, meta)
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
    scope: Annotated[
        str | None,
        Field(
            description=(
                "DD-278 scope tag: 'work', 'personal', 'family', 'public', or None. "
                "When set, the message is fetched then verified against the scope's labelIds; "
                "on mismatch returns empty payload + _meta.redactions=['scope_mismatch']. "
                "Server-side filter (DD-278 scope-tag wrapper) — post-fetch label verification."
            )
        ),
    ] = None,
    include_meta: Annotated[
        bool,
        Field(description="Append structured _meta envelope — DD-338 Track 3. Default False."),
    ] = False,
) -> str:
    """Read a single email message with headers and body.

    Body modes control token usage:
    - ``stripped``: HTML→plaintext, truncated at paragraph boundary (default)
    - ``full``: Complete body text (may be very large)
    - ``snippet``: First ~200 chars only
    - ``none``: Headers and metadata only

    Supports DD-278 scope-tag wrapper via ``scope=`` argument. Post-fetch
    verification — bytes are pulled before scope-verify; documented residual
    threat per DD-338 spec §10.
    """
    start = time.monotonic()
    try:
        # Validate scope early to surface InvalidRequestError before any API call
        if scope is not None and scope not in _VALID_SCOPES:
            raise InvalidRequestError(f"Unknown scope {scope!r}. Valid: work, personal, family, public, None.")
        client = _get_client()
        message = await _run(client.get_message, message_id)
        filters_applied: list[str] = []
        redactions: list[str] = []
        scope_mismatch = False
        if scope is not None:
            filters_applied.append(f"scope={scope}")
            label_ids = _message_label_ids(message)
            if not _scope_matches(scope, label_ids, client):
                scope_mismatch = True
                redactions.append("scope_mismatch")
        if scope_mismatch:
            payload = "(no messages matched scope filter)"
            matched_total = 0
            returned = 0
        else:
            payload = format_message_body(message, body_mode=body_mode, max_body_chars=max_body_chars)
            matched_total = 1
            returned = 1
        if not include_meta:
            return payload
        latency_ms = int((time.monotonic() - start) * 1000)
        domain_hints: dict[str, str] = {}
        if not scope_mismatch:
            domain_hints = _compute_domain_hints_for_records([message])
        meta = _format_meta_envelope(
            matched_total=matched_total,
            returned=returned,
            filtered_by=filters_applied,
            latency_ms=latency_ms,
            redactions=redactions or None,
            domain_hints=domain_hints or None,
        )
        return _append_meta(payload, meta)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_snippets(
    query: Annotated[str, Field(description="Gmail search query")] = "",
    label: Annotated[str, Field(description="Filter by label ID")] = "",
    scope: Annotated[
        str | None,
        Field(
            description=(
                "DD-278 scope tag: 'work', 'personal', 'family', 'public', or None (default unscoped). "
                "Maps to Gmail category:/label: clause via GMAIL_{WORK,PERSONAL,FAMILY}_LABEL env vars. "
                "Server-side filter (DD-278 scope-tag wrapper)."
            )
        ),
    ] = None,
    limit: Annotated[int, Field(description="Max messages (default 20)")] = DEFAULT_LIMIT,
    include_meta: Annotated[
        bool,
        Field(description="Append structured _meta envelope — DD-338 Track 3. Default False."),
    ] = False,
) -> str:
    """Token-efficient message previews. One line per message with date, sender, subject, and snippet.

    Lower token cost than ``gmail_search`` — use this for scanning/triage.
    Supports DD-278 scope-tag wrapper via ``scope=`` argument.
    """
    start = time.monotonic()
    try:
        effective_query, filters_applied = _compose_scoped_query(query, scope)
        if label:
            filters_applied.append(f"label={label}")
        if limit != DEFAULT_LIMIT:
            filters_applied.append(f"limit={limit}")
        label_ids = [label] if label else None
        messages, total = await _run(
            _get_client().search_messages,
            query=effective_query,
            label_ids=label_ids,
            limit=limit,
            include_details=False,
        )
        # DD-338 Phase B.1.b — stable sort: internalDate desc, id asc tie-break.
        messages = sorted(
            messages,
            key=lambda m: (-int(m.get("internalDate", "0") or "0"), m.get("id", "")),
        )
        payload = format_snippets(messages, total=total, limit=limit)
        if not include_meta:
            return payload
        latency_ms = int((time.monotonic() - start) * 1000)
        domain_hints = _compute_domain_hints_for_records(messages)
        meta = _format_meta_envelope(
            matched_total=total,
            returned=len(messages),
            filtered_by=filters_applied,
            latency_ms=latency_ms,
            domain_hints=domain_hints or None,
        )
        return _append_meta(payload, meta)
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
    scope: Annotated[
        str | None,
        Field(
            description=(
                "DD-278 scope tag: 'work', 'personal', 'family', 'public', or None. "
                "When set, the thread is fetched then verified against aggregated labelIds; "
                "on mismatch returns empty payload + _meta.redactions=['scope_mismatch']. "
                "Server-side filter (DD-278 scope-tag wrapper) — post-fetch label verification."
            )
        ),
    ] = None,
    include_meta: Annotated[
        bool,
        Field(description="Append structured _meta envelope — DD-338 Track 3. Default False."),
    ] = False,
) -> str:
    """Read a full email thread with all messages.

    Thread modes control token usage:
    - ``deduped``: Strips quoted reply content (``On ... wrote:`` blocks) — recommended
    - ``latest``: Only the most recent message in the thread
    - ``full``: All messages with complete content (high token cost for long threads)

    Supports DD-278 scope-tag wrapper via ``scope=`` argument. Post-fetch
    verification across the union of message labelIds in the thread —
    bytes are pulled before scope-verify per DD-338 spec §10 residual threat.
    """
    start = time.monotonic()
    try:
        if scope is not None and scope not in _VALID_SCOPES:
            raise InvalidRequestError(f"Unknown scope {scope!r}. Valid: work, personal, family, public, None.")
        client = _get_client()
        thread = await _run(client.get_thread, thread_id)
        filters_applied: list[str] = []
        redactions: list[str] = []
        scope_mismatch = False
        if scope is not None:
            filters_applied.append(f"scope={scope}")
            label_ids = _thread_label_ids(thread)
            if not _scope_matches(scope, label_ids, client):
                scope_mismatch = True
                redactions.append("scope_mismatch")
        if scope_mismatch:
            payload = "(no messages matched scope filter)"
            matched_total = 0
            returned = 0
        else:
            payload = format_thread(thread, thread_mode=thread_mode, max_body_chars=max_body_chars)
            matched_total = 1
            returned = 1
        if not include_meta:
            return payload
        latency_ms = int((time.monotonic() - start) * 1000)
        domain_hints: dict[str, str] = {}
        if not scope_mismatch:
            thread_messages = thread.get("messages", []) if isinstance(thread, dict) else []
            if isinstance(thread_messages, list):
                domain_hints = _compute_domain_hints_for_records(thread_messages)
        meta = _format_meta_envelope(
            matched_total=matched_total,
            returned=returned,
            filtered_by=filters_applied,
            latency_ms=latency_ms,
            redactions=redactions or None,
            domain_hints=domain_hints or None,
        )
        return _append_meta(payload, meta)
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
        # DD-338 Phase B.1.b — stable sort: sendAsEmail asc (case-folded).
        identities = sorted(
            identities,
            key=lambda i: (i.get("sendAsEmail", "") or "").casefold(),
        )
        return format_send_as_list(identities)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_filters() -> str:
    """List all Gmail filters with criteria and actions."""
    try:
        filters = await _run(_get_client().list_filters)
        # DD-338 Phase B.1.b — stable sort: id asc.
        filters = sorted(filters, key=lambda f: f.get("id", "") or "")
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
    remove_labels: Annotated[str, Field(description="Label IDs to remove, comma-separated (e.g. UNREAD, INBOX)")] = "",
) -> str:
    """Add or remove labels on a message (star, mark read, etc.). Requires GMAIL_WRITE_ENABLED=true."""
    if err := require_write():
        return err
    try:
        add = [lbl.strip() for lbl in add_labels.split(",") if lbl.strip()] if add_labels else None
        remove = [lbl.strip() for lbl in remove_labels.split(",") if lbl.strip()] if remove_labels else None
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
            add = [lbl.strip() for lbl in add_labels.split(",") if lbl.strip()] if add_labels else None
            remove = [lbl.strip() for lbl in remove_labels.split(",") if lbl.strip()] if remove_labels else None
            if not add and not remove:
                return "Error: 'label' action requires add_labels or remove_labels."
        case _:
            return f"Error: Unknown action '{action}'. Use: archive, read, unread, star, unstar, trash, label."

    try:
        result = await _run(_get_client().batch_modify, ids, add_labels=add, remove_labels=remove)
        return str(result)
    except GmailError as e:
        return _error_response(e)


@mcp.tool()
async def gmail_delete(
    message_id: Annotated[str, Field(description="Message ID to delete")],
    confirm: Annotated[
        bool,
        Field(description="Must be true to confirm permanent deletion. Use gmail_move to TRASH for soft delete."),
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
        action["addLabelIds"] = [lbl.strip() for lbl in add_labels.split(",") if lbl.strip()]
    if remove_labels:
        action["removeLabelIds"] = [lbl.strip() for lbl in remove_labels.split(",") if lbl.strip()]
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
# AI TOOLS (powered by Gemini — requires GOOGLE_API_KEY)
# ===========================================================================


@mcp.tool()
async def gmail_classify(
    message_id: Annotated[str, Field(description="Gmail message ID to classify")],
    body_mode: Annotated[
        str,
        Field(description="Body format for classification: 'stripped' (default), 'snippet' (cheaper)"),
    ] = "stripped",
) -> str:
    """Classify an email using Gemini. Returns category, priority, suggested action, and one-line summary.

    Categories: personal, work, transactional, marketing, notification, social, finance, travel, support.
    Requires GOOGLE_API_KEY.
    """
    if err := require_gemini():
        return err
    try:
        message = await _run(_get_client().get_message, message_id)
        email_text = format_message_body(message, body_mode=body_mode, max_body_chars=2000)
        result = await _run(get_gemini_client().classify, email_text)

        if "error" in result:
            return f"Classification failed: {result.get('raw', result.get('error'))}"

        lines = [
            f"Category: {result.get('category', '?')}",
            f"Priority: {result.get('priority', '?')}",
            f"Action: {result.get('action', '?')}",
            f"Summary: {result.get('summary', '?')}",
        ]
        return "\n".join(lines)
    except GmailError as e:
        return _error_response(e)
    except Exception as e:
        return f"Error: Gemini classification failed: {e}"


@mcp.tool()
async def gmail_summarise(
    message_id: Annotated[str, Field(description="Message ID (single email) — mutually exclusive with thread_id")] = "",
    thread_id: Annotated[str, Field(description="Thread ID (full thread) — mutually exclusive with message_id")] = "",
) -> str:
    """Summarise an email or thread using Gemini. Returns concise summary with action items.

    Provide either message_id (single email) or thread_id (full thread), not both.
    Requires GOOGLE_API_KEY.
    """
    if err := require_gemini():
        return err
    if not message_id and not thread_id:
        return "Error: Provide either message_id or thread_id."
    if message_id and thread_id:
        return "Error: Provide message_id or thread_id, not both."

    try:
        if message_id:
            message = await _run(_get_client().get_message, message_id)
            email_text = format_message_body(message, body_mode="stripped", max_body_chars=4000)
        else:
            thread = await _run(_get_client().get_thread, thread_id)
            email_text = format_thread(thread, thread_mode="deduped", max_body_chars=2000)

        summary: str = await _run(get_gemini_client().summarise, email_text)
        return summary
    except GmailError as e:
        return _error_response(e)
    except Exception as e:
        return f"Error: Gemini summarisation failed: {e}"


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    """Run the MCP server."""
    if TRANSPORT == "http":
        from starlette.middleware import Middleware

        from gmail_blade_mcp.auth import BearerAuthMiddleware, get_bearer_token

        bearer = get_bearer_token()
        logger.info("Starting HTTP transport on %s:%s", HTTP_HOST, HTTP_PORT)
        if bearer:
            logger.info("Bearer token auth enabled (GMAIL_MCP_API_TOKEN is set)")
        else:
            logger.info("Bearer token auth disabled (no GMAIL_MCP_API_TOKEN)")
        mcp.run(
            transport="http",
            host=HTTP_HOST,
            port=HTTP_PORT,
            middleware=[Middleware(BearerAuthMiddleware)],
        )
    else:
        mcp.run()
