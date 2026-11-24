"""inspect_media: what a file is, how long / big it is, and what it would cost to process. Free, no model call."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from bodhan_core.errors import ToolError, check_input_file
from bodhan_core.media import details, estimate_inr, kind_of

_NOTES = {
    "video": "Bodhan has no video tool. The audio track can be transcribed with transcribe_audio (needs ffmpeg); nothing can be dubbed or re-rendered.",
    "other": "No Bodhan tool handles this file type. Supported: audio (transcribe_audio), PNG/JPEG/PDF (ocr_document), text (read_text_file).",
    "image": "ocr_document reads .png, .jpg and .jpeg only; convert other image formats first.",
}


class MediaInfo(BaseModel):
    ok: bool
    path: str | None = None
    kind: str | None = None  # audio | video | image | pdf | text | other
    size_bytes: int = 0
    duration_s: float | None = None  # audio / video
    pages: int | None = None  # PDF
    width: int | None = None  # image
    height: int | None = None
    suggested_tool: str | None = None  # the Bodhan tool that reads this file
    est_cost_inr: float | None = None  # cost of that tool on the whole file
    note: str | None = None
    error: ToolError | None = None


def inspect_media(file_path: str) -> MediaInfo:
    path = Path(file_path)
    if err := check_input_file(path, "file_path", "audio, video, image, PDF or text"):
        return MediaInfo(ok=False, error=err)
    kind = kind_of(path)
    info = details(path, kind)
    est = estimate_inr(kind, info)
    tool = {"audio": "transcribe_audio", "video": "transcribe_audio", "image": "ocr_document", "pdf": "ocr_document", "text": "read_text_file"}.get(kind)
    return MediaInfo(
        ok=True,
        path=str(path.resolve()),
        kind=kind,
        size_bytes=path.stat().st_size,
        suggested_tool=tool,
        est_cost_inr=est.get("est_cost_inr", 0.0 if kind == "text" else None),
        note=_NOTES.get(kind),
        **info,
    )
