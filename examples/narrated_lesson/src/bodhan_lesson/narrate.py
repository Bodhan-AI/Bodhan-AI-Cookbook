"""Build per-language narration scripts for TTS.

Split the translated lesson into sections. Keep translated prose as-is
(light markdown cleanup only). Prefer a source-side spoken_plan:
swap tutor-tone snippets for tables/math, then speak. Fallback: per-unit
LLM English → Indic-Translate. Never free-write Indic in the LLM.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from bodhan_lesson.llm_client import chat_json
from bodhan_lesson.parallel import get_max_lang_workers, map_parallel
from bodhan_lesson.progress import done, item, stage
from bodhan_lesson.spoken_plan import swap_hard_spans, translate_plan_snippets
from bodhan_lesson.translate import translate_text

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_DISPLAY_MATH_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(?:\\.|[^$\\])+\$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FIGURE_RE = re.compile(
    r"^(?:fig(?:ure)?|चित्र|படம்|চিত্র)\b",
    re.IGNORECASE,
)
# Textbook section numbers in headings (6.1, 6.2.1) — awkward if Speak reads digits.
_SECTION_NUM_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)+\s*")


def slugify(title: str, *, fallback: str) -> str:
    """ASCII slug for filenames; fall back when the title is non-Latin."""
    ascii_ready = title.encode("ascii", "ignore").decode("ascii").lower().strip()
    slug = _SLUG_RE.sub("-", ascii_ready).strip("-")
    return slug or fallback


def markdown_to_speech(text: str) -> str:
    """Strip light markdown so TTS gets plain wording. Keep language text."""
    out = text or ""
    out = re.sub(r"^#{1,6}\s+", "", out, flags=re.MULTILINE)
    out = _BOLD_RE.sub(r"\1", out)
    out = _ITALIC_RE.sub(r"\1", out)
    out = out.replace("`", "")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def title_for_speech(title: str) -> str:
    """Heading text for TTS: drop leading section numbers, keep MD title elsewhere.

    ``6.1 Introduction`` → ``Introduction``. Number-only titles yield empty
    (skip speaking). Does not rewrite lesson markdown.
    """
    raw = markdown_to_speech(title or "")
    if not raw:
        return ""
    cleaned = _SECTION_NUM_PREFIX_RE.sub("", raw, count=1).strip()
    return cleaned


def unit_needs_spoken_form(text: str) -> bool:
    """True when raw markup would sound bad if read aloud as-is."""
    raw = (text or "").strip()
    if not raw:
        return False
    if _TABLE_RE.search(raw):
        return True
    if _DISPLAY_MATH_RE.search(raw):
        return True
    # Inline math with LaTeX commands / superscripts — not plain $x$.
    for match in _INLINE_MATH_RE.finditer(raw):
        body = match.group(0)
        if "\\" in body or "^" in body or "_" in body or "{" in body:
            return True
    return False


def split_sections(markdown: str) -> list[dict[str, Any]]:
    """Split lesson markdown on ATx headings into ordered sections."""
    text = markdown or ""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        body = text.strip()
        return [
            {
                "id": "sec_001",
                "slug": "lesson",
                "title": "Lesson",
                "body": body,
            }
        ]

    sections: list[dict[str, Any]] = []
    # Leading prose before the first heading.
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(
            {
                "id": "sec_000",
                "slug": "preamble",
                "title": "Preamble",
                "body": preamble,
            }
        )

    for index, match in enumerate(matches):
        title = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sec_id = f"sec_{index + 1:03d}"
        sections.append(
            {
                "id": sec_id,
                "slug": slugify(title, fallback=sec_id),
                "title": title,
                "body": body,
            }
        )
    return sections


def split_units(body: str) -> list[dict[str, Any]]:
    """Split a section body into paragraph units."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    units: list[dict[str, Any]] = []
    for i, para in enumerate(paragraphs):
        kind = "prose"
        if _TABLE_RE.search(para):
            kind = "table"
        elif _DISPLAY_MATH_RE.search(para) or unit_needs_spoken_form(para):
            kind = "equation" if ("$" in para or "$$" in para) else "special"
        elif _FIGURE_RE.match(para):
            kind = "figure"
        units.append({"id": f"u{i + 1}", "kind": kind, "text": para})
    return units


