"""Translate assembled lesson markdown with Indic-Translate.

Protects LaTeX and HTML tables with placeholders, chunks on paragraphs,
pivots Indic→Indic via English, and checks basic structure survival.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from bodhan_lesson.bodhan_client import get_translate_api_key, post_json
from bodhan_lesson.parallel import get_max_lang_workers, map_parallel
from bodhan_lesson.progress import done, item, stage

# Product POST /translate returns 404 on some gateways; docs also allow chat completions.
TRANSLATE_PATH = "/v1/chat/completions"
TRANSLATE_MODEL = "indic-translate"
DEFAULT_MAX_TOKENS = 2048
# Soft character budget per request (paragraph groups).
DEFAULT_CHUNK_CHARS = 1800

_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_DISPLAY_MATH_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(?:\\.|[^$\\])+\$")
_PLACEHOLDER_RE = re.compile(r"⟦PH(\d+)⟧")
_HEADING_RE = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
_DIGIT_RUN_RE = re.compile(r"\d+(?:[.,]\d+)?")


def is_indic_lang(code: str | None) -> bool:
    """True for non-English translate codes (including script variants)."""
    if not code:
        return False
    base = code.split("-", 1)[0]
    return base != "en"


def needs_pivot(source_lang: str | None, target_lang: str) -> bool:
    """Indic→Indic goes via English for better quality."""
    src = source_lang or "en"
    return is_indic_lang(src) and is_indic_lang(target_lang)


def protect_spans(text: str) -> tuple[str, list[str]]:
    """Replace tables and math with placeholders so the MT leaves them alone."""
    spans: list[str] = []

    def _store(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"⟦PH{len(spans) - 1}⟧"

    out = _TABLE_RE.sub(_store, text)
    out = _DISPLAY_MATH_RE.sub(_store, out)
    out = _INLINE_MATH_RE.sub(_store, out)
    return out, spans


def restore_spans(text: str, spans: list[str]) -> str:
    """Put protected spans back. Missing placeholders stay as-is."""

    def _put(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if 0 <= idx < len(spans):
            return spans[idx]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_put, text)


def chunk_paragraphs(text: str, *, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Split on blank lines and pack into chunks under max_chars."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for para in paragraphs:
        extra = len(para) + (2 if current else 0)
        if current and size + extra > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            size = len(para)
        else:
            current.append(para)
            size += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def structure_fingerprint(text: str) -> dict[str, Any]:
    """Counts used to check that structure survived translation."""
    headings = _HEADING_RE.findall(text)
    return {
        "headings": len(headings),
        "h1": sum(1 for h in headings if h == "#"),
        "h2": sum(1 for h in headings if h == "##"),
        "tables": len(_TABLE_RE.findall(text)),
        "display_math": len(_DISPLAY_MATH_RE.findall(text)),
        "inline_math": len(_INLINE_MATH_RE.findall(text)),
        "digit_runs": _DIGIT_RUN_RE.findall(text),
    }


def structure_ok(source: str, translated: str) -> tuple[bool, list[str]]:
    """Compare structure fingerprints; return (ok, list of issues)."""
    src = structure_fingerprint(source)
    dst = structure_fingerprint(translated)
    issues: list[str] = []
    for key in ("headings", "h1", "h2", "tables", "display_math", "inline_math"):
        if src[key] != dst[key]:
            issues.append(f"{key}: source={src[key]} translated={dst[key]}")
    # Numbers should appear in the translation (order may change slightly).
    src_digits = src["digit_runs"]
    dst_digits = dst["digit_runs"]
    if sorted(src_digits) != sorted(dst_digits):
        issues.append("digit_runs mismatch")
    return (len(issues) == 0), issues


def _extract_chat_translation(result: dict[str, Any]) -> tuple[str, bool]:
    """Pull translated text from a chat.completions-shaped response."""
    # Prefer chat shape; fall back to legacy POST /translate fields if present.
    if isinstance(result.get("translation"), str):
        return result["translation"], bool(result.get("truncated"))

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError("translate response had no choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise RuntimeError("translate response missing message.content")
    truncated = choices[0].get("finish_reason") == "length"
    return content, truncated


def translate_text(
    text: str,
    target_language: str,
    *,
    api_key: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One Indic-Translate call via POST /v1/chat/completions."""
    key = api_key or get_translate_api_key()
    poster = post_fn or post_json

    # Match the working console/gateway shape (max_tokens only on truncation retry).
    body: dict[str, Any] = {
        "model": TRANSLATE_MODEL,
        "messages": [{"role": "user", "content": text}],
        "target_language": target_language,
    }
    result = poster(TRANSLATE_PATH, body, api_key=key)
    translation, truncated = _extract_chat_translation(result)
    if truncated:
        retry = dict(body)
        retry["max_tokens"] = max_tokens * 2
        result = poster(TRANSLATE_PATH, retry, api_key=key)
        translation, truncated = _extract_chat_translation(result)
        if truncated:
            raise RuntimeError(
                f"translation truncated for target_language={target_language}"
            )
    return {
        "translation": translation,
        "usage": result.get("usage") or {},
        "truncated": truncated,
    }


