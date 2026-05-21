# Changelog

## 0.4.0 — 2026-05-21

### Added

- **DD-278 scope-tag wrapper** on the four read tools (`gmail_search`,
  `gmail_snippets`, `gmail_thread`, `gmail_read`). New optional `scope=`
  argument accepts `work` / `personal` / `family` / `public` / `None`. For
  search-family tools the scope tag is composed into the Gmail native `q=`
  expression server-side (`(<user query>) (<scope expr>)`); for single-record
  tools (`gmail_read`, `gmail_thread`) the scope is verified post-fetch against
  the record's `labelIds`, with a `scope_mismatch` redaction in the `_meta`
  envelope when the verification fails.
- **Env-var override scheme** for scope-tag mappings: `GMAIL_WORK_LABEL`
  (default `category:work`), `GMAIL_PERSONAL_LABEL` (default
  `category:personal`), `GMAIL_FAMILY_LABEL` (default `label:Family`). The env
  var value is interpolated verbatim into the `q=` clause — invalid Gmail
  syntax surfaces as `InvalidRequestError` via the existing error path.
- **DD-338 Track 3 `_meta` envelope** on the four read tools via new
  `include_meta: bool = False` argument. When `True`, the tool appends a
  canonical JSON-tail block (`\n\n_meta: {"matched_total": ..., "returned":
  ..., "filtered_by": [...], "latency_ms": ...}`) per the architect amendment
  2026-05-21. Optional `redactions`, `next_cursor`, and `error_notes` fields.

### Changed

- Catalog declaration `audit_surface: minimal → structured` on the four read
  tools (paired stallari-plugins PR). `scope_filtering` remains `server-side`
  as honestly declared in the previous catalog release. `deterministic_ordering`
  unchanged in Phase A.1.

### Notes

- Backwards-compatible: `gmail_search(query="from:alice")` with no `scope` and
  no `include_meta` produces byte-identical output to v0.3.0. All 67 pre-existing
  tests pass unchanged.
- `scope="public"` is a no-op pass-through on search-family tools (no addition
  to `q=`); the scope tag is recorded in `_meta.filtered_by` to surface the
  dispatcher's intent.
- Post-fetch scope verification on `gmail_read` / `gmail_thread` is best-effort
  — bytes are pulled from Gmail before the scope check. Documented residual
  threat per DD-338 spec §10; mitigated by single-record fetches naming the
  specific message/thread ID.

## 0.3.0 — earlier

Prior production release. See git history.
