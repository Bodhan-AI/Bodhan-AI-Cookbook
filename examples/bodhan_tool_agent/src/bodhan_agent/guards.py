"""Code-enforced rules around every LLM-chosen action.

* The LLM never writes Indian-language text: any LLM-authored string containing an Indic script is
  rejected, with a hint to pass a tool's output file instead.
* Infrastructure arguments are forced: outputs go to the run's work dir, speech is always verified.
* Tool results are compacted before the LLM sees them: paths, stats, issues and short previews,
  with any text from the input files wrapped as untrusted data.
"""

from __future__ import annotations

import json
import re

# Indic blocks (Devanagari through Malayalam), Ol Chiki, Meetei Mayek, plus Arabic script (Urdu, Kashmiri, Sindhi).
INDIC_RE = re.compile(r"[ऀ-ൿ᱐-᱿ꯀ-꯿؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

# String arguments the LLM authors itself (as opposed to paths and codes).
AUTHORED_FIELDS = {
    "translate_text": ["text"],
    "synthesize_speech": ["text"],
    "write_text": ["content"],
    "summarize_text": ["focus"],
    "finish": ["answer"],
}
FORCED_ARGS = {"synthesize_speech": {"verify": True}}

UNTRUSTED_KEYS = {"preview", "text", "translation"}
KEEP_KEYS = [
    "ok", "error", "kind", "name", "path", "duration_s", "pages", "pages_processed", "document_pages", "chars", "lines",
    "language", "language_source", "target_language", "voice", "voice_choice", "segments", "cues", "units",
    "text_path", "transcript_path", "segments_path", "output_path", "audio_path", "srt_path", "manifest_path",
    "complete", "coverage", "issue_count", "issues", "verification", "warnings", "note", "valid", "has_more",
    "offset", "end", "total_chars", "estimated_cost_inr", "suggested_tool", "cost_inr", "cached_pages",
    "failed_pages", "failed_windows", "script", "words", "preview", "translation", "text", "voices", "languages",
]


def indic_violation(tool: str, arguments: dict) -> str | None:
    for field in AUTHORED_FIELDS.get(tool, []):
        value = arguments.get(field)
        if isinstance(value, str) and INDIC_RE.search(value):
            return field
    return None


def violation_result(tool: str, field: str) -> dict:
    hint = ("Indian-language text must come from a Bodhan model, never from you. Pass the file produced by a tool "
            "instead: e.g. translate_text(path=...) then synthesize_speech(path=<its output_path>). Write English with "
            "write_text, then translate it with translate_text.")
    return {"ok": False, "error": {"code": "POLICY_VIOLATION",
                                   "message": f"{tool}.{field} contains Indian-language text written by the agent.",
                                   "hint": hint, "retryable": False}}


def apply_forced(tool: str, arguments: dict) -> dict:
    args = {k: v for k, v in arguments.items() if k != "out_dir"}
    args.update(FORCED_ARGS.get(tool, {}))
    return args


def _untrusted(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return f"<untrusted_content>{value}</untrusted_content>"


def compact(result: dict, *, preview_limit: int = 240, text_limit: int = 6000) -> dict:
    """What the LLM sees of a tool result. Full outputs stay in files."""
    out = {}
    for key in KEEP_KEYS:
        if key not in result:
            continue
        value = result[key]
        if key in UNTRUSTED_KEYS and isinstance(value, str):
            value = _untrusted(value, text_limit if key == "text" else preview_limit)
        elif key == "issues" and isinstance(value, list):
            value = value[:3]
        elif key in ("failed_pages", "failed_windows") and isinstance(value, list):
            value = len(value)
        elif key == "script" and isinstance(value, dict):
            value = {k: value.get(k) for k in ("script", "candidates", "ambiguous")}
        elif key == "voices" and isinstance(value, list):
            value = value[:12]
        out[key] = value
    return out


def summarize_for_history(tool: str, result: dict) -> str:
    """One-line stand-in for an old tool result once it has been pruned from the context."""
    if not result.get("ok"):
        err = result.get("error", {})
        return f"{tool} failed: {err.get('code')} {err.get('message', '')[:120]}"
    paths = {k: v for k, v in result.items() if k.endswith("_path")}
    extra = {k: result[k] for k in ("language", "duration_s", "pages_processed", "complete", "issue_count") if k in result}
    return f"{tool} ok: {json.dumps({**paths, **extra}, ensure_ascii=False)[:400]}"
