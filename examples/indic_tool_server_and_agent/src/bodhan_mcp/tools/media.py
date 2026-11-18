"""Media helpers: inspection, script detection, audio concat/split, PDF rendering, SRT building."""
from __future__ import annotations

import logging
import mimetypes
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, List

from bodhan_mcp.config import settings
from bodhan_mcp.core.chunking import get_audio_duration_seconds, chunk_audio
from bodhan_mcp.core.errors import BodhanError, FILE_NOT_FOUND, INVALID_INPUT, UNSUPPORTED_FORMAT, UNSUPPORTED_LANGUAGE
from bodhan_mcp.core.file_store import default_file_store
from bodhan_mcp.schemas import (
    InspectMediaOutput,
    DetectScriptOutput,
    SplitAudioOutput,
    ConcatAudioOutput,
    RenderPDFPagesOutput,
    BuildSRTOutput,
    TranscriptSegment,
    GetSupportedLanguagesOutput,
)

logger = logging.getLogger(__name__)


def detect_script(text: str) -> DetectScriptOutput:
    """Identify the script and probable Indic language from Unicode block analysis."""
    if not text or not text.strip():
        return DetectScriptOutput(ok=True, detected_language=None, script_name=None, confidence="low")

    # Unicode script ranges
    script_counts = {
        "Devanagari": len(re.findall(r'[\u0900-\u097F]', text)),  # hi, mr, sa, mai, bho, kok, ne
        "Bengali": len(re.findall(r'[\u0980-\u09FF]', text)),     # bn, as
        "Gurmukhi": len(re.findall(r'[\u0A00-\u0A7F]', text)),    # pa
        "Gujarati": len(re.findall(r'[\u0A80-\u0AFF]', text)),    # gu
        "Odia": len(re.findall(r'[\u0B00-\u0B7F]', text)),        # or
        "Tamil": len(re.findall(r'[\u0B80-\u0BFF]', text)),       # ta
        "Telugu": len(re.findall(r'[\u0C00-\u0C7F]', text)),      # te
        "Kannada": len(re.findall(r'[\u0C80-\u0CFF]', text)),     # kn
        "Malayalam": len(re.findall(r'[\u0D00-\u0D7F]', text)),   # ml
        "Arabic": len(re.findall(r'[\u0600-\u06FF]', text)),      # ur
        "Latin": len(re.findall(r'[A-Za-z]', text)),              # en
    }

    best_script, count = max(script_counts.items(), key=lambda kv: kv[1])
    if count == 0:
        return DetectScriptOutput(ok=True, detected_language=None, script_name=None, confidence="low")

    script_to_lang = {
        "Tamil": "ta",
        "Telugu": "te",
        "Kannada": "kn",
        "Malayalam": "ml",
        "Gujarati": "gu",
        "Gurmukhi": "pa",
        "Odia": "or",
        "Bengali": "bn",
        "Devanagari": "hi",  # primary default
        "Arabic": "ur",
        "Latin": "en",
    }
    lang = script_to_lang.get(best_script)
    confidence = "high" if count > 15 else "medium"

    return DetectScriptOutput(
        ok=True,
        detected_language=lang,
        script_name=best_script,
        confidence=confidence,
    )


def inspect_media(file_path: str) -> InspectMediaOutput:
    """Inspect any media file: mime type, size, duration, pages, dimensions, or script."""
    p = Path(file_path)
    if not p.exists():
        raise BodhanError(FILE_NOT_FOUND, f"File not found: {file_path}")

    size_bytes = p.stat().st_size
    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"

    duration = None
    num_pages = None
    width = None
    height = None
    detected_sc = None

    ext = p.suffix.lower()

    if ext in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".mp4", ".mkv"}:
        try:
            duration = get_audio_duration_seconds(file_path)
        except Exception:
            duration = None

    if ext == ".pdf":
        # 1. Try PyMuPDF (fitz)
        try:
            import fitz
            doc = fitz.open(file_path)
            num_pages = len(doc)
            doc.close()
        except Exception:
            pass

        # 2. Try pypdfium2
        if num_pages is None:
            try:
                import pypdfium2
                pdf = pypdfium2.PdfDocument(file_path)
                num_pages = len(pdf)
                pdf.close()
            except Exception:
                pass

        # 3. Try pdf2image
        if num_pages is None:
            try:
                from pdf2image import pdfinfo_from_path
                info = pdfinfo_from_path(file_path)
                num_pages = info.get("Pages")
            except Exception:
                pass

    if ext in {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}:
        try:
            from PIL import Image
            with Image.open(file_path) as im:
                width, height = im.size
        except Exception:
            pass

    if ext in {".txt", ".md"}:
        try:
            sample = p.read_text(encoding="utf-8")[:500]
            sc = detect_script(sample)
            detected_sc = sc.script_name
        except Exception:
            pass

    return InspectMediaOutput(
        ok=True,
        file_path=str(p.resolve()),
        mime_type=mime,
        size_bytes=size_bytes,
        duration_seconds=duration,
        num_pages=num_pages,
        width=width,
        height=height,
        detected_script=detected_sc,
    )


