"""DD-338 A.2.dom — per-record domain hint computation.

Pattern engine consumed by ``server.py`` to annotate per-record results with
a ``domain_hints: {record_id: domain}`` entry in the ``_meta`` envelope.

First-match-wins over a user-defined pattern list. Patterns live in the
BladeConfigStore (Convention #23) at::

    <state-root>/blade-config/gmail-blade-mcp/config.yaml

The YAML shape::

    patterns:
      - field: from
        op: contains
        value: "@family.com"
        domain: family
      - field: labelIds
        op: equals
        value: Label_42
        domain: work

Empty / missing / malformed config ⇒ empty pattern list ⇒ no
``domain_hints`` key emitted (Convention #22 graceful degradation).
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pattern:
    """A single domain-hint matching rule.

    Attributes:
        field: Logical field name (e.g. ``from``, ``labelIds``); resolved by
            the per-blade ``field_projector`` callable into the record's
            actual storage shape.
        op: One of ``equals`` | ``contains`` | ``glob``.
        value: Comparison value (always coerced to string; lists are matched
            element-wise).
        domain: Domain string to emit on match (e.g. ``family``, ``work``).
    """

    field: str
    op: str
    value: str
    domain: str


_VALID_OPS = frozenset({"equals", "contains", "glob"})


def compute_domain_hint(
    record: dict[str, Any],
    patterns: list[Pattern],
    field_projector: Callable[[dict[str, Any], str], Any],
) -> str | None:
    """Compute the domain hint for a single record.

    First-match-wins over ``patterns``. Returns ``None`` when no pattern
    matches, the record lacks the projected field, or the pattern list is
    empty.

    The projector may return:
        - ``None`` (field absent ⇒ no match)
        - a scalar (compared directly)
        - a list of scalars or dicts (each element compared; dicts coerced
          to empty string ⇒ no match unless ``value=""``)
    """
    if not patterns:
        return None
    for pattern in patterns:
        if pattern.op not in _VALID_OPS:
            # Unknown op silently skipped — defensive against future schema drift.
            continue
        rec_val = field_projector(record, pattern.field)
        if rec_val is None:
            continue
        candidates: list[Any] = rec_val if isinstance(rec_val, list) else [rec_val]
        for c in candidates:
            if isinstance(c, dict):
                # Dict candidates cannot be matched without further projection;
                # callers should project to a string inside the projector.
                continue
            s = str(c)
            if pattern.op == "equals":
                if s == pattern.value:
                    return pattern.domain
            elif pattern.op == "contains":
                if pattern.value in s:
                    return pattern.domain
            elif pattern.op == "glob":
                if fnmatch.fnmatchcase(s, pattern.value):
                    return pattern.domain
    return None


def load_patterns_from_yaml(yaml_str: str) -> list[Pattern]:
    """Parse a YAML config string into a list of ``Pattern``.

    Returns ``[]`` on any of:
        - empty input
        - YAML parse error
        - non-mapping root
        - ``patterns`` key missing or non-list
        - per-pattern missing required keys / type errors

    Per-pattern parse failures are silently skipped (not fatal) — partial
    configs still load their good entries.
    """
    if not yaml_str.strip():
        return []
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pyyaml not installed; domain_hint patterns disabled")
        return []
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        logger.warning("blade-config YAML parse error: %s", e)
        return []
    if not data or not isinstance(data, dict):
        return []
    raw_patterns = data.get("patterns", [])
    if not isinstance(raw_patterns, list):
        return []
    result: list[Pattern] = []
    for p in raw_patterns:
        if not isinstance(p, dict):
            continue
        try:
            result.append(
                Pattern(
                    field=str(p["field"]),
                    op=str(p["op"]),
                    value=str(p["value"]),
                    domain=str(p["domain"]),
                )
            )
        except (KeyError, TypeError):
            continue
    return result
