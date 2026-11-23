"""Media inspection and language/script identification helper tools."""

from __future__ import annotations

import contextlib
import mimetypes
import os
import re
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

from ..client.client import VOICE_CATALOG, SUPPORTED_LANGUAGES
from ..client.pricing import (
    calculate_ocr_cost,
    calculate_speak_cost,
    calculate_transcribe_cost,
)
from ..models import LanguageInfo, MediaInfo, VoiceInfo

# Unicode script ranges for Indian languages
SCRIPT_RANGES: List[Tuple[str, str, int, int]] = [
    ("hi", "Devanagari", 0x0900, 0x097F),
    ("bn", "Bengali", 0x0980, 0x09FF),
    ("pa", "Gurmukhi", 0x0A00, 0x0A7F),
    ("gu", "Gujarati", 0x0A80, 0x0AFF),
    ("or", "Odia", 0x0B00, 0x0B7F),
    ("ta", "Tamil", 0x0B80, 0x0BFF),
    ("te", "Telugu", 0x0C00, 0x0C7F),
    ("kn", "Kannada", 0x0C80, 0x0CFF),
    ("ml", "Malayalam", 0x0D00, 0x0D7F),
    ("sat", "Ol Chiki", 0x1C50, 0x1C7F),
    ("mni", "Meetei Mayek", 0xABC0, 0xABFF),
    ("ur", "Perso-Arabic", 0x0600, 0x06FF),
    ("en", "Latin", 0x0041, 0x007A),
]

_URDU_SPECIFIC_CHARS = set("ٹڈڑۓےہںگپچژھ")
_ARABIC_SPECIFIC_CHARS = set("ةىأإآؤئذثضظ")

LANGUAGE_NAMES: Dict[str, str] = {
    "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri", "en": "English",
    "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri", "mr": "Marathi", "ne": "Nepali",
    "or": "Odia", "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi",
    "ta": "Tamil", "te": "Telugu", "ur": "Urdu", "fr": "French", "es": "Spanish",
}

AVAILABLE_STYLES = [
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger",
    "children's stories", "disgust", "educational lecture", "fear", "happy", "news",
    "sad", "single person narration audiobook", "surprise"
]


