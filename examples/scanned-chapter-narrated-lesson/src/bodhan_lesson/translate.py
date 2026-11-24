import time
from pathlib import Path
import requests
from .config import BODHAN_BASE_URL
from .lesson import LessonSection, StructuredLesson

LANG_NAMES = {
    "as": "Assamese",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "kok": "Konkani",
    "ks": "Kashmiri",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}


def normalize_lang(lang: str) -> str:
    """Convert codes like 'hi' to Bodhan's plain ISO code 'hi'."""
    return lang.strip().split("-")[0].lower()


def _translate_with_retry(text: str, lang: str, translate_key: str, max_retries: int = 5) -> str:
    if not text.strip() or lang == "en":
        return text

    lang_name = LANG_NAMES.get(lang, lang)
    delay = 5.0

    for attempt in range(1, max_retries + 1):
        resp = requests.post(
            f"{BODHAN_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {translate_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "indic-translate",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Translate the following text into {lang_name}. "
                            f"Preserve all Markdown formatting and any [[PART_X]] markers exactly. "
                            f"Output only the translation:\n\n{text}"
                        ),
                    }
                ],
                "temperature": 0.0,
            },
            timeout=90,
        )
        if resp.status_code == 429 and attempt < max_retries:
            wait = float(resp.headers.get("Retry-After", delay))
            print(f"[Translate] Rate limited (429). Waiting {wait:.0f}s before retry {attempt}/{max_retries}...")
            time.sleep(wait)
            delay *= 2
            continue
        if not resp.ok:
            raise RuntimeError(f"Translate error {resp.status_code}: {resp.text}")
        return resp.json()["choices"][0]["message"]["content"].strip()

    raise RuntimeError("Translate failed after max retries due to rate limit.")


def _translate_batch(items: list[str], lang: str, translate_key: str) -> list[str]:
    """Translate a list of strings in a single API request using [[PART_i]] markers."""
    if not items:
        return []
    joined = "\n\n".join(f"[[PART_{i}]]\n{item}" for i, item in enumerate(items))
    translated = _translate_with_retry(joined, lang, translate_key)

    results = []
    for i in range(len(items)):
        marker = f"[[PART_{i}]]"
        next_marker = f"[[PART_{i + 1}]]"
        if marker in translated:
            after = translated.split(marker, 1)[1]
            chunk = after.split(next_marker, 1)[0] if next_marker in after else after
            results.append(chunk.strip())
        else:
            # Fallback to single-item call if a marker was altered
            time.sleep(2.0)
            results.append(_translate_with_retry(items[i], lang, translate_key))
    return results


def translate_lesson(
    lesson: StructuredLesson,
    target_lang: str,
    out_dir: Path,
    translate_key: str,
) -> StructuredLesson:
    lang = normalize_lang(target_lang)
    if lang == "en":
        return lesson

    cache_file = out_dir / "cache" / f"lesson_{lang}.json"
    if cache_file.exists():
        print(f"[Translate] Loaded '{lang}' lesson from cache")
        return StructuredLesson.model_validate_json(cache_file.read_text(encoding="utf-8"))

    print(f"[Translate] Translating lesson to '{lang}' via indic-translate (batched)...")

    # Call 1: chapter metadata + practice questions
    meta_items = [lesson.chapter_title, lesson.summary, *lesson.practice_questions]
    meta_out = _translate_batch(meta_items, lang, translate_key)
    tr_title, tr_summary = meta_out[0], meta_out[1]
    tr_questions = meta_out[2:]

    # Call 2: one batched request per section (heading + markdown + narration)
    translated_sections = []
    for sec in lesson.sections:
        time.sleep(1.5)
        sec_out = _translate_batch(
            [sec.heading, sec.clean_markdown, sec.narration_script],
            lang,
            translate_key,
        )
        translated_sections.append(
            LessonSection(
                section_id=sec.section_id,
                heading=sec_out[0],
                clean_markdown=sec_out[1],
                narration_script=sec_out[2],
            )
        )

    translated_lesson = StructuredLesson(
        chapter_title=tr_title,
        summary=tr_summary,
        sections=translated_sections,
        practice_questions=tr_questions,
        low_confidence_notes=lesson.low_confidence_notes,
    )

    cache_file.write_text(translated_lesson.model_dump_json(indent=2), encoding="utf-8")
    return translated_lesson