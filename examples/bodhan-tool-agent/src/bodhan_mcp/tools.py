"""Bodhan MCP Tool definitions and implementations.

All tools:
- Have typed input and output schemas
- Use file paths for media (never base64 in model context)
- Handle model limits internally (audio chunking <= 30s, TTS <= 150 chars, PDF page splitting)
- Enforce budget and disk caching
- Return structured, actionable error dictionaries rather than raising unhandled exceptions
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

from bodhan_mcp.cache import DiskCache
from bodhan_mcp.client import BodhanAPIError, BodhanClient
from bodhan_mcp.config import Settings, load_settings
from bodhan_mcp.ffmpeg_utils import (
    convert_to_wav_16k_mono,
    extract_audio_from_video as ffmpeg_extract_audio,
    get_audio_duration,
    join_audio_files,
    split_audio_at_silence,
)
from bodhan_mcp.lang_utils import (
    SUPPORTED_INDIC_LANGUAGES,
    detect_script_language,
    is_language_supported,
)
from bodhan_mcp.pdf_utils import render_pdf_to_images
from bodhan_mcp.pricing import (
    BudgetManager,
    calculate_ocr_cost,
    calculate_speak_cost,
    calculate_transcribe_cost,
    calculate_translate_cost,
)

# Default voice catalog per language from bodhan_api_rules.json
DEFAULT_VOICES: dict[str, str] = {
    "hi": "Amit", "ta": "Anitha", "te": "Sravani", "bn": "Sourav",
    "mr": "Anagha", "gu": "Dhara", "kn": "Deepika", "ml": "Lakshmi",
    "pa": "Manpreet", "or": "Itishree", "as": "Prastuti", "ur": "Zaid",
    "sa": "Bharati", "ne": "Srijana", "ks": "Zoon", "doi": "Preeti",
    "brx": "Gwrbw", "kok": "Anjali", "mai": "Vaidehi", "mni": "Thoibi",
    "sat": "Phulmani", "sd": "Moomal", "en": "Amit",
}


def _split_text_by_length(text: str, max_chars: int = 150) -> list[str]:
    """Splits text into chunks <= max_chars respecting sentence/clause boundaries."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    # Split by standard sentence delimiters (English and Indic danda)
    sentence_delims = re.split(r"([.!?।\n]+)", text)
    sentences = []
    i = 0
    while i < len(sentence_delims):
        part = sentence_delims[i]
        punct = sentence_delims[i + 1] if i + 1 < len(sentence_delims) else ""
        combined = (part + punct).strip()
        if combined:
            sentences.append(combined)
        i += 2

    chunks = []
    current_chunk = ""

    for s in sentences:
        if len(s) > max_chars:
            # Sentence itself is longer than limit, split on commas or spaces
            words = s.split(" ")
            sub_chunk = ""
            for w in words:
                if len(sub_chunk) + len(w) + 1 <= max_chars:
                    sub_chunk = (sub_chunk + " " + w).strip()
                else:
                    if sub_chunk:
                        chunks.append(sub_chunk)
                    sub_chunk = w
            if sub_chunk:
                chunks.append(sub_chunk)
        elif len(current_chunk) + len(s) + 1 <= max_chars:
            current_chunk = (current_chunk + " " + s).strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = s

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def format_srt_timestamp(seconds: float) -> str:
    """Formats float seconds into standard SubRip timestamp (HH:MM:SS,mmm)."""
    seconds = max(0.0, seconds)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    total_secs = int(seconds)
    hours = total_secs // 3600
    minutes = (total_secs % 3600) // 60
    secs = total_secs % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