def translate_chunked(
    text: str,
    target_language: str,
    *,
    api_key: str | None = None,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    term_map: dict[str, str] | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Protect spans, translate paragraph chunks, restore spans."""
    protected, spans = protect_spans(text)
    chunks = chunk_paragraphs(protected, max_chars=max_chars)
    if not chunks:
        return {
            "translation": text,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "chunks": 0,
        }

    mapped = term_map if term_map is not None else {}
    out_parts: list[str] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for chunk in chunks:
        # Apply known English→target terms for short heading-like lines.
        to_send = chunk
        for en, tgt in mapped.items():
            if en and en in to_send:
                to_send = to_send.replace(en, tgt)

        piece = translate_text(
            to_send, target_language, api_key=api_key, post_fn=post_fn
        )
        translated = piece["translation"]
        out_parts.append(translated)

        # Learn short heading mappings from this chunk (English source lines).
        for src_line, dst_line in zip(
            chunk.split("\n"), translated.split("\n"), strict=False
        ):
            s = src_line.strip()
            d = dst_line.strip()
            if s.startswith("#") and d.startswith("#") and 3 < len(s) < 80:
                mapped.setdefault(s, d)

        for key in usage_total:
            usage_total[key] += int((piece.get("usage") or {}).get(key) or 0)

    joined = "\n\n".join(out_parts)
    restored = restore_spans(joined, spans)
    return {
        "translation": restored,
        "usage": usage_total,
        "chunks": len(chunks),
        "term_map": mapped,
    }


def translate_lesson_markdown(
    text: str,
    target_language: str,
    *,
    source_lang: str | None = None,
    api_key: str | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Translate full lesson markdown; pivot via English when needed."""
    src = source_lang or "en"
    pivot_used = needs_pivot(src, target_language)
    term_map: dict[str, str] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    working = text
    chunks = 0

    if pivot_used:
        mid = translate_chunked(
            working, "en", api_key=api_key, term_map=term_map, post_fn=post_fn
        )
        working = mid["translation"]
        chunks += int(mid.get("chunks") or 0)
        for key in usage:
            usage[key] += int((mid.get("usage") or {}).get(key) or 0)
        term_map = mid.get("term_map") or term_map

    if target_language == "en":
        final = working if pivot_used or src == "en" else working
    else:
        out = translate_chunked(
            working,
            target_language,
            api_key=api_key,
            term_map=term_map,
            post_fn=post_fn,
        )
        final = out["translation"]
        chunks += int(out.get("chunks") or 0)
        for key in usage:
            usage[key] += int((out.get("usage") or {}).get(key) or 0)
        term_map = out.get("term_map") or term_map

    ok, issues = structure_ok(text, final)
    return {
        "translation": final,
        "usage": usage,
        "chunks": chunks,
        "pivot": pivot_used,
        "structure_ok": ok,
        "structure_issues": issues,
        "term_map_size": len(term_map),
    }


def translate_lesson_file(
    lesson_path: Path,
    out_dir: Path,
    langs: list[str],
    *,
    source_lang: str | None = None,
    api_key: str | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Translate lesson.src.md into out/{lang}/lesson.{lang}.md per language."""
    if not lesson_path.is_file():
        raise FileNotFoundError(f"Missing lesson markdown: {lesson_path}")

    source_text = lesson_path.read_text(encoding="utf-8")
    key = api_key or get_translate_api_key()
    total = len(langs)
    workers = get_max_lang_workers()
    stage("translate", f"{total} language(s), workers={min(workers, max(1, total))}")

    def _one(job: tuple[int, str]) -> dict[str, Any]:
        index, lang = job
        item("translate", index, total, lang)
        started = time.perf_counter()
        lang_dir = out_dir / lang
        lang_dir.mkdir(parents=True, exist_ok=True)
        out_path = lang_dir / f"lesson.{lang}.md"
        try:
            if lang == (source_lang or "en"):
                out_path.write_text(source_text, encoding="utf-8")
                item("translate", index, total, lang, "ok (same as source)")
                return {
                    "lang": lang,
                    "status": "ok",
                    "path": str(out_path),
                    "pivot": False,
                    "chunks": 0,
                    "usage": {},
                    "structure_ok": True,
                    "structure_issues": [],
                    "latency_s": round(time.perf_counter() - started, 3),
                    "error": None,
                }

            translated = translate_lesson_markdown(
                source_text,
                lang,
                source_lang=source_lang,
                api_key=key,
                post_fn=post_fn,
            )
            out_path.write_text(translated["translation"], encoding="utf-8")
            item(
                "translate",
                index,
                total,
                lang,
                f"ok chunks={translated['chunks']}",
            )
            return {
                "lang": lang,
                "status": "ok",
                "path": str(out_path),
                "pivot": translated["pivot"],
                "chunks": translated["chunks"],
                "usage": translated["usage"],
                "structure_ok": translated["structure_ok"],
                "structure_issues": translated["structure_issues"],
                "latency_s": round(time.perf_counter() - started, 3),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 — isolate per language
            item("translate", index, total, lang, f"error: {exc}")
            return {
                "lang": lang,
                "status": "error",
                "path": None,
                "pivot": needs_pivot(source_lang, lang),
                "chunks": 0,
                "usage": {},
                "structure_ok": False,
                "structure_issues": [],
                "latency_s": round(time.perf_counter() - started, 3),
                "error": str(exc),
            }

    results = map_parallel(list(enumerate(langs, start=1)), _one)
    ok = sum(1 for row in results if row.get("status") == "ok")
    done("translate", ok=ok, failed=len(results) - ok)
    return results
