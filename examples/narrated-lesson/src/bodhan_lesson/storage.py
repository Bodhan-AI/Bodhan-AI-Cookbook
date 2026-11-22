"""Reading and writing stage outputs under the output directory."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bodhan_lesson.models import FigureDescription, Page, SectionNarration
from bodhan_lesson.stages.assemble import Lesson


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_pages(pages: list[Page], pages_dir: Path) -> None:
    """Write one `page_NNN.json` per page: cleaned blocks, flags and dropped blocks."""
    for page in pages:
        write_json(pages_dir / f"page_{page.number:03d}.json", page.to_dict())


def save_lesson(lesson: Lesson, source_dir: Path) -> None:
    """Write the lesson transcript plus its traceability map and section list."""
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "lesson.src.md").write_text(lesson.markdown, encoding="utf-8")
    write_json(source_dir / "lesson.src.map.json", lesson.element_map())
    write_json(source_dir / "sections.json", [asdict(section) for section in lesson.sections])


def save_figures(figures: dict[str, FigureDescription], source_dir: Path) -> None:
    write_json(source_dir / "figures.json", [asdict(f) for f in figures.values()])


def save_narration(sections: list[SectionNarration], stats: object, source_dir: Path) -> None:
    """Write the English narration script: machine-readable JSON and a readable Markdown copy."""
    write_json(source_dir / "narration_script.en.json", {
        "language": "en", "ai_generated": True, "stats": asdict(stats),  # type: ignore[call-overload]
        "sections": [asdict(s) for s in sections]})
    lines = ["# Narration script (English)", ""]
    for section in sections:
        lines += [f"## {section.title}", ""]
        for unit in section.units:
            lines += [f"**{unit.id}** `{unit.kind}` {', '.join(unit.element_ids)}"
                      + (f" - {'; '.join(unit.warnings)}" if unit.warnings else ""), "", unit.text, ""]
        lines += [f"_Skipped {s['element']}: {s['reason']}_" for s in section.skipped] + [""] * bool(section.skipped)
    (source_dir / "narration_script.en.md").write_text("\n".join(lines), encoding="utf-8")
