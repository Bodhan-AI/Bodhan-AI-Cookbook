from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable
from html import escape
from typing import Any

from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_mcp.core.languages import LANGUAGE_SCRIPTS, SCRIPT_RANGES
from bodhan_mcp.utils.script import script_counts, script_of

INDIC_SCRIPTS: frozenset[str] = frozenset(script for _, _, script in SCRIPT_RANGES) - {"Latin"}
PATH_FIELDS: frozenset[str] = frozenset(
    {"path", "file_path", "audio_path", "text_path", "output_path", "segments_path", "blocks_path"}
)
CERTAIN_LANGUAGE_SOURCES: frozenset[str] = frozenset({"requested", "detected"})
MCP_WRITING_TOOLS: frozenset[str] = frozenset(
    {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document"}
)
INDIC_SPEECH_PRODUCERS: frozenset[str] = frozenset({"bodhan", "user_input"})
COMPOSABLE_PRODUCERS: frozenset[str] = frozenset({"bodhan", "user_input"})
WHOLE_REDACTION_SHARE = 0.5
UNTRUSTED_TAG = "untrusted_data"

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:instructions?|prompts?|rules|guidelines)\b",
        r"\b(?:system|developer)\s+(?:prompt|message|instructions?)\b",
        r"\byou\s+are\s+now\b",
        r"\b(?:call|use|invoke|run)\s+the\s+\w+\s+tool\b",
        r"\bapi[\s_-]?keys?\b|\bpasswords?\b|\bcredentials?\b",
        r"\b(?:send|upload|email|post|forward)\b.{0,60}(?:https?://|\S+@\S+\.\w+)",
        r"https?://\S+",
        r"^\s*(?:assistant|system)\s*:",
    )
)
_ROMANIZED_HINDI: frozenset[str] = frozenset(
    {
        "hai", "hain", "nahi", "nahin", "kya", "kyun", "kyunki", "kaise", "aap", "aapka", "aapki", "mera",
        "meri", "mere", "tera", "teri", "tumhara", "humara", "hamara", "accha", "achha", "bahut", "lekin",
        "aur", "yeh", "woh", "kuch", "bhi", "raha", "rahi", "rahe", "gaya", "gayi", "karo", "karna", "kijiye",
        "dhanyavaad", "dhanyavad", "shukriya", "matlab", "abhi", "kab", "kahan", "kaun", "sakta", "sakti",
    }
)  # fmt: skip
_ROMANIZED_MIN_WORDS = 3
_ROMANIZED_MIN_SHARE = 0.08
_WORD_RE = re.compile(r"[A-Za-z]+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_MIN_SECRET_CHARS = 6


def _is_letter(char: str) -> bool:
    return unicodedata.category(char)[0] in "LM"


def _base_language(code: str) -> str:
    return code.split("-", 1)[0].lower()


def _normalized(paths: Iterable[str]) -> frozenset[str]:
    return frozenset(os.path.normpath(path) for path in paths)


def _is_known_path(key: str | None, value: str, known: frozenset[str]) -> bool:
    return key in PATH_FIELDS and os.path.normpath(value) in known


def is_indic_letter(char: str) -> bool:
    return _is_letter(char) and script_of(char) in INDIC_SCRIPTS


def indic_letter_count(text: str) -> int:
    return sum(1 for char in text if is_indic_letter(char))


def contains_indic(text: str) -> bool:
    return any(is_indic_letter(char) for char in text)


def indic_share(text: str) -> float:
    letters = [char for char in text if _is_letter(char)]
    if not letters:
        return 0.0
    return sum(1 for char in letters if script_of(char) in INDIC_SCRIPTS) / len(letters)


def indic_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    for index, char in enumerate(text):
        if is_indic_letter(char):
            if start is None:
                start = index
            end = index + 1
        elif _is_letter(char) and start is not None:
            spans.append((start, end))
            start = None
    if start is not None:
        spans.append((start, end))
    return spans


def main_indic_script(text: str) -> str | None:
    counts = {script: count for script, count in script_counts(text).items() if script in INDIC_SCRIPTS}
    return max(counts, key=lambda script: counts[script]) if counts else None


def placeholder(text: str, ref: str | None = None) -> str:
    script = main_indic_script(text) or "Indic"
    where = f", in {ref}" if ref else ""
    return f"[{len(text.strip()):,} chars of {script} text{where}; hidden from the LLM]"


def redact_text(text: str, ref: str | None = None) -> tuple[str, int]:
    spans = indic_spans(text)
    if not spans:
        return text, 0
    if indic_share(text) >= WHOLE_REDACTION_SHARE:
        return placeholder(text, ref), 1
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(text[cursor:start])
        parts.append(placeholder(text[start:end], ref))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts), len(spans)


def redact(value: Any, known_paths: Iterable[str] = (), ref: str | None = None) -> tuple[Any, int]:
    known = _normalized(known_paths)

    def walk(item: Any, key: str | None) -> tuple[Any, int]:
        if isinstance(item, str):
            return (item, 0) if _is_known_path(key, item, known) else redact_text(item, ref)
        if isinstance(item, dict):
            total = 0
            result: dict[Any, Any] = {}
            for inner_key, inner in item.items():
                result[inner_key], count = walk(inner, str(inner_key))
                total += count
            return result, total
        if isinstance(item, (list, tuple)):
            total = 0
            items: list[Any] = []
            for inner in item:
                cleaned, count = walk(inner, key)
                items.append(cleaned)
                total += count
            return items, total
        return item, 0

    return walk(value, None)


