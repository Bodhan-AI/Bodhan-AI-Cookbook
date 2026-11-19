"""
Media inspection and language detection helper tools.
"""

import os
import wave
from typing import Optional
from ..models import (
    InspectMediaInput,
    MediaInspectionResult,
    DetectLanguageInput,
    LanguageDetectionResult,
    SUPPORTED_LANGUAGES,
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    DOC_EXTENSIONS,
)
from ..client import BodhanClient


def run_inspect_media(args: InspectMediaInput) -> MediaInspectionResult:
    """Inspect media file to extract technical properties without reading into LLM context."""
    if not os.path.exists(args.file_path):
        raise FileNotFoundError(f"Media file not found: {args.file_path}")

    ext = os.path.splitext(args.file_path)[1].lower()
    size_bytes = os.path.getsize(args.file_path)

    res = MediaInspectionResult(
        file_path=args.file_path,
        file_type="unknown",
        file_size_bytes=size_bytes,
        details={},
    )

    if ext in AUDIO_EXTENSIONS:
        res.file_type = "audio"
        try:
            import soundfile as sf
            info = sf.info(args.file_path)
            res.duration_seconds = round(float(info.duration), 2)
            res.sample_rate = int(info.samplerate)
            res.channels = int(info.channels)
            res.details["format"] = info.format
            res.details["subtype"] = info.subtype
        except Exception:
            try:
                with wave.open(args.file_path, "rb") as w:
                    frames = w.getnframes()
                    rate = w.getframerate()
                    res.duration_seconds = round(frames / float(rate), 2)
                    res.sample_rate = rate
                    res.channels = w.getnchannels()
            except Exception:
                res.duration_seconds = max(1.0, round(size_bytes / 32000.0, 2))

    elif ext in IMAGE_EXTENSIONS:
        res.file_type = "image"
        try:
            from PIL import Image
            with Image.open(args.file_path) as img:
                res.width, res.height = img.size
                res.details["format"] = img.format
                res.details["mode"] = img.mode
        except Exception:
            pass

    elif ext in DOC_EXTENSIONS:
        res.file_type = "pdf"
        try:
            import pypdf
            reader = pypdf.PdfReader(args.file_path)
            res.page_count = len(reader.pages)
        except Exception:
            res.page_count = 1

    elif ext in {".txt", ".md", ".json", ".srt", ".csv"}:
        res.file_type = "text"

    return res


def run_detect_language(args: DetectLanguageInput, client: Optional[BodhanClient] = None) -> LanguageDetectionResult:
    """Detect language and script from text or audio snippet."""
    if args.text:
        text = args.text
        # Analyze unicode character scripts
        script_counts = {
            "hi": 0,  # Devanagari (Hindi/Marathi)
            "bn": 0,  # Bengali
            "ta": 0,  # Tamil
            "te": 0,  # Telugu
            "kn": 0,  # Kannada
            "ml": 0,  # Malayalam
            "gu": 0,  # Gujarati
            "pa": 0,  # Gurmukhi
            "or": 0,  # Odia
            "ur": 0,  # Urdu
            "en": 0,  # Latin
        }

        for ch in text:
            code = ord(ch)
            if 0x0900 <= code <= 0x097F:
                script_counts["hi"] += 1
            elif 0x0980 <= code <= 0x09FF:
                script_counts["bn"] += 1
            elif 0x0B80 <= code <= 0x0BFF:
                script_counts["ta"] += 1
            elif 0x0C00 <= code <= 0x0C7F:
                script_counts["te"] += 1
            elif 0x0C80 <= code <= 0x0CFF:
                script_counts["kn"] += 1
            elif 0x0D00 <= code <= 0x0D7F:
                script_counts["ml"] += 1
            elif 0x0A80 <= code <= 0x0AFF:
                script_counts["gu"] += 1
            elif 0x0A00 <= code <= 0x0A7F:
                script_counts["pa"] += 1
            elif 0x0B00 <= code <= 0x0B7F:
                script_counts["or"] += 1
            elif 0x0600 <= code <= 0x06FF:
                script_counts["ur"] += 1
            elif 0x0041 <= code <= 0x007A:
                script_counts["en"] += 1

        top_lang = max(script_counts, key=script_counts.get)
        top_count = script_counts[top_lang]

        if top_count == 0:
            return LanguageDetectionResult(
                detected_lang="en",
                language_name="English",
                confidence=0.5,
                is_indic=False,
                is_supported=True,
                method="script_analysis",
            )

        confidence = round(min(1.0, top_count / max(1, sum(script_counts.values()))), 2)
        is_indic = top_lang != "en"
        return LanguageDetectionResult(
            detected_lang=top_lang,
            language_name=SUPPORTED_LANGUAGES.get(top_lang, top_lang),
            confidence=confidence,
            is_indic=is_indic,
            is_supported=True,
            method="script_analysis",
        )

    elif args.audio_path and client:
        # Run probe transcription
        res = client.transcribe(args.audio_path, duration_seconds=5.0)
        lang = res.get("language", "hi")
        is_supported = lang in SUPPORTED_LANGUAGES
        return LanguageDetectionResult(
            detected_lang=lang,
            language_name=SUPPORTED_LANGUAGES.get(lang, lang),
            confidence=0.85,
            is_indic=lang != "en",
            is_supported=is_supported,
            method="transcribe_probe",
        )

    return LanguageDetectionResult(
        detected_lang="en",
        language_name="English",
        confidence=0.5,
        is_indic=False,
        is_supported=True,
        method="default_fallback",
    )
