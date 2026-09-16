"""Namespace governance: normalization, validation and confirmation codes.

A namespace is just an attribute of a memory record, not a registered entity:
the set of known namespaces is derived dynamically from live (non-deleted)
records. Because a typo would otherwise either return an empty result set
(misread as "no memories") or silently create an isolated namespace, writes
and queries against an unknown namespace are rejected unless the caller
explicitly confirms the matching validation rule.

Confirmation codes are keyed by the validation rule they bypass and only ever
bypass that one rule — they can never skip name format/length/reserved-word
checks or embedding validation. Codes with no matching rule are ignored.
"""

import logging

logger = logging.getLogger("namespace")

# A namespace name is trim-normalized, case-sensitive and Unicode-friendly.
MAX_NAMESPACE_LENGTH = 64

# Names that would be ambiguous as a literal namespace value (e.g. "all" is
# how users describe "no namespace filter"). ``default`` is intentionally
# allowed — it is the configured default namespace.
RESERVED_NAMESPACES = frozenset({"all", "*", "none", "null"})

# Validation-rule identifier, shared by the confirmation code that bypasses it.
NAMESPACE_NOT_FOUND = "namespace_not_found"

# Registry of rules a confirmation code may bypass. A code only takes effect
# when it appears here under the exact same string as the rule.
CONFIRM_RULES = frozenset({NAMESPACE_NOT_FOUND})

# Control characters would break single-line identifiers and log/JSONL fields.
_FORBIDDEN_CHARS = ("\x00", "\n", "\r", "\t")


class NamespaceNotFound(Exception):
    """Raised when a write/query targets a namespace with no live memories.

    ``requested`` is the trimmed namespace the caller asked for; ``known`` is
    the list of currently known namespaces, including the configured default,
    suitable for building a retry hint.
    """

    code = NAMESPACE_NOT_FOUND

    def __init__(self, requested: str, known):
        self.requested = requested
        self.known = list(known or [])
        super().__init__(f"namespace not found: {requested}")


def normalize_namespace(namespace) -> str | None:
    """Trim a namespace value; blank/None/non-string becomes None.

    Callers decide what "no namespace" means: the default namespace for
    ``add``, "leave unchanged" for ``update``, "all namespaces" for queries.
    """
    if not isinstance(namespace, str):
        return None
    trimmed = namespace.strip()
    return trimmed or None


def validate_namespace_name(namespace: str) -> str:
    """Validate a normalized, non-empty namespace name (format only).

    Raises ``ValueError``; existence is enforced separately (needs storage).
    """
    if len(namespace) > MAX_NAMESPACE_LENGTH:
        raise ValueError(
            f"namespace must be at most {MAX_NAMESPACE_LENGTH} characters"
        )
    if namespace in RESERVED_NAMESPACES:
        raise ValueError(f"namespace '{namespace}' is reserved")
    if any(ch in namespace for ch in _FORBIDDEN_CHARS):
        raise ValueError("namespace must not contain control characters")
    return namespace


def effective_confirm_codes(confirm_codes) -> set[str]:
    """Return only the confirmation codes that map to a known rule."""
    if not isinstance(confirm_codes, (list, tuple, set, frozenset)):
        return set()
    return {
        code for code in confirm_codes
        if isinstance(code, str) and code in CONFIRM_RULES
    }


def is_confirmed(confirm_codes, code: str) -> bool:
    """True when ``code`` is a known rule explicitly confirmed by the caller."""
    return code in effective_confirm_codes(confirm_codes)
