"""Code-level guards that do not trust the LLM to follow the rules.

* No LLM-written Indian-language text: inline `text` arguments and the final answer may not contain
  Indic script unless that exact text came from the user's task statement.
* Untrusted content is wrapped and flagged before the LLM sees it.
* Artifacts must be files a tool produced in this run.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from bodhan_mcp.text import INDIC_SCRIPTS, char_script, contains_indic, looks_like_injection

UNTRUSTED_FIELDS = {"preview", "content"}
PATH_FIELDS = ("transcript_path", "segments_path", "output_path", "audio_path", "subtitle_path")


def _norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s).split())


def indic_text_allowed(text: str, task: str) -> bool:
    """Inline Indic text is allowed only if it was supplied by the user in the task itself."""
    if not contains_indic(text):
        return True
    return _norm(text) in _norm(task)


def strip_indic(text: str) -> str:
    """Last-resort sanitiser for the final answer: replace runs of Indic script with a pointer to the artifacts."""
    out, run = [], False
    for ch in text:
        is_indic = char_script(ch) in INDIC_SCRIPTS or (run and unicodedata.category(ch) in {"Mn", "Mc"})
        if is_indic:
            if not run:
                out.append("[see artifact]")
            run = True
        else:
            run = False
            out.append(ch)
    return re.sub(r"(\[see artifact\]\s*)+", "[see artifact] ", "".join(out))


def wrap_untrusted(data: Any) -> tuple[Any, bool]:
    """Mark file-derived text as data. Returns (wrapped copy, whether something looked like an injection)."""
    flagged = False

    def walk(v: Any, key: str | None = None) -> Any:
        nonlocal flagged
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str) and key in UNTRUSTED_FIELDS:
            if looks_like_injection(v):
                flagged = True
            return f"<untrusted_file_content>{v}</untrusted_file_content>"
        return v

    wrapped = walk(data)
    return wrapped, flagged


def produced_paths(result: dict[str, Any]) -> list[str]:
    return [result[f] for f in PATH_FIELDS if isinstance(result.get(f), str)]
