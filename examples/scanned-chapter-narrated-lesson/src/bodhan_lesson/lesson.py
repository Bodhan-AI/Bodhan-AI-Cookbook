import json
import re
from pathlib import Path
from openai import OpenAI
from pydantic import BaseModel, Field
from .config import Settings


class LessonSection(BaseModel):
    section_id: str = Field(description="Short ID like sec_1, sec_2")
    heading: str = Field(description="Clear section heading")
    clean_markdown: str = Field(
        description="Cleaned, well-formatted Markdown study notes for this section"
    )
    narration_script: str = Field(
        description="Natural, conversational spoken teacher narration script for this section"
    )


class StructuredLesson(BaseModel):
    chapter_title: str
    summary: str
    sections: list[LessonSection]
    practice_questions: list[str]
    low_confidence_notes: list[str] = Field(
        default_factory=list,
        description="Any OCR artifacts, missing figures, or unclear equations flagged for review",
    )


SYSTEM_PROMPT = """You are an expert school teacher creating a clean study guide and spoken audio lesson from scanned textbook OCR pages.

Return ONLY valid JSON matching this exact schema:
{
  "chapter_title": "string",
  "summary": "string",
  "sections": [
    {
      "section_id": "sec_1",
      "heading": "string",
      "clean_markdown": "string",
      "narration_script": "string"
    }
  ],
  "practice_questions": ["string"],
  "low_confidence_notes": ["string"]
}

Rules:
1. Clean up OCR noise, broken line wraps, and garbled symbols while preserving exact textbook definitions, theorems, proofs, and worked examples.
2. Prefer clean Unicode math symbols (∠, △, °, ⊥, ∥, =, +, −, ×, ÷) instead of raw LaTeX backslashes so JSON and translations remain clean.
3. Break the content into logical teaching sections (sec_1, sec_2, ...).
4. For each section, write:
   - `clean_markdown`: clear, structured study notes in Markdown.
   - `narration_script`: a warm, clear spoken explanation as if a teacher is reading and explaining the concepts aloud (spell out symbols naturally for speech, e.g., say "angle ABC" instead of "∠ABC").
5. Include 3-5 practice questions based on the pages and note any unreadable OCR parts in `low_confidence_notes`.
"""


def _sanitize_json_backslashes(raw: str) -> str:
    """Strip markdown code fences if present and escape stray single backslashes."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL).strip()
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", cleaned)


def build_lesson_from_ocr(
    ocr_pages: list[dict],
    out_dir: Path,
    settings: Settings,
) -> StructuredLesson:
    cache_file = out_dir / "cache" / "structured_lesson.json"
    if cache_file.exists():
        print("[LLM] Loaded structured lesson from cache")
        return StructuredLesson.model_validate_json(cache_file.read_text(encoding="utf-8"))

    print(f"[LLM] Structuring lesson & narration script with {settings.llm_model}...")
    combined_ocr = "\n\n".join(
        f"--- PAGE {p['page']} ---\n{p['markdown']}" for p in ocr_pages
    )

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Convert these scanned textbook pages into a structured lesson:\n\n{combined_ocr}",
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    raw_json = resp.choices[0].message.content or ""
    raw_json = _sanitize_json_backslashes(raw_json)
    lesson = StructuredLesson.model_validate_json(raw_json)

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(lesson.model_dump_json(indent=2), encoding="utf-8")
    return lesson