def find_indic(value: Any, known_paths: Iterable[str] = (), location: str = "") -> list[str]:
    known = _normalized(known_paths)
    found: list[str] = []

    def walk(item: Any, key: str | None, where: str) -> None:
        if isinstance(item, str):
            if contains_indic(item) and not _is_known_path(key, item, known):
                found.append(where or "value")
        elif isinstance(item, dict):
            for inner_key, inner in item.items():
                walk(inner, str(inner_key), f"{where}.{inner_key}" if where else str(inner_key))
        elif isinstance(item, (list, tuple)):
            for index, inner in enumerate(item):
                walk(inner, key, f"{where}[{index}]")

    walk(value, None, location)
    return found


def wrap_untrusted(text: str, source: str) -> str:
    safe_text = re.sub(rf"</?\s*{UNTRUSTED_TAG}", lambda match: match.group(0).replace("<", "&lt;"), text)
    return f'<{UNTRUSTED_TAG} source="{escape(source, quote=True)}">\n{safe_text}\n</{UNTRUSTED_TAG}>'


def injection_lines(text: str, limit: int = 5, max_chars: int = 160) -> list[str]:
    flagged: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(pattern.search(stripped) for pattern in _INJECTION_PATTERNS):
            flagged.append(stripped if len(stripped) <= max_chars else stripped[: max_chars - 1] + "…")
            if len(flagged) >= limit:
                break
    return flagged


def romanized_indic_words(text: str) -> list[str]:
    words = [word.lower() for word in _WORD_RE.findall(text)]
    if not words:
        return []
    hits = [word for word in words if word in _ROMANIZED_HINDI]
    distinct = sorted(set(hits))
    if len(distinct) >= _ROMANIZED_MIN_WORDS and len(hits) / len(words) >= _ROMANIZED_MIN_SHARE:
        return distinct
    return []


def llm_text_problem(text: str) -> tuple[str, str] | None:
    if contains_indic(text):
        return "indic_script", "contains Indian-language script; write English and use translate_text instead."
    words = romanized_indic_words(text)
    if words:
        return (
            "romanized_indic",
            f"looks like romanized Indian-language text ({', '.join(words[:5])}); write English, or use "
            "translate_text with script='roman'.",
        )
    return None


def harden_tool_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    hardened = dict(args)
    if tool in MCP_WRITING_TOOLS:
        hardened["overwrite"] = False
    return hardened


def inline_speech_problem(language: str) -> str | None:
    if _base_language(language) == "en":
        return None
    return (
        f"inline text can only be spoken in English. For '{language}', pass text_path pointing to Bodhan output "
        "(a translation, transcript or OCR file) or to the user's own text file."
    )


def speech_source_problem(artifact: Artifact | None, language: str) -> str | None:
    if artifact is None:
        return "text_path is not a file known to this run; use a path from an earlier tool result or input."
    if not artifact.usable:
        return f"{artifact.id} has status '{artifact.step_status}' and cannot be spoken until it is usable."
    target = _base_language(language)
    if target != "en" and artifact.producer not in INDIC_SPEECH_PRODUCERS:
        return (
            f"{artifact.id} was produced by '{artifact.producer}'. Indian-language speech must read Bodhan output "
            "or the user's own text; translate it with translate_text first."
        )
    if (
        artifact.language is not None
        and artifact.language_source in CERTAIN_LANGUAGE_SOURCES
        and _base_language(artifact.language) != target
    ):
        return (
            f"{artifact.id} is '{artifact.language}' text but speech was requested in '{language}'. "
            "Translate it to the requested language first."
        )
    scripts = LANGUAGE_SCRIPTS.get(target)
    if artifact.script is not None and scripts and artifact.script not in scripts:
        return (
            f"{artifact.id} is written in {artifact.script} script, which '{language}' does not use. "
            "Translate it to the requested language first."
        )
    return None


def _composed_from_bodhan(ledger: Ledger, artifact: Artifact) -> bool:
    if not artifact.sources:
        return False
    for source_id in artifact.sources:
        source = ledger.get(source_id)
        if source is None or not source.usable:
            return False
        if source.producer in COMPOSABLE_PRODUCERS:
            continue
        if source.producer == "code_composed" and _composed_from_bodhan(ledger, source):
            continue
        return False
    return True


def deliverable_source_problem(ledger: Ledger, artifact: Artifact, language: str | None) -> str | None:
    if artifact.producer == "user_input":
        return f"{artifact.id} is one of the user's inputs, not something produced in this run."
    indic = artifact.indic_text or (language is not None and _base_language(language) != "en")
    if not indic:
        return None
    if artifact.producer == "llm_english":
        return f"{artifact.id} was written by the LLM and cannot be an Indian-language deliverable."
    if artifact.producer == "code_composed" and not _composed_from_bodhan(ledger, artifact):
        return (
            f"{artifact.id} was not assembled only from usable Bodhan output or user text, so it cannot be an "
            "Indian-language deliverable."
        )
    return None


def scrub_secrets(text: str, secrets: Iterable[str]) -> str:
    for secret in sorted({item for item in secrets if len(item) >= _MIN_SECRET_CHARS}, key=len, reverse=True):
        text = text.replace(secret, "[secret]")
    return _BEARER_RE.sub("Bearer [secret]", text)
