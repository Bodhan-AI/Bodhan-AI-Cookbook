"""Source-side spoken plan for tables and dense math.

Plan once on lesson.src.md (tutor-tone English). Narrate swaps those
snippets into translated prose instead of re-describing per language.
Never free-write Indic in the LLM.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from bodhan_lesson.llm_client import chat_json
from bodhan_lesson.progress import done, stage
from bodhan_lesson.translate import is_indic_lang, translate_text

SPOKEN_PLAN_NAME = "spoken_plan.json"

_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_DISPLAY_MATH_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(?:\\.|[^$\\])+\$")


def _inline_is_dense(body: str) -> bool:
    return "\\" in body or "^" in body or "_" in body or "{" in body


def extract_hard_spans(text: str) -> list[dict[str, Any]]:
    """Tables, display math, and dense inline math in reading order (no overlap)."""
    raw = text or ""
    candidates: list[tuple[int, int, str, str]] = []
    for match in _TABLE_RE.finditer(raw):
        candidates.append((match.start(), match.end(), "table", match.group(0)))
    for match in _DISPLAY_MATH_RE.finditer(raw):
        candidates.append((match.start(), match.end(), "display_math", match.group(0)))
    for match in _INLINE_MATH_RE.finditer(raw):
        body = match.group(0)
        if _inline_is_dense(body):
            candidates.append((match.start(), match.end(), "inline_math", body))

    candidates.sort(key=lambda row: (row[0], -(row[1] - row[0])))
    spans: list[dict[str, Any]] = []
    cursor = 0
    for start, end, kind, source_span in candidates:
        if start < cursor:
            continue
        spans.append(
            {
                "id": f"span_{len(spans) + 1:03d}",
                "kind": kind,
                "source_span": source_span,
                "start": start,
                "end": end,
            }
        )
        cursor = end
    return spans


def swap_hard_spans(text: str, replacements: list[str | None]) -> tuple[str, list[str]]:
    """Replace hard spans in order. None keeps the original span.

    Returns (new_text, notes).
    """
    spans = extract_hard_spans(text)
    notes: list[str] = []
    if not spans:
        return text or "", notes

    parts: list[str] = []
    cursor = 0
    raw = text or ""
    for index, span in enumerate(spans):
        start = int(span["start"])
        end = int(span["end"])
        parts.append(raw[cursor:start])
        spoken = replacements[index] if index < len(replacements) else None
        if spoken is None or not str(spoken).strip():
            parts.append(span["source_span"])
            notes.append(f"kept_span:{span['id']}")
        else:
            parts.append(str(spoken).strip())
        cursor = end
    parts.append(raw[cursor:])
    if len(replacements) < len(spans):
        notes.append(f"replacement_shortfall:{len(spans) - len(replacements)}")
    elif len(replacements) > len(spans):
        notes.append(f"replacement_surplus:{len(replacements) - len(spans)}")
    return "".join(parts), notes


def _tutor_prompt(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = [
        {
            "span_id": row["id"],
            "kind": row["kind"],
            "text": row.get("text_for_llm") or row["source_span"],
        }
        for row in items
    ]
    system = (
        "You write short tutor-style English narration for textbook fragments "
        "a student cannot hear as raw markup (HTML tables, LaTeX equations). "
        "Speak as a clear teacher: accurate, plain words, no slang. "
        "Reply with JSON only. Schema: "
        '{"items":[{"span_id":"...","spoken_en":"..."}]}. '
        "Rules: (1) One or two sentences per item. "
        "(2) Spell out math in words (degrees, fractions, symbols). "
        "(3) Do not invent facts beyond the fragment. "
        "(4) Cover every given span_id. "
        "(5) English only — never write Indian-language script."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"spans": payload}, ensure_ascii=False)},
    ]


def describe_spans_en(
    spans: list[dict[str, Any]],
    *,
    chat_fn: Callable[..., tuple[dict[str, Any], dict[str, int]]] | None = None,
) -> tuple[dict[str, str], dict[str, int], list[str]]:
    """LLM tutor spoken_en per span id. Returns (id→text, usage, errors)."""
    if not spans:
        return {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, []

    caller = chat_fn or chat_json
    try:
        data, usage = caller(_tutor_prompt(spans))
    except Exception as exc:  # noqa: BLE001
        return {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, [
            f"llm_spoken_plan_failed: {exc}"
        ]

    mapping: dict[str, str] = {}
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("span_id") or "")
        spoken = (item.get("spoken_en") or "").strip()
        if sid and spoken:
            mapping[sid] = spoken

    errors: list[str] = []
    for span in spans:
        if span["id"] not in mapping:
            errors.append(f"missing_spoken_en:{span['id']}")
    return mapping, usage, errors


def _pivot_spans_to_english(
    spans: list[dict[str, Any]],
    *,
    api_key: str | None,
    post_fn: Callable[..., dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    """When source is Indic, translate span text to English for the LLM."""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    notes: list[str] = []
    out: list[dict[str, Any]] = []
    for span in spans:
        row = dict(span)
        try:
            result = translate_text(
                span["source_span"], "en", api_key=api_key, post_fn=post_fn
            )
            row["text_for_llm"] = result["translation"]
            for key in usage:
                usage[key] += int((result.get("usage") or {}).get(key) or 0)
        except Exception as exc:  # noqa: BLE001
            row["text_for_llm"] = span["source_span"]
            notes.append(f"pivot_failed:{span['id']}:{exc}")
        out.append(row)
    return out, usage, notes


def build_spoken_plan(
    markdown: str,
    *,
    source_lang: str = "en",
    api_key: str | None = None,
    chat_fn: Callable[..., tuple[dict[str, Any], dict[str, int]]] | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Detect hard spans and fill tutor-tone spoken_en (once per chapter)."""
    started = time.perf_counter()
    spans = extract_hard_spans(markdown)
    pivot_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    notes: list[str] = []
    planning_lang = "en"
    work = spans

    if spans and is_indic_lang(source_lang):
        work, pivot_usage, pivot_notes = _pivot_spans_to_english(
            spans, api_key=api_key, post_fn=post_fn
        )
        notes.extend(pivot_notes)
        notes.append("planning_via_en_pivot")

    spoken_map, llm_usage, describe_errors = describe_spans_en(work, chat_fn=chat_fn)
    notes.extend(describe_errors)

    planned: list[dict[str, Any]] = []
    for span in work:
        planned.append(
            {
                "id": span["id"],
                "kind": span["kind"],
                "source_span": span["source_span"],
                "spoken_en": spoken_map.get(span["id"]),
                "status": "ok" if spoken_map.get(span["id"]) else "missing",
            }
        )

    ok = sum(1 for row in planned if row["status"] == "ok")
    return {
        "version": 1,
        "source_lang": source_lang or "en",
        "planning_lang": planning_lang,
        "span_count": len(planned),
        "ok": ok,
        "missing": len(planned) - ok,
        "spans": planned,
        "notes": notes,
        "llm_usage": llm_usage,
        "pivot_usage": pivot_usage,
        "latency_s": round(time.perf_counter() - started, 3),
    }