def render_pdf_pages(
    file_path: str,
    dpi: int = 200,
    output_dir: Optional[str] = None,
) -> RenderPDFPagesOutput:
    """Render PDF pages to PNG image files.
    
    Supports PyMuPDF (fitz), pypdfium2, and pdf2image.
    """
    p = Path(file_path)
    if not p.exists():
        raise BodhanError(FILE_NOT_FOUND, f"PDF file not found: {file_path}")

    out_d = Path(output_dir) if output_dir else p.parent / f"{p.stem}_rendered_pages"
    out_d.mkdir(parents=True, exist_ok=True)
    base = p.stem
    image_paths: List[str] = []

    # 1. Try PyMuPDF (fitz) - fastest, self-contained, no poppler binary needed
    try:
        import fitz
        doc = fitz.open(file_path)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat)
            img_path = str(out_d / f"{base}_page_{i+1:03d}.png")
            pix.save(img_path)
            image_paths.append(img_path)
        doc.close()
        if image_paths:
            return RenderPDFPagesOutput(ok=True, page_image_paths=image_paths, num_pages=len(image_paths))
    except Exception as exc:
        logger.info("fitz PDF render error: %s, trying fallback", exc)

    # 2. Try pypdfium2
    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(file_path)
        scale = dpi / 72.0
        for i, page in enumerate(pdf):
            image = page.render(scale=scale).to_pil()
            img_path = str(out_d / f"{base}_page_{i+1:03d}.png")
            image.save(img_path, format="PNG")
            image_paths.append(img_path)
        pdf.close()
        if image_paths:
            return RenderPDFPagesOutput(ok=True, page_image_paths=image_paths, num_pages=len(image_paths))
    except Exception as exc:
        logger.info("pypdfium2 PDF render error: %s, trying fallback", exc)

    # 3. Try pdf2image (requires poppler)
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(file_path, dpi=dpi)
        for i, image in enumerate(images):
            img_path = str(out_d / f"{base}_page_{i+1:03d}.png")
            image.save(img_path, "PNG")
            image_paths.append(img_path)
        if image_paths:
            return RenderPDFPagesOutput(ok=True, page_image_paths=image_paths, num_pages=len(image_paths))
    except Exception as exc:
        logger.error("pdf2image PDF render error: %s", exc)

    raise BodhanError(
        UNSUPPORTED_FORMAT,
        f"Failed to render PDF {file_path}. Ensure PyMuPDF, pypdfium2, or pdf2image/poppler is installed.",
    )


def split_audio(file_path: str, chunk_seconds: int = 30, output_dir: Optional[str] = None) -> SplitAudioOutput:
    """Split audio file into chunks <= chunk_seconds."""
    chunks = chunk_audio(file_path, chunk_seconds=chunk_seconds, output_dir=output_dir)
    paths = [c[0] for c in chunks]
    return SplitAudioOutput(ok=True, chunks=paths, num_chunks=len(paths))


