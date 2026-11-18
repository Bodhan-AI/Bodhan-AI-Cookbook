"""probe_media and detect_language: the two free tools, so nothing is bought blind."""
from __future__ import annotations

from pathlib import Path

from ..core import costs, media
from ..core.langid import identify
from ..core.schemas import LanguageGuess, MediaInventory, MediaItem

_TYPES = {
    ".wav": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".mp3": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".amr": "audio",
    ".webm": "video",
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".txt": "text",
    ".md": "text",
    ".srt": "text",
    ".csv": "text",
    ".json": "text",
}


def _expand(paths: list[str]) -> list[str]:
    found: list[str] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            found.extend(str(f) for f in sorted(path.iterdir()) if f.is_file() and not f.name.startswith("."))
        else:
            found.append(str(path))
    return found


def _item(path: str) -> MediaItem:
    p = Path(path)
    kind = _TYPES.get(p.suffix.lower(), "unknown")
    if not p.is_file():
        return MediaItem(path=path, kind="unknown", mime="", size_bytes=0, warnings=["missing_file"])
    item = MediaItem(path=str(p.resolve()), kind=kind, mime=f"application/{kind}" if kind != "unknown" else "", size_bytes=p.stat().st_size)
    if kind in ("audio", "video"):
        try:
            info = media.probe_audio(str(p)) if kind == "audio" else media.probe_video(str(p))
        except Exception:
            item.warnings.append("undecodable")
            return item
        item.duration_s = info.get("duration_s")
        item.sample_rate = info.get("sample_rate")
        item.channels = info.get("channels")
        item.has_audio = info.get("has_audio", True) if kind == "video" else True
        if item.duration_s is not None:
            item.est_asr_cost_inr = costs.asr_cost(item.duration_s)
        if item.duration_s and item.duration_s < 0.3:
            item.warnings.append("too_short")
        if kind == "audio" and media.is_silent(str(p)):
            item.warnings.append("silent")
        if kind == "video" and not item.has_audio:
            item.warnings.append("no_audio_track")
    elif kind == "pdf":
        try:
            item.pages = media.pdf_page_count(str(p))
        except Exception as exc:
            item.warnings.append("encrypted" if "encrypt" in str(exc).lower() else "unreadable_pdf")
    elif kind == "text":
        guess = identify(str(p))
        item.script = guess["script"]
        item.language_hint = guess["language"]
        if guess["chars_sampled"] == 0:
            item.warnings.append("empty_file")
    return item


def probe_media(paths: list[str]) -> MediaInventory:
    """Inspect audio, video, image, PDF and text files and report what they are, how long they are and how many pages they hold, so a caller can plan before paying for anything. Handles batches and directories (one level). Detects the script of text files and estimates transcription cost. Free: never calls a model. Missing paths come back as items with a warning instead of failing the whole batch."""
    items = [_item(p) for p in _expand(paths)]
    return MediaInventory(items=items, total_duration_s=round(sum(i.duration_s or 0 for i in items), 3))


def detect_language(text_path: str) -> LanguageGuess:
    """Identify the language of a text file from its writing system. Free and local. Returns a confident language only when the script is unambiguous (Tamil, Telugu, Kannada, Malayalam, Gujarati, Gurmukhi, Odia); for shared scripts such as Devanagari, Bengali or Perso-Arabic it returns candidate codes and low confidence instead of guessing. Never use it to choose a transcription language for audio — omit `language` there and let the model auto-detect."""
    return LanguageGuess(**identify(text_path))
