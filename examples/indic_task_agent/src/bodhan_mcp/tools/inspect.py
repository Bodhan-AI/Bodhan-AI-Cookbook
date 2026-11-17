"""inspect_files: free, local media inspection. Lets a planner see what it has before spending money."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..catalog import PRICING
from ..errors import BodhanToolError
from ..media import classify, load_image, media_info, pdf_page_count, read_text_file
from ..models import FileInfo, InspectResult
from ..text import detect_script

MAX_FILES = 200


def _expand(paths: list[str]) -> tuple[list[Path], list[str]]:
    found: list[Path] = []
    missing: list[str] = []
    for raw in paths:
        p = Path(str(raw).strip()).expanduser()
        if not p.exists():
            missing.append(str(raw))
        elif p.is_dir():
            found.extend(sorted(q for q in p.rglob("*") if q.is_file() and not q.name.startswith(".")))
        else:
            found.append(p)
    return [p.resolve() for p in found][:MAX_FILES], missing


def _inspect_one(path: Path) -> FileInfo:
    info = FileInfo(path=str(path), exists=True, kind=classify(path), size_bytes=path.stat().st_size)
    try:
        if info.kind in {"audio", "video"}:
            m = media_info(path)
            info.duration_s, info.has_audio = m["duration_s"], m["has_audio"]
            if not m["has_audio"]:
                info.notes.append("No audio track: nothing to transcribe.")
            else:
                info.estimated_cost_inr["transcribe"] = round(
                    m["duration_s"] / 60 * PRICING["indic-transcribe"]["per_audio_minute"], 4
                )
        elif info.kind == "pdf":
            info.pages = pdf_page_count(path)
            info.estimated_cost_inr["ocr"] = round(info.pages * PRICING["indic-ocr"]["per_image"], 2)
            info.notes.append("PDF pages are rendered to images and read with Indic-OCR (also for digital PDFs).")
        elif info.kind == "image":
            _, _, blank = load_image(path)
            info.pages = 1
            info.estimated_cost_inr["ocr"] = PRICING["indic-ocr"]["per_image"]
            if blank:
                info.notes.append("Image looks blank.")
        elif info.kind in {"text", "document"}:
            text = read_text_file(path)
            info.text_chars = len(text)
            rep = detect_script(text)
            info.detected_script, info.likely_languages = rep.dominant, rep.likely_languages
            info.estimated_cost_inr["speak"] = round(
                len(text) / 10_000 * PRICING["indic-speak"]["per_10k_input_chars"], 4
            )
            if rep.unsupported_script:
                info.notes.append(f"Text is in {rep.dominant} script, which no Bodhan model supports.")
        else:
            info.notes.append("Unrecognised file type.")
    except BodhanToolError as exc:
        info.notes.append(f"{exc.code}: {exc.message}")
    return info


async def inspect_files(paths: list[str]) -> InspectResult:
    if not paths:
        raise BodhanToolError("invalid_input", "Pass at least one file or directory path.")
    files, missing = _expand(paths)
    infos = await asyncio.gather(*(asyncio.to_thread(_inspect_one, p) for p in files))
    infos = list(infos) + [FileInfo(path=m, exists=False, kind="missing") for m in missing]
    return InspectResult(files=infos, missing=missing)
