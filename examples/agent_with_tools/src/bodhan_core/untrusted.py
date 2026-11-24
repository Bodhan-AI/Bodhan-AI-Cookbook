"""Prompt-injection screening for text that came out of OCR, transcripts or input files.

This is a tripwire, not a defence: the real protection is structural (the tools cannot run commands,
writes are confined to the output folder, the planner is told tool text is data). A hit adds a warning
next to the text the planner sees and is recorded in the trace and report.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"ignore\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?)",
    r"disregard\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier)?\s*(?:instructions?|prompts?|rules?)",
    r"forget\s+(?:everything|all|your)\s+(?:above|previous|instructions?)",
    r"(?:system|developer)\s+prompt",
    r"you\s+are\s+now\s+(?:a|an|the|in)\b",
    r"new\s+instructions?\s*:",
    r"(?:^|\n)\s*(?:system|assistant)\s*:",
    r"\b(?:call|use|run|invoke|execute)\s+(?:the\s+)?\w+\s+tool\b",
    r"\b(?:delete|overwrite|exfiltrate|reveal)\b[^.\n]{0,40}\b(?:file|key|secret|password|token)s?\b",
    r"\bapi[_ -]?key\b",
    r"instead\s*,?\s*(?:translate|speak|say|write|output)\b",
]
_RX = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


def find_injection(text: str) -> list[str]:
    """Return the snippets of ``text`` that look like instructions aimed at an AI agent (empty list: none found)."""
    hits: list[str] = []
    for rx in _RX:
        m = rx.search(text)
        if m:
            hits.append(text[max(0, m.start() - 20) : m.end() + 20].replace("\n", " ").strip())
    return hits
