"""Scrub account identifiers and key-like strings from provider error messages before they reach reports or logs."""
from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"\borg[_-][A-Za-z0-9]{8,}"), "org_[redacted]"),
    (re.compile(r"\b(?:sk|gsk|xai|pk)[-_][A-Za-z0-9_-]{12,}"), "[redacted-key]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}"), "Bearer [redacted]"),
    (re.compile(r"\b(?:user|project|acct|account)[_-][A-Za-z0-9]{10,}"), "[redacted-id]"),
]


def redact(text):
    if not isinstance(text, str):
        return text
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text