def concat_audio(file_paths: List[str], output_path: Optional[str] = None) -> ConcatAudioOutput:
    """Concatenate multiple audio files in order."""
    if not file_paths:
        raise BodhanError(INVALID_INPUT, "file_paths list cannot be empty.")

    for fp in file_paths:
        if not os.path.exists(fp):
            raise BodhanError(FILE_NOT_FOUND, f"Audio file not found: {fp}")

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        res_path = str(out_p.resolve())
    else:
        dest = default_file_store.base_dir / "concatenated.wav"
        res_path = str(dest.resolve())

    # Try pydub first
    try:
        from pydub import AudioSegment
        combined = AudioSegment.empty()
        for fp in file_paths:
            seg = AudioSegment.from_file(fp)
            combined += seg
        combined.export(res_path, format=Path(res_path).suffix.lstrip(".") or "wav")
        duration = len(combined) / 1000.0
        return ConcatAudioOutput(ok=True, output_file_path=res_path, duration_seconds=round(duration, 2))
    except Exception as exc:
        logger.info("pydub concat failed (%s), attempting ffmpeg concat", exc)

    # Fallback to ffmpeg concat demuxer
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f_list:
        for fp in file_paths:
            f_list.write(f"file '{os.path.abspath(fp)}'\n")
        f_list_path = f_list.name

    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f_list_path, "-c", "copy", res_path]
        subprocess.run(cmd, capture_output=True, check=True)
    except Exception:
        # Re-encode if copy fails
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f_list_path, res_path]
        subprocess.run(cmd, capture_output=True, check=True)
    finally:
        if os.path.exists(f_list_path):
            os.remove(f_list_path)

    duration = get_audio_duration_seconds(res_path)
    return ConcatAudioOutput(ok=True, output_file_path=res_path, duration_seconds=round(duration, 2))


def _format_srt_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS,mmm string."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


async def build_srt(
    segments: List[TranscriptSegment],
    output_path: Optional[str] = None,
    target_language: Optional[str] = None,
    source_language: Optional[str] = None,
) -> BuildSRTOutput:
    """Build SRT subtitle file from transcript segments, optionally translating them."""
    if not segments:
        raise BodhanError(INVALID_INPUT, "Segments list cannot be empty.")

    # Optional translation of segments
    translated_texts: list[str] = []
    if target_language:
        from bodhan_mcp.clients.translate import TranslateClient
        tgt = str(target_language).lower()
        if tgt not in settings.translate_languages:
            raise BodhanError(
                UNSUPPORTED_LANGUAGE,
                f"Target language '{target_language}' is not supported for subtitle translation.",
            )
        client = TranslateClient()
        for seg in segments:
            if isinstance(seg, dict):
                orig_text = str(seg.get("text", ""))
            else:
                orig_text = str(getattr(seg, "text", ""))
            if orig_text.strip():
                try:
                    res = await client.translate_text(
                        orig_text,
                        target_language_code=tgt,
                        source_language_code=source_language,
                    )
                    translated_texts.append(res.get("translated_text", orig_text))
                except Exception as exc:
                    logger.warning("Failed to translate segment '%s': %s", orig_text, exc)
                    translated_texts.append(orig_text)
            else:
                translated_texts.append("")

    lines: list[str] = []
    for idx, seg in enumerate(segments, start=1):
        if isinstance(seg, dict):
            start = float(seg.get("start_seconds") if seg.get("start_seconds") is not None else seg.get("start_ms", 0.0) / 1000.0)
            end = float(seg.get("end_seconds") if seg.get("end_seconds") is not None else seg.get("end_ms", start + 2.0) / 1000.0)
            text = str(seg.get("text", ""))
        else:
            start = float(getattr(seg, "start_seconds", 0.0))
            end = float(getattr(seg, "end_seconds", start + 2.0))
            text = str(getattr(seg, "text", ""))

        if translated_texts and idx <= len(translated_texts):
            text = translated_texts[idx - 1]

        start_str = _format_srt_time(start)
        end_str = _format_srt_time(end)
        lines.append(f"{idx}")
        lines.append(f"{start_str} --> {end_str}")
        lines.append(text)
        lines.append("")

    srt_content = "\n".join(lines)
    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(srt_content, encoding="utf-8")
        final_path = str(out_p.resolve())
    else:
        final_path = default_file_store.save_text(srt_content, extension=".srt", prefix="subtitles")

    return BuildSRTOutput(ok=True, srt_file_path=final_path, num_entries=len(segments))


def get_supported_languages() -> GetSupportedLanguagesOutput:
    """Return list of supported language codes per model and TTS voices."""
    return GetSupportedLanguagesOutput(
        ok=True,
        stt_languages=sorted(list(settings.stt_languages)),
        translate_languages=sorted(list(settings.translate_languages)),
        tts_languages=sorted(list(settings.tts_languages)),
        tts_voices=settings.tts_voices,
    )