class BodhanTools:
    """Core tool implementations exposing Bodhan Indic AI models."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: BodhanClient | None = None,
        cache: DiskCache | None = None,
        budget: BudgetManager | None = None,
    ):
        self.settings = settings or load_settings()
        self.cache = cache or DiskCache()
        self.budget = budget or BudgetManager()
        self.client = client or BodhanClient(settings=self.settings, cache=self.cache, budget=self.budget)

    # ------------------------------------------------------------------------
    # 1. transcribe_audio
    # ------------------------------------------------------------------------
    def transcribe_audio(self, file_path: str, language: str | None = None) -> dict[str, Any]:
        """Transcribes audio using Indic-Transcribe.

        Converts any audio format to 16 kHz mono WAV, splits into <= 30s windows,
        and provides full transcript and per-window results with quality flags.
        """
        path = Path(file_path)
        if not path.exists():
            return {
                "success": False,
                "error": f"Audio file not found: {file_path}",
                "actionable_tip": "Verify the file path exists on disk.",
            }

        try:
            # 1. Convert to 16 kHz mono WAV
            temp_wav = path.parent / f"{path.stem}_converted_16k.wav"
            converted_path = convert_to_wav_16k_mono(path, temp_wav)
            total_duration = get_audio_duration(converted_path)

            # 2. Window splitting (<= 30s)
            windows = split_audio_at_silence(converted_path, max_window_s=30.0)

            window_results: list[dict[str, Any]] = []
            combined_texts: list[str] = []
            total_cost = 0.0
            flagged_count = 0

            current_time = 0.0
            for idx, win_path in enumerate(windows):
                win_dur = get_audio_duration(win_path)
                win_start = current_time
                win_end = current_time + win_dur
                current_time = win_end

                # Cache check
                cache_key = self.cache.generate_key("indic-transcribe", win_path.read_bytes(), lang=language or "auto")
                cached = self.cache.get_json(cache_key)

                if cached is not None:
                    win_text = cached.get("text", "")
                    win_cost = 0.0
                else:
                    win_cost = calculate_transcribe_cost(win_dur)
                    self.budget.check_budget(win_cost)

                    with open(win_path, "rb") as f:
                        data = {"model": "indic-transcribe", "vad": "true"}
                        if language:
                            data["language"] = language
                        files = {"file": (win_path.name, f, "audio/wav")}
                        resp = self.client.post("/v1/audio/transcriptions", model_id="indic-transcribe", data=data, files=files, timeout=90.0)

                    resp_data = resp.json()
                    win_text = resp_data.get("text", "").strip()
                    self.cache.set_json(cache_key, {"text": win_text, "duration": win_dur})
                    self.budget.record_cost("indic-transcribe", win_cost, f"window_{idx}")

                total_cost += win_cost
                is_empty = len(win_text.strip()) == 0
                suspicious = is_empty and (win_dur >= 3.0)
                if suspicious:
                    flagged_count += 1

                window_results.append({
                    "window_index": idx,
                    "start_s": round(win_start, 2),
                    "end_s": round(win_end, 2),
                    "duration_s": round(win_dur, 2),
                    "text": win_text,
                    "is_empty": is_empty,
                    "suspicious": suspicious,
                })
                if win_text:
                    combined_texts.append(win_text)

            full_transcript = " ".join(combined_texts).strip()

            return {
                "success": True,
                "transcript": full_transcript,
                "total_duration_s": round(total_duration, 2),
                "windows_count": len(windows),
                "windows": window_results,
                "flagged_windows_count": flagged_count,
                "cost_inr": round(total_cost, 4),
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "actionable_tip": "Check that ffmpeg is installed and the audio format is valid.",
            }

    # ------------------------------------------------------------------------
    # 2. translate_text
    # ------------------------------------------------------------------------
    def translate_text(
        self,
        text: str = "",
        file_path: str = "",
        target_language_code: str = "hi",
        target_script: str = "native",
    ) -> dict[str, Any]:
        """Translates text into an Indic language or English using Indic-Translate.

        Accepts either raw text or a file_path. If file_path is provided (or if text is a file path),
        reads the file content directly in plain code so callers never have to retype long text.
        Splits long text by paragraphs and sentences, preserves formatting, and reassembles.
        """
        # Resolve text from file_path if provided, or if text itself is a file path
        if file_path:
            p = Path(file_path)
            if p.exists() and p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace")
            else:
                return {
                    "success": False,
                    "error": f"Input text file not found: {file_path}",
                    "actionable_tip": "Verify the file path exists.",
                }
        elif text:
            # Check if text is actually a file path passed into text argument
            p = Path(text.strip().strip("'\""))
            if p.exists() and p.is_file():
                file_path = str(p)
                text = p.read_text(encoding="utf-8", errors="replace")

        if not text.strip():
            return {
                "success": False,
                "error": "Empty text provided for translation.",
                "actionable_tip": "Provide non-empty text or a valid file_path.",
            }

        # Language check in plain code
        supported, lang_code, msg = is_language_supported(target_language_code)
        if not supported or not lang_code:
            return {
                "success": False,
                "error": f"Unsupported target language: {target_language_code}",
                "actionable_tip": "Bodhan Indic models only support the 22 scheduled Indian languages plus English. Unsupported languages (e.g. French, German) must be declined honestly.",
            }

        try:
            # Paragraph splitting to preserve document layout and tables
            paragraphs = text.split("\n\n")
            translated_paragraphs = []
            total_cost = 0.0
            total_tokens = 0

            for para in paragraphs:
                if not para.strip():
                    translated_paragraphs.append("")
                    continue

                cache_key = self.cache.generate_key("indic-translate", para, target=lang_code, script=target_script)
                cached = self.cache.get_json(cache_key)

                if cached is not None:
                    trans_text = cached.get("content", "")
                else:
                    payload = {
                        "model": "indic-translate",
                        "messages": [{"role": "user", "content": para}],
                        "target_language_code": lang_code,
                        "target_script": target_script,
                    }
                    resp = self.client.post("/v1/chat/completions", model_id="indic-translate", json_data=payload, timeout=60.0)
                    resp_data = resp.json()
                    trans_text = resp_data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    usage = resp_data.get("usage", {})
                    out_tokens = usage.get("completion_tokens", len(trans_text.split()))

                    para_cost = calculate_translate_cost(out_tokens)
                    self.budget.record_cost("indic-translate", para_cost, f"translate_{lang_code}")
                    self.cache.set_json(cache_key, {"content": trans_text, "tokens": out_tokens})
                    total_cost += para_cost
                    total_tokens += out_tokens

                translated_paragraphs.append(trans_text)

            reassembled = "\n\n".join(translated_paragraphs)

            return {
                "success": True,
                "translated_text": reassembled,
                "target_language": lang_code,
                "target_script": target_script,
                "tokens_count": total_tokens,
                "cost_inr": round(total_cost, 4),
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "actionable_tip": "Ensure the translation text does not contain malformed formatting.",
            }

    # ------------------------------------------------------------------------
    # 3. synthesize_speech
    # ------------------------------------------------------------------------
    def synthesize_speech(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        style: str | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """Synthesizes text into speech using Indic-Speak.

        Splits text into chunks <= 150 chars at sentence boundaries, synthesizes each,
        joins into a single WAV audio file, and measures the duration.
        """
        if not text.strip():
            return {
                "success": False,
                "error": "Empty text provided for speech synthesis.",
                "actionable_tip": "Provide text to synthesize.",
            }

        supported, lang_code, _ = is_language_supported(language)
        if not supported or not lang_code:
            return {
                "success": False,
                "error": f"Unsupported language for TTS: {language}",
                "actionable_tip": "Bodhan Indic-Speak supports 22 Indian languages and English.",
            }

        # Resolve voice
        resolved_voice = voice or DEFAULT_VOICES.get(lang_code, "Amit")

        # Destination file
        if output_path is None:
            out_p = Path(".cache") / "generated_audio" / f"speech_{lang_code}_{os.getpid()}_{int(get_audio_duration(Path('.cache/probe_speak_test.wav')) if Path('.cache/probe_speak_test.wav').exists() else 0)}.wav"
        else:
            out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Split into chunks <= 150 characters
            chunks = _split_text_by_length(text, max_chars=150)
            chunk_files: list[Path] = []
            total_cost = 0.0

            for i, chunk in enumerate(chunks):
                cache_key = self.cache.generate_key("indic-speak", chunk, lang=lang_code, voice=resolved_voice, style=style)
                cached_bytes = self.cache.get_bytes(cache_key)

                chunk_file = out_p.parent / f"chunk_{i:03d}_{cache_key[:8]}.wav"

                if cached_bytes is not None:
                    chunk_file.write_bytes(cached_bytes)
                else:
                    chunk_cost = calculate_speak_cost(len(chunk))
                    self.budget.check_budget(chunk_cost)

                    instructions = {"lang": lang_code}
                    if style:
                        instructions["style"] = style

                    payload = {
                        "model": "indic-speak",
                        "input": chunk,
                        "voice": resolved_voice,
                        "instructions": json_instructions(instructions),
                    }

                    resp = self.client.post("/v1/audio/speech", model_id="indic-speak", json_data=payload, timeout=45.0)
                    chunk_bytes = resp.content
                    self.cache.set_bytes(cache_key, chunk_bytes)
                    chunk_file.write_bytes(chunk_bytes)
                    self.budget.record_cost("indic-speak", chunk_cost, f"speak_{lang_code}_chunk_{i}")
                    total_cost += chunk_cost

                chunk_files.append(chunk_file)

            # 2. Join chunks into single final WAV
            join_audio_files(chunk_files, out_p)
            duration_s = get_audio_duration(out_p)

            # Clean temporary chunk files if not cached
            for cf in chunk_files:
                if cf != out_p and cf.exists():
                    try:
                        cf.unlink()
                    except OSError:
                        pass

            return {
                "success": True,
                "audio_path": str(out_p.resolve()),
                "duration_s": round(duration_s, 2),
                "chunks_count": len(chunks),
                "voice": resolved_voice,
                "cost_inr": round(total_cost, 4),
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "actionable_tip": "Ensure input text contains valid characters and length is within budget.",
            }

    # ------------------------------------------------------------------------
    # 4. extract_document_text
    # ------------------------------------------------------------------------
    def extract_document_text(self, file_path: str) -> dict[str, Any]:
        """Extracts text from images or multi-page PDFs using Indic-OCR.

        Converts PDF pages to PNG, executes OCR per page, and returns structured Markdown.
        """
        path = Path(file_path)
        if not path.exists():
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "actionable_tip": "Verify the image or PDF file path exists.",
            }

        try:
            pages_to_ocr: list[Path] = []
            if path.suffix.lower() == ".pdf":
                pages_to_ocr = render_pdf_to_images(path)
            else:
                pages_to_ocr = [path]

            page_results: list[dict[str, Any]] = []
            markdown_parts: list[str] = []
            total_cost = 0.0

            for p_idx, p_img in enumerate(pages_to_ocr):
                img_bytes = p_img.read_bytes()
                cache_key = self.cache.generate_key("indic-ocr", img_bytes)
                cached = self.cache.get_json(cache_key)

                if cached is not None:
                    p_text = cached.get("text", "")
                    blocks = cached.get("blocks", [])
                else:
                    page_cost = calculate_ocr_cost(1)
                    self.budget.check_budget(page_cost)

                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    mime = "image/png" if p_img.suffix.lower() == ".png" else "image/jpeg"
                    payload = {
                        "model": "indic-ocr",
                        "messages": [
                            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}
                        ],
                    }

                    resp = self.client.post("/v1/chat/completions", model_id="indic-ocr", json_data=payload, timeout=60.0)
                    resp_data = resp.json()
                    p_text = resp_data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    blocks = resp_data.get("blocks", [])

                    self.cache.set_json(cache_key, {"text": p_text, "blocks": blocks})
                    self.budget.record_cost("indic-ocr", page_cost, f"ocr_page_{p_idx + 1}")
                    total_cost += page_cost

                page_header = f"--- Page {p_idx + 1} ---"
                markdown_parts.append(f"{page_header}\n\n{p_text}")
                page_results.append({
                    "page_number": p_idx + 1,
                    "text": p_text,
                    "blocks_count": len(blocks),
                })

            combined_markdown = "\n\n".join(markdown_parts)

            return {
                "success": True,
                "text": combined_markdown,
                "pages_count": len(pages_to_ocr),
                "pages": page_results,
                "cost_inr": round(total_cost, 4),
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "actionable_tip": "Ensure the document is a supported format (PDF, PNG, JPG).",
            }

    # ------------------------------------------------------------------------
    # 5. inspect_media (Free Tool)
    # ------------------------------------------------------------------------
    def inspect_media(self, file_path: str) -> dict[str, Any]:
        """Free helper tool inspecting media properties, duration, pages, and cost estimates."""
        path = Path(file_path)
        if not path.exists():
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "actionable_tip": "Check the file path.",
            }

        size_bytes = path.stat().st_size
        suffix = path.suffix.lower()
        info: dict[str, Any] = {
            "success": True,
            "file_name": path.name,
            "size_bytes": size_bytes,
            "format": suffix,
        }

        # Audio / Video inspection
        if suffix in (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".mp4", ".mkv", ".mov", ".avi"):
            try:
                dur = get_audio_duration(path)
                info["duration_s"] = round(dur, 2)
                info["estimated_transcribe_cost_inr"] = round(calculate_transcribe_cost(dur), 4)
                info["media_type"] = "video" if suffix in (".mp4", ".mkv", ".mov", ".avi") else "audio"
            except Exception:
                info["media_type"] = "unknown_media"

        # PDF inspection
        elif suffix == ".pdf":
            try:
                import pypdfium2 as pdfium
                doc = pdfium.PdfDocument(str(path))
                page_count = len(doc)
                info["media_type"] = "document_pdf"
                info["page_count"] = page_count
                info["estimated_ocr_cost_inr"] = round(calculate_ocr_cost(page_count), 4)
            except Exception:
                info["media_type"] = "pdf"

        # Image inspection
        elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
            info["media_type"] = "image"
            info["estimated_ocr_cost_inr"] = round(calculate_ocr_cost(1), 4)

        # Text file
        elif suffix in (".txt", ".md", ".json"):
            info["media_type"] = "text"
            text_content = path.read_text(encoding="utf-8", errors="replace")
            info["char_count"] = len(text_content)
            info["word_count"] = len(text_content.split())
            info["preview"] = text_content[:200]
            info["detected_script"] = detect_script_language(text_content)
            info["file_path"] = str(path.resolve())
            info["estimated_tts_cost_inr"] = round(calculate_speak_cost(len(text_content)), 4)
            info["estimated_translate_cost_inr"] = round(calculate_translate_cost(int(len(text_content.split()) * 1.3)), 4)
        else:
            info["media_type"] = "other"
            info["file_path"] = str(path.resolve())

        return info

    # ------------------------------------------------------------------------
    # 6. Plain Code Helpers
    # ------------------------------------------------------------------------
    def detect_language(self, text: str | None = None, audio_path: str | None = None) -> dict[str, Any]:
        """Detects language via script analysis for text or transcription heuristic for audio."""
        if text:
            script_code = detect_script_language(text)
            if script_code and script_code in SUPPORTED_INDIC_LANGUAGES:
                return {
                    "success": True,
                    "language_code": script_code,
                    "language_name": SUPPORTED_INDIC_LANGUAGES[script_code],
                    "method": "unicode_script_analysis",
                }
            return {
                "success": False,
                "error": "Could not determine language from text script.",
                "actionable_tip": "Specify the language code explicitly.",
            }

        if audio_path:
            # Inspect first 15s window with transcription
            res = self.transcribe_audio(audio_path)
            if res.get("success") and res.get("transcript"):
                return self.detect_language(text=res["transcript"])
            return {
                "success": False,
                "error": "Could not detect language from audio.",
                "actionable_tip": "Specify the language parameter explicitly.",
            }

        return {
            "success": False,
            "error": "Must provide either 'text' or 'audio_path'.",
            "actionable_tip": "Provide text or audio file path.",
        }

    def extract_audio_from_video(self, video_path: str, output_path: str | None = None) -> dict[str, Any]:
        """Extracts 16 kHz mono WAV audio track from video file."""
        v_path = Path(video_path)
        if not v_path.exists():
            return {"success": False, "error": f"Video not found: {video_path}"}

        out_wav = Path(output_path) if output_path else v_path.parent / f"{v_path.stem}_audio.wav"
        try:
            res_path = ffmpeg_extract_audio(v_path, out_wav)
            dur = get_audio_duration(res_path)
            return {
                "success": True,
                "audio_path": str(res_path.resolve()),
                "duration_s": round(dur, 2),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def build_srt(self, windows_or_segments: list[dict[str, Any]], output_path: str | None = None) -> dict[str, Any]:
        """Generates standard SubRip (.srt) subtitle content from timestamped segments."""
        if not windows_or_segments:
            return {"success": False, "error": "No segments provided to build SRT."}

        srt_lines = []
        sub_idx = 1

        for seg in windows_or_segments:
            text = seg.get("text", "").strip()
            if not text:
                continue

            start_s = float(seg.get("start_s", 0.0))
            end_s = float(seg.get("end_s", start_s + 2.0))

            srt_lines.append(str(sub_idx))
            srt_lines.append(f"{format_srt_timestamp(start_s)} --> {format_srt_timestamp(end_s)}")
            srt_lines.append(text)
            srt_lines.append("")  # Blank line separator
            sub_idx += 1

        srt_content = "\n".join(srt_lines)

        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(srt_content, encoding="utf-8")
            saved_path = str(out_p.resolve())
        else:
            saved_path = None

        return {
            "success": True,
            "srt_content": srt_content,
            "srt_path": saved_path,
            "subtitle_count": sub_idx - 1,
        }


def json_instructions(obj: dict) -> str:
    """Formats instructions object as JSON string for indic-speak."""
    import json
    return json.dumps(obj)
