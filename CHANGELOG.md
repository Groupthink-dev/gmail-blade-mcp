# Changelog

## [0.9.0] - 2026-07-02

### Added

- **Env-credential mode for Stallari / headless deployments** (#5): when
  `GMAIL_OAUTH_CLIENT_ID` + `GMAIL_OAUTH_CLIENT_SECRET` + `GMAIL_OAUTH_REFRESH_TOKEN`
  are set, credentials are built in memory (refresh-token grant) and nothing is
  ever written to disk. Precedence: env triple → `~/.gmail-blade/token.json` →
  typed `AuthError` with remediation. A partial triple is a hard error naming
  the missing vars.
- **`gmail-blade-mcp auth` CLI verb**: interactive OAuth consent (the only code
  path that may open a browser). `--scopes {read,write}` (default write),
  `--client-id`/`--client-secret`, `--no-save-token`, and `--check` (prints
  account email + granted scopes without prompting). Mint prints the
  paste-ready `GMAIL_OAUTH_*` triple for Stallari's Connections UI.
- `ScopeError` (subclass of `AuthError`): raised when the refresh-token grant
  lacks requested scopes (`invalid_scope` on refresh) or the API returns 403
  `insufficientPermissions` — message points at `auth --scopes write`.
- New `errors.py` module — exception hierarchy moved out of `client.py`
  (re-exported there, so `from gmail_blade_mcp.client import AuthError` still
  works).

### Changed

- **BREAKING: the server never opens a browser.** First-run consent moved to
  the `auth` verb; server mode raises a typed `AuthError` telling you to run it
  (or set the env triple). Previously a missing/expired token in a headless
  deployment would hang on `InstalledAppFlow.run_local_server`.
- `token.json` is written with 0600 perms (dir 0700), set at create time;
  pre-existing files are chmodded on load.
- Console script entry moved `server:main` → `cli:main`; bare
  `gmail-blade-mcp` (and `gmail-blade-mcp serve`) behaviour unchanged.
- Missing `credentials.json` now raises `AuthError` instead of
  `FileNotFoundError`.
- `GmailClient` construction (OAuth refresh + profile fetch) now runs off the
  event loop via an async `_aget_client()` accessor — first tool call no longer
  blocks the loop.

## [0.8.0] - 2026-05-24

### Changed

- DD-338 Phase E.python: depend on `stallari-mcp-helpers>=0.1.0,<1.0.0`; deleted
  local `src/gmail_blade_mcp/domain_hint.py` + local `_format_meta_envelope` /
  `_append_meta` helpers from `server.py`. Pure substrate swap — no behavioural
  change. Wire-shape:
  - `_meta.filtered_by` now alphabetically sorted (canonical lib invariant).
  - JSON separators tightened to `(",", ":")` (canonical compact form).
  - `_meta.redactions` (empty list) and `_meta.next_cursor` (null) now always
    emitted as required keys (canonical lib always-required shape).
- `_compute_domain_hints_for_records` now pre-flattens Gmail message records
  via `_gmail_field_projector` into a flat dict before calling the canonical
  `compute_domain_hint` (which dropped the projector-callable parameter). The
  per-blade `_gmail_field_projector` is retained — it encodes Gmail's nested
  `payload.headers[*]` shape and remains blade-local logic.

## 0.6.0 — 2026-05-23

### Changed

- **DD-338 Phase B.1.b — stable sort-before-return on 5 multi-record read tools.**
  - `gmail_search` + `gmail_snippets`: now sort by `internalDate` desc, `id` asc
    tie-break before formatter — provides byte-identical output across paginations
    and cache thrash.
  - `gmail_mailboxes`: `format_label_list` now sorts by case-folded `name` asc with
    `id` asc tie-break (formerly raw `name` only). Users with mixed-case labels
    (e.g. `Family` + `family-archive`) will see a deterministic re-order; consumers
    that tolerated unstable ordering pre-B.1.b are unaffected by content but see
    tightened ordering. Land without deprecation cycle per DD-338 spec architect
    lock #5.
  - `gmail_identities`: sort by case-folded `sendAsEmail` asc before formatter.
  - `gmail_filters`: sort by `id` asc before formatter.
- **Catalog declaration**: `granularity.deterministic_ordering` flips
  `unstable → stable` on all 5 tools in `stallari-plugins` catalog entry.
- **Tool descriptions** updated to document the chosen sort key per tool.

### Added

- **16 new pytest cases** in `tests/test_b1b_determinism.py` — N=5 byte-identical
  determinism harness per tool + sort-key invariance + empty-input + id-tiebreak
  test for `format_label_list`.

## 0.5.0 — 2026-05-23

### Added

- **DD-338 A.2.dom.c per-record `domain_hints` substrate.** Read tools
  (`gmail_search`, `gmail_snippets`, `gmail_read`, `gmail_thread`) now emit a
  `domain_hints: {message_id: domain}` entry in the `_meta` envelope when
  user-defined patterns match. Pattern engine in
  `src/gmail_blade_mcp/domain_hint.py` (`Pattern` dataclass +
  `compute_domain_hint` first-match-wins + `load_patterns_from_yaml`).
- **`_load_blade_config` BladeConfigStore reader** (Convention #23 reader-side
  compliance): reads `<state-root>/blade-config/gmail-blade-mcp/config.yaml`
  via `STALLARI_STATE_ROOT` override or `~/Library/Application
  Support/Stallari/` default. Blade-id sanitiser (`.lower().replace("/", "_")`)
  in lockstep with the Swift writer.
- **Gmail field projector** mapping logical pattern fields (`from`, `to`,
  `cc`, `subject`, `labelIds`, `id`, `threadId`) onto the Gmail Messages API
  record shape — case-insensitive header lookup with single/multi-header
  collapse.
- **`stallari-plugin.yaml`**: new `blade_domain_hint_patterns:` block at
  pack root, shipping empty `patterns: []` by default. Users define
  patterns via the in-app DomainConsentView (stallari-harness v0.99.29.0).
- **32 new pytest cases** in `tests/test_domain_hint.py` covering the pure
  engine, the YAML loader, the Gmail field projector, the BladeConfigStore
  reader, and end-to-end emission via `gmail_search`.

### Changed

- `_format_meta_envelope` accepts a new `domain_hints: dict[str, str] | None
  = None` kwarg; emits the `domain_hints` key only when non-empty
  (Convention #22 graceful degradation).
- New dependency: `pyyaml>=6.0` for blade-config parsing.

### Notes

- Backwards-compatible: existing tools without populated patterns emit
  byte-identical output to v0.4.0 (`domain_hints` key absent). All 99
  pre-existing tests pass unchanged; 131/131 total green.
- Missing / malformed `config.yaml` ⇒ blade runs with empty pattern list
  ⇒ no `domain_hints` emitted. Never crashes (Convention #22).
- No phone-home (Convention #19): config reload is a local file read at
  module import only.
- `gmail_thread` keys hints by each contained message ID (sub-records)
  rather than the thread ID — most useful for the dispatcher when a
  thread spans domains.

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
