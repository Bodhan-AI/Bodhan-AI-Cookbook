from pathlib import Path
from .lesson import StructuredLesson
from .translate import normalize_lang


def write_markdown_lesson(
    lesson: StructuredLesson,
    target_lang: str,
    audio_paths: dict[str, Path],
    out_dir: Path,
) -> Path:
    lang = normalize_lang(target_lang)
    md_path = out_dir / f"lesson_{lang}.md"

    lines = [
        f"# {lesson.chapter_title}",
        "",
        f"**Language:** `{lang}`  ",
        f"**Full Chapter Audio:** [{audio_paths['full'].name}](audio_{lang}/{audio_paths['full'].name})",
        "",
        "## Chapter Summary",
        lesson.summary,
        "",
        "---",
        "",
    ]

    for sec in lesson.sections:
        sec_audio = audio_paths.get(sec.section_id)
        lines.append(f"## {sec.heading}")
        if sec_audio:
            lines.append(f"*Audio Narration:* [{sec_audio.name}](audio_{lang}/{sec_audio.name})")
        lines.extend(["", sec.clean_markdown, "", "---", ""])

    if lesson.practice_questions:
        lines.append("## Practice Questions")
        for idx, q in enumerate(lesson.practice_questions, 1):
            lines.append(f"{idx}. {q}")
        lines.append("")

    if lesson.low_confidence_notes:
        lines.append("## OCR Quality Notes (Review Recommended)")
        for note in lesson.low_confidence_notes:
            lines.append(f"- {note}")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path