def bodhan_inspect_media(file_path: str) -> MediaInfo:
    """Inspect a file or directory to get dimensions, duration, format, char count, and cost estimates."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Directory inspection
    if path.is_dir():
        files = sorted([p for p in path.rglob("*") if p.is_file() and not p.name.startswith(".")])
        total_bytes = sum(p.stat().st_size for p in files)
        preview = ", ".join(p.name for p in files[:10])
        more = f" (+{len(files) - 10} more)" if len(files) > 10 else ""
        return MediaInfo(
            path=str(path),
            media_type="directory",
            size_bytes=total_bytes,
            page_count=len(files),
            format="dir",
            notes=f"Directory with {len(files)} file(s): {preview}{more}",
        )

    size_bytes = path.stat().st_size
    suffix = path.suffix.lower()
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"

    # Audio inspection
    if suffix in (".wav", ".mp3", ".ogg", ".flac", ".m4a") or mime.startswith("audio/"):
        duration = None
        sample_rate = None
        channels = None
        if suffix == ".wav":
            try:
                with contextlib.closing(wave.open(str(path), "rb")) as wf:
                    channels = wf.getnchannels()
                    sample_rate = wf.getframerate()
                    frames = wf.getnframes()
                    duration = round(frames / float(sample_rate), 2) if sample_rate > 0 else None
            except Exception:
                pass
        est_cost = calculate_transcribe_cost(duration or 0.0) if duration else None
        cost_note = f" | est. transcribe cost: ₹{est_cost:.4f}" if est_cost is not None else ""
        return MediaInfo(
            path=str(path),
            media_type="audio",
            size_bytes=size_bytes,
            duration_seconds=duration,
            sample_rate=sample_rate,
            channels=channels,
            format=suffix.lstrip("."),
            notes=f"Audio file ready for Indic-Transcribe{cost_note}",
        )

    # Video inspection
    if suffix in (".mp4", ".mov", ".mkv", ".avi", ".webm") or mime.startswith("video/"):
        return MediaInfo(
            path=str(path),
            media_type="video",
            size_bytes=size_bytes,
            format=suffix.lstrip("."),
            notes="Video container file",
        )

    # PDF inspection
    if suffix == ".pdf" or mime == "application/pdf":
        page_count = 1
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(path))
            page_count = len(pdf)
        except Exception:
            try:
                import fitz
                doc = fitz.open(str(path))
                page_count = len(doc)
            except Exception:
                pass
        est_ocr = calculate_ocr_cost(page_count)
        return MediaInfo(
            path=str(path),
            media_type="pdf",
            size_bytes=size_bytes,
            page_count=page_count,
            format="pdf",
            notes=f"PDF ({page_count} page(s)) ready for Indic-OCR | est. OCR cost: ₹{est_ocr:.4f}",
        )

    # Image inspection
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff") or mime.startswith("image/"):
        width, height = None, None
        try:
            with Image.open(path) as img:
                width, height = img.size
        except Exception:
            pass
        return MediaInfo(
            path=str(path),
            media_type="image",
            size_bytes=size_bytes,
            width=width,
            height=height,
            format=suffix.lstrip("."),
            notes="Image ready for Indic-OCR",
        )

    # Text / Markdown inspection
    if suffix in (".txt", ".md", ".json", ".csv", ".srt", ".vtt"):
        char_count = 0
        lang_hint = ""
        try:
            txt = path.read_text(encoding="utf-8", errors="replace")
            char_count = len(txt)
            lang_info = bodhan_detect_language(text=txt[:2000])
            lang_hint = f" | lang={lang_info.code} ({lang_info.script})"
        except Exception:
            pass
        est_speak = calculate_speak_cost(char_count)
        return MediaInfo(
            path=str(path),
            media_type="text",
            size_bytes=size_bytes,
            format=suffix.lstrip("."),
            notes=f"Text file ({char_count} chars{lang_hint}) | est. Indic-Speak cost: ₹{est_speak:.4f}",
        )

    return MediaInfo(
        path=str(path),
        media_type="unknown",
        size_bytes=size_bytes,
        format=suffix.lstrip("."),
        notes="Unrecognized media format",
    )


def bodhan_detect_language(text: Optional[str] = None, file_path: Optional[str] = None) -> LanguageInfo:
    """Identify Indic script and language from text or file content using Unicode character distribution."""
    content = ""
    if file_path:
        p = Path(file_path).resolve()
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8")[:5000]
            except Exception:
                pass
    elif text:
        content = text[:5000]

    if not content:
        return LanguageInfo(
            code="unknown",
            name="Unknown",
            script="Unknown",
            confidence=0.0,
            is_indic=False,
            is_supported=False,
        )

    counts: Dict[str, int] = {}
    script_names: Dict[str, str] = {}
    for code, script_name, start, end in SCRIPT_RANGES:
        match_count = sum(1 for char in content if start <= ord(char) <= end)
        if match_count > 0:
            counts[code] = match_count
            script_names[code] = script_name

    if not counts:
        # Check if there are alphabetic characters in unsupported scripts (CJK, Cyrillic, Greek, etc.)
        has_other_alpha = any(ch.isalpha() for ch in content)
        if has_other_alpha:
            return LanguageInfo(
                code="unknown",
                name="Unsupported Script",
                script="Non-Indic/Non-Latin",
                confidence=0.0,
                is_indic=False,
                is_supported=False,
            )
        return LanguageInfo(
            code="en",
            name="English",
            script="Latin",
            confidence=0.5,
            is_indic=False,
            is_supported=True,
        )

    best_code = max(counts, key=counts.get)
    total_matched = sum(counts.values())
    confidence = round(counts[best_code] / float(total_matched), 2) if total_matched > 0 else 0.5
    script = script_names.get(best_code, "Unknown")

    # Distinguish Arabic from Urdu when Perso-Arabic script lacks Urdu-specific letters
    if best_code == "ur":
        has_urdu = any(ch in _URDU_SPECIFIC_CHARS for ch in content)
        has_arabic_only = any(ch in _ARABIC_SPECIFIC_CHARS for ch in content)
        if has_arabic_only and not has_urdu:
            return LanguageInfo(
                code="ar",
                name="Arabic",
                script="Arabic",
                confidence=confidence,
                is_indic=False,
                is_supported=False,
            )

    name = LANGUAGE_NAMES.get(best_code, best_code.upper())
    is_supported = best_code in SUPPORTED_LANGUAGES

    return LanguageInfo(
        code=best_code,
        name=name,
        script=script,
        confidence=confidence,
        is_indic=best_code != "en",
        is_supported=is_supported,
    )


def bodhan_list_voices(language: Optional[str] = None) -> List[VoiceInfo]:
    """List available voices and styles for a language from the official Indic-Speak voice library."""
    results = []
    target_langs = [language.lower()] if language and language.lower() in VOICE_CATALOG else list(VOICE_CATALOG.keys())

    for lang in target_langs:
        gender_map = VOICE_CATALOG.get(lang, {})
        for gender, names in gender_map.items():
            for name in names:
                results.append(
                    VoiceInfo(
                        name=name,
                        language=lang,
                        gender=gender,
                        styles=AVAILABLE_STYLES,
                    )
                )
    return results


INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+override", re.IGNORECASE),
    re.compile(r"do\s+not\s+translate", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"पिछले\s+सभी\s+निर्देश\s+भूल", re.IGNORECASE),
]


def bodhan_read_text(
    file_path: str,
    offset_chars: int = 0,
    max_chars: int = 4000,
) -> Dict[str, object]:
    """Read a bounded slice of a text/Markdown/transcript file marked untrusted with injection warnings."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Text file not found: {file_path}")

    full_text = path.read_text(encoding="utf-8", errors="replace")
    total_chars = len(full_text)
    start = max(0, int(offset_chars))
    end = min(total_chars, start + max(1, int(max_chars)))
    chunk = full_text[start:end]

    warnings: List[str] = []
    for pat in INJECTION_PATTERNS:
        if pat.search(chunk):
            warnings.append(f"Potential prompt-injection pattern matched: '{pat.pattern}'")

    return {
        "path": str(path),
        "untrusted": True,
        "offset_chars": start,
        "returned_chars": len(chunk),
        "total_chars": total_chars,
        "has_more": end < total_chars,
        "injection_warnings": warnings,
        "content": chunk,
    }