def write_spoken_plan(source_dir: Path, plan: dict[str, Any]) -> Path:
    """Persist source/spoken_plan.json."""
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / SPOKEN_PLAN_NAME
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_spoken_plan(source_dir: Path) -> dict[str, Any] | None:
    """Load spoken_plan.json if present."""
    path = source_dir / SPOKEN_PLAN_NAME
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def ensure_spoken_plan(
    source_dir: Path,
    lesson_path: Path,
    *,
    source_lang: str = "en",
    rebuild: bool = True,
    api_key: str | None = None,
    chat_fn: Callable[..., tuple[dict[str, Any], dict[str, int]]] | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build (or load) the source spoken plan and write it under source/."""
    if not rebuild:
        existing = load_spoken_plan(source_dir)
        if existing is not None:
            return existing

    if not lesson_path.is_file():
        raise FileNotFoundError(f"Missing lesson for spoken plan: {lesson_path}")

    stage("spoken_plan", f"source_lang={source_lang or 'en'}")
    markdown = lesson_path.read_text(encoding="utf-8")
    plan = build_spoken_plan(
        markdown,
        source_lang=source_lang,
        api_key=api_key,
        chat_fn=chat_fn,
        post_fn=post_fn,
    )
    path = write_spoken_plan(source_dir, plan)
    plan["path"] = str(path)
    done(
        "spoken_plan",
        spans=plan.get("span_count"),
        ok=plan.get("ok"),
        missing=plan.get("missing"),
    )
    return plan


def translate_plan_snippets(
    plan: dict[str, Any],
    target_lang: str,
    *,
    api_key: str | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> tuple[list[str | None], dict[str, int], list[str]]:
    """Translate each spoken_en into target_lang (pass-through for en)."""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    notes: list[str] = []
    out: list[str | None] = []
    for span in plan.get("spans") or []:
        spoken_en = (span.get("spoken_en") or "").strip()
        if not spoken_en:
            out.append(None)
            notes.append(f"no_spoken_en:{span.get('id')}")
            continue
        if target_lang == "en":
            out.append(spoken_en)
            continue
        try:
            result = translate_text(
                spoken_en, target_lang, api_key=api_key, post_fn=post_fn
            )
            out.append(result["translation"])
            for key in usage:
                usage[key] += int((result.get("usage") or {}).get(key) or 0)
        except Exception as exc:  # noqa: BLE001
            out.append(None)
            notes.append(f"translate:{span.get('id')}:{exc}")
    return out, usage, notes
