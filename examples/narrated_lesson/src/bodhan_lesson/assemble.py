"""Assemble cleaned OCR pages into one source lesson markdown.

Joins pages in order, formats blocks by label, and lightly rejoins
sentences split across page breaks. Never invents text.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from bodhan_lesson.progress import done, stage

_TERMINAL_END_RE = re.compile(r"[.!?…”\"')\]]\s*$")
_LOWER_START_RE = re.compile(r"^[a-z]")
_HYPHEN_BREAK_RE = re.compile(r"-\s*$")


def list_clean_pages(pages_dir: Path) -> list[Path]:
    """Return page_*.clean.json paths in name order."""
    files = [
        path
        for path in pages_dir.iterdir()
        if path.is_file()
        and path.name.startswith("page_")
        and path.name.endswith(".clean.json")
    ]
    return sorted(files, key=lambda path: path.name.lower())


def load_clean_page(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"bad clean artifact: {path}")
    return data


def block_to_markdown(block: dict[str, Any]) -> str | None:
    """Render one cleaned block as markdown. Skip empty non-text slots."""
    text = (block.get("text") or "").strip()
    if not text:
        return None

    label = block.get("label") or ""
    if label in {"Chapter-title", "Title"}:
        # Keep OCR wording; mark as heading for later stages.
        return f"# {text}"
    if label in {"Section-title"}:
        return f"## {text}"
    if label in {"Image-caption", "Caption"}:
        return text
    if label in {"Equation"}:
        return text
    if label in {"Table"}:
        return text
    # Paragraph, List-item, and anything else with text.
    return text


def format_page_blocks(blocks: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Format a page's blocks; return markdown and a trace list."""
    parts: list[str] = []
    trace: list[dict[str, Any]] = []
    for block in blocks:
        md = block_to_markdown(block)
        if md is None:
            continue
        parts.append(md)
        trace.append(
            {
                "id": block.get("id"),
                "label": block.get("label"),
                "flags": block.get("flags") or [],
            }
        )
    return "\n\n".join(parts), trace


def should_join_across_pages(prev_text: str, next_text: str) -> bool:
    """Heuristic: true when the next page likely continues the same sentence."""
    prev = (prev_text or "").rstrip()
    nxt = (next_text or "").lstrip()
    if not prev or not nxt:
        return False

    # Soft hyphen / cut word at page end → join tightly.
    if _HYPHEN_BREAK_RE.search(prev):
        return True

    # Ends with sentence punctuation → new paragraph.
    if _TERMINAL_END_RE.search(prev):
        return False

    # Continues mid-sentence (lowercase) or after comma/semicolon.
    if prev[-1] in {",", ";", ":"}:
        return True
    if _LOWER_START_RE.match(nxt):
        return True
    return False


def join_page_markdown(parts: list[str]) -> str:
    """Join per-page markdown with optional mid-sentence stitching."""
    if not parts:
        return ""

    out = parts[0].rstrip()
    for chunk in parts[1:]:
        chunk = chunk.strip()
        if not chunk:
            continue
        if should_join_across_pages(out, chunk):
            if _HYPHEN_BREAK_RE.search(out):
                out = _HYPHEN_BREAK_RE.sub("", out) + chunk
            else:
                out = out + " " + chunk
        else:
            out = out + "\n\n" + chunk
    if out and not out.endswith("\n"):
        out += "\n"
    return out


def assemble_lesson(
    pages_dir: Path,
    source_dir: Path | None = None,
) -> dict[str, Any]:
    """Build lesson.src.md (+ assembly.json) from cleaned pages.

    Partial success: missing/broken pages are recorded; others still assemble.
    """
    started = time.perf_counter()
    source_dir = source_dir or pages_dir.parent
    clean_paths = list_clean_pages(pages_dir)
    stage("assemble", f"{len(clean_paths)} clean page(s)")

    page_sections: list[str] = []
    assembly_pages: list[dict[str, Any]] = []
    gaps: list[str] = []

    if not clean_paths:
        gaps.append(f"No page_*.clean.json under {pages_dir}")

    for path in clean_paths:
        stem = path.name[: -len(".clean.json")]
        try:
            artifact = load_clean_page(path)
            blocks = artifact.get("blocks") or []
            if not isinstance(blocks, list):
                raise ValueError("blocks must be a list")
            md, trace = format_page_blocks(blocks)
            if md:
                page_sections.append(md)
            assembly_pages.append(
                {
                    "stem": stem,
                    "page_image": artifact.get("page_image"),
                    "status": "ok",
                    "block_count": len(trace),
                    "repair": artifact.get("repair"),
                    "blocks": trace,
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001 — isolate per page
            gaps.append(f"{stem}: {exc}")
            assembly_pages.append(
                {
                    "stem": stem,
                    "page_image": None,
                    "status": "error",
                    "block_count": 0,
                    "repair": None,
                    "blocks": [],
                    "error": str(exc),
                }
            )

    lesson_md = join_page_markdown(page_sections)
    lesson_path = source_dir / "lesson.src.md"
    assembly_path = source_dir / "assembly.json"

    lesson_path.write_text(lesson_md, encoding="utf-8")

    ok_pages = sum(1 for row in assembly_pages if row["status"] == "ok")
    failed_pages = sum(1 for row in assembly_pages if row["status"] == "error")
    status = "ok"
    if not ok_pages:
        status = "error"
    elif failed_pages or gaps:
        status = "partial"

    assembly = {
        "status": status,
        "lesson_path": str(lesson_path),
        "page_count": len(assembly_pages),
        "ok": ok_pages,
        "failed": failed_pages,
        "gaps": gaps,
        "pages": assembly_pages,
        "latency_s": round(time.perf_counter() - started, 3),
    }
    assembly_path.write_text(
        json.dumps(assembly, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    done("assemble", status=status, ok=ok_pages, failed=failed_pages, chars=len(lesson_md))

    return {
        "status": status,
        "lesson_path": str(lesson_path),
        "assembly_path": str(assembly_path),
        "page_count": len(assembly_pages),
        "ok": ok_pages,
        "failed": failed_pages,
        "gaps": gaps,
        "char_count": len(lesson_md),
        "latency_s": assembly["latency_s"],
        "error": None if ok_pages else (gaps[0] if gaps else "assemble failed"),
    }