def _describe_prompt(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = [{"unit_id": u["id"], "kind": u["kind"], "text": u["text"]} for u in units]
    system = (
        "You write short English spoken narration for textbook fragments that a "
        "listener cannot hear as raw markup (tables, LaTeX equations, dense math). "
        "Reply with JSON only. Schema: "
        '{"items":[{"unit_id":"...","spoken_en":"..."}]}. '
        "Rules: (1) One or two plain English sentences per item. "
        "(2) Be accurate; spell out math in words (degrees, fractions, symbols). "
        "(3) Do not translate or rewrite ordinary prose. "
        "(4) Cover every given unit_id."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"units": payload}, ensure_ascii=False)},
    ]


def describe_units_en(
    units: list[dict[str, Any]],
    *,
    chat_fn: Callable[..., tuple[dict[str, Any], dict[str, int]]] | None = None,
) -> tuple[dict[str, str], dict[str, int], list[str]]:
    """Ask the LLM for English spoken forms. Returns (id→text, usage, errors)."""
    if not units:
        return {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, []

    caller = chat_fn or chat_json
    errors: list[str] = []
    try:
        data, usage = caller(_describe_prompt(units))
    except Exception as exc:  # noqa: BLE001 — soft-fail descriptions
        return {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, [
            f"llm_describe_failed: {exc}"
        ]

    mapping: dict[str, str] = {}
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("unit_id") or "")
        spoken = (item.get("spoken_en") or "").strip()
        if uid and spoken:
            mapping[uid] = spoken

    for unit in units:
        if unit["id"] not in mapping:
            errors.append(f"missing_spoken_en:{unit['id']}")
    return mapping, usage, errors


def _translate_spoken(
    spoken_en: str,
    target_lang: str,
    *,
    api_key: str | None,
    post_fn: Callable[..., dict[str, Any]] | None,
) -> tuple[str, dict[str, int]]:
    if target_lang == "en":
        return spoken_en, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    result = translate_text(
        spoken_en, target_lang, api_key=api_key, post_fn=post_fn
    )
    return result["translation"], result.get("usage") or {}


def narrate_section(
    section: dict[str, Any],
    target_lang: str,
    *,
    api_key: str | None = None,
    chat_fn: Callable[..., tuple[dict[str, Any], dict[str, int]]] | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one section's TTS text. Skip with reason if nothing speakable."""
    units = split_units(section.get("body") or "")
    need = [u for u in units if unit_needs_spoken_form(u["text"])]
    spoken_map, llm_usage, describe_errors = describe_units_en(units=need, chat_fn=chat_fn)

    translate_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    notes: list[str] = list(describe_errors)
    spoken_parts: list[str] = []
    unit_rows: list[dict[str, Any]] = []

    title = (section.get("title") or "").strip()
    if title and title.lower() != "preamble":
        spoken_title = title_for_speech(title)
        if spoken_title:
            spoken_parts.append(spoken_title)

    for unit in units:
        uid = unit["id"]
        raw = unit["text"]
        if unit_needs_spoken_form(raw):
            spoken_en = spoken_map.get(uid)
            if not spoken_en:
                notes.append(f"skipped_unit:{uid}:no_spoken_en")
                unit_rows.append(
                    {
                        "id": uid,
                        "kind": unit["kind"],
                        "status": "skipped",
                        "skip_reason": "no_spoken_en",
                        "text_for_tts": None,
                    }
                )
                continue
            try:
                spoken_tgt, usage = _translate_spoken(
                    spoken_en, target_lang, api_key=api_key, post_fn=post_fn
                )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"skipped_unit:{uid}:translate:{exc}")
                unit_rows.append(
                    {
                        "id": uid,
                        "kind": unit["kind"],
                        "status": "skipped",
                        "skip_reason": f"translate:{exc}",
                        "spoken_en": spoken_en,
                        "text_for_tts": None,
                    }
                )
                continue
            for key in translate_usage:
                translate_usage[key] += int(usage.get(key) or 0)
            line = markdown_to_speech(spoken_tgt)
            spoken_parts.append(line)
            unit_rows.append(
                {
                    "id": uid,
                    "kind": unit["kind"],
                    "status": "ok",
                    "spoken_en": spoken_en,
                    "text_for_tts": line,
                }
            )
        else:
            line = markdown_to_speech(raw)
            if not line:
                continue
            spoken_parts.append(line)
            unit_rows.append(
                {
                    "id": uid,
                    "kind": unit["kind"],
                    "status": "ok",
                    "text_for_tts": line,
                }
            )

    text_for_tts = "\n\n".join(p for p in spoken_parts if p).strip()
    if not text_for_tts:
        return {
            "id": section["id"],
            "slug": section["slug"],
            "title": title,
            "status": "skipped",
            "skip_reason": "empty_section",
            "text_for_tts": None,
            "notes": notes or ["empty_section"],
            "qa": {"cer": None, "transcript": None, "retries": 0},
            "units": unit_rows,
            "llm_usage": llm_usage,
            "translate_usage": translate_usage,
        }

    return {
        "id": section["id"],
        "slug": section["slug"],
        "title": title,
        "status": "ok",
        "skip_reason": None,
        "text_for_tts": text_for_tts,
        "notes": notes,
        "qa": {"cer": None, "transcript": None, "retries": 0},
        "units": unit_rows,
        "llm_usage": llm_usage,
        "translate_usage": translate_usage,
    }


def narrate_lesson_markdown(
    markdown: str,
    target_lang: str,
    *,
    api_key: str | None = None,
    chat_fn: Callable[..., tuple[dict[str, Any], dict[str, int]]] | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
    spoken_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a full narration script object for one language.

    When ``spoken_plan`` is set, hard spans are swapped to tutor snippets
    first; leftover hard units still use the per-unit LLM fallback.
    """
    working_md = markdown
    plan_notes: list[str] = []
    translate_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    used_plan = False

    if spoken_plan and (spoken_plan.get("spans") or []):
        replacements, snip_usage, snip_notes = translate_plan_snippets(
            spoken_plan, target_lang, api_key=api_key, post_fn=post_fn
        )
        for key in translate_usage:
            translate_usage[key] += int(snip_usage.get(key) or 0)
        plan_notes.extend(snip_notes)
        working_md, swap_notes = swap_hard_spans(working_md, replacements)
        plan_notes.extend(swap_notes)
        used_plan = True

    sections_in = split_sections(working_md)
    sections_out: list[dict[str, Any]] = []
    llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # After a successful plan swap, avoid calling the LLM again for leftovers
    # unless something still looks like raw markup.
    effective_chat = chat_fn
    if used_plan and chat_fn is None:
        effective_chat = None

    for section in sections_in:
        row = narrate_section(
            section,
            target_lang,
            api_key=api_key,
            chat_fn=effective_chat,
            post_fn=post_fn,
        )
        for key in llm_usage:
            llm_usage[key] += int((row.get("llm_usage") or {}).get(key) or 0)
        for key in translate_usage:
            translate_usage[key] += int((row.get("translate_usage") or {}).get(key) or 0)
        notes = list(row["notes"] or [])
        if plan_notes and section is sections_in[0]:
            notes = [f"spoken_plan:{n}" for n in plan_notes] + notes
        public = {
            "id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "status": row["status"],
            "skip_reason": row["skip_reason"],
            "text_for_tts": row["text_for_tts"],
            "notes": notes,
            "qa": row["qa"],
            "units": row["units"],
        }
        sections_out.append(public)

    ok = sum(1 for s in sections_out if s["status"] == "ok")
    skipped = sum(1 for s in sections_out if s["status"] == "skipped")
    return {
        "lang": target_lang,
        "sections": sections_out,
        "section_count": len(sections_out),
        "ok": ok,
        "skipped": skipped,
        "llm_usage": llm_usage,
        "translate_usage": translate_usage,
        "used_spoken_plan": used_plan,
    }


def narrate_lesson_file(
    lesson_path: Path,
    out_dir: Path,
    lang: str,
    *,
    api_key: str | None = None,
    chat_fn: Callable[..., tuple[dict[str, Any], dict[str, int]]] | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
    spoken_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write out/{lang}/narration_script.{lang}.json from a lesson markdown file."""
    if not lesson_path.is_file():
        raise FileNotFoundError(f"Missing lesson markdown: {lesson_path}")

    started = time.perf_counter()
    markdown = lesson_path.read_text(encoding="utf-8")
    script = narrate_lesson_markdown(
        markdown,
        lang,
        api_key=api_key,
        chat_fn=chat_fn,
        post_fn=post_fn,
        spoken_plan=spoken_plan,
    )
    lang_dir = out_dir / lang
    lang_dir.mkdir(parents=True, exist_ok=True)
    out_path = lang_dir / f"narration_script.{lang}.json"
    payload = {
        "lang": lang,
        "lesson_path": str(lesson_path),
        "sections": script["sections"],
        "section_count": script["section_count"],
        "ok": script["ok"],
        "skipped": script["skipped"],
        "llm_usage": script["llm_usage"],
        "translate_usage": script["translate_usage"],
        "used_spoken_plan": script.get("used_spoken_plan", False),
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    status = "ok" if script["ok"] else "error"
    return {
        "lang": lang,
        "status": status,
        "path": str(out_path),
        "section_count": script["section_count"],
        "ok": script["ok"],
        "skipped": script["skipped"],
        "llm_usage": script["llm_usage"],
        "translate_usage": script["translate_usage"],
        "used_spoken_plan": script.get("used_spoken_plan", False),
        "latency_s": round(time.perf_counter() - started, 3),
        "error": None if status == "ok" else "no speakable sections",
    }


def narrate_translated_lessons(
    out_dir: Path,
    translations: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    chat_fn: Callable[..., tuple[dict[str, Any], dict[str, int]]] | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
    spoken_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Narrate each successfully translated language; isolate failures."""
    work = [row for row in translations if row.get("lang")]
    total = len(work)
    workers = get_max_lang_workers()
    stage("narrate", f"{total} language(s), workers={min(workers, max(1, total))}")

    def _one(job: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, row = job
        lang = row.get("lang")
        item("narrate", index, total, str(lang))
        if row.get("status") != "ok" or not row.get("path"):
            item("narrate", index, total, str(lang), "skipped")
            return {
                "lang": lang,
                "status": "skipped",
                "path": None,
                "section_count": 0,
                "ok": 0,
                "skipped": 0,
                "llm_usage": {},
                "translate_usage": {},
                "latency_s": 0.0,
                "error": row.get("error") or "translation not ok",
            }
        try:
            result = narrate_lesson_file(
                Path(row["path"]),
                out_dir,
                lang,
                api_key=api_key,
                chat_fn=chat_fn,
                post_fn=post_fn,
                spoken_plan=spoken_plan,
            )
            item(
                "narrate",
                index,
                total,
                str(lang),
                f"{result.get('status')} sections={result.get('section_count')}",
            )
            return result
        except Exception as exc:  # noqa: BLE001 — isolate per language
            item("narrate", index, total, str(lang), f"error: {exc}")
            return {
                "lang": lang,
                "status": "error",
                "path": None,
                "section_count": 0,
                "ok": 0,
                "skipped": 0,
                "llm_usage": {},
                "translate_usage": {},
                "latency_s": 0.0,
                "error": str(exc),
            }

    results = map_parallel(list(enumerate(work, start=1)), _one)
    ok = sum(1 for row in results if row.get("status") == "ok")
    done("narrate", ok=ok, failed=len(results) - ok)
    return results
