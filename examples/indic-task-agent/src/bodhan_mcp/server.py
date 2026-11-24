"""
Bodhan MCP Tool Server.
Exposes Bodhan Indic models and media tools over stdio as typed MCP tools.
Handles sub-30s audio chunking, TTS sentence limits, and multi-page PDF OCR internally.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mcp.server.mcpserver import MCPServer

from bodhan_mcp.client import (
    BodhanClient,
    BodhanError,
    BodhanUnsupportedLanguageError,
)
from bodhan_mcp.config import (
    INDIC_SPEAK_SAMPLE_VOICES,
    SUPPORTED_INDIC_LANGUAGES,
    is_language_supported,
    normalize_lang_code,
)
from bodhan_mcp.helpers import (
    chunk_audio_if_needed,
    concatenate_audio_clips,
    extract_pdf_pages_as_images,
    inspect_media as probe_media_file,
    split_text_for_tts,
)

logger = logging.getLogger("bodhan.mcp_server")

# Initialize MCP Server
server = MCPServer(
    name="bodhan-indic-tools",
    version="1.0.0",
    description="Model Context Protocol (MCP) server for Bodhan's Indic AI models (ASR, MT, TTS, OCR).",
)

# Global client instance
client = BodhanClient()


# ---------------------------------------------------------------------------
# MCP Resource (Stretch Goal): Voice Catalog
# ---------------------------------------------------------------------------
@server.resource("bodhan://voices")
def list_voices_resource() -> str:
    """Returns the catalog of supported Indic languages and production voices."""
    catalog = {
        "supported_languages": SUPPORTED_INDIC_LANGUAGES,
        "indic_speak_voices": INDIC_SPEAK_SAMPLE_VOICES,
        "total_languages": len(SUPPORTED_INDIC_LANGUAGES),
    }
    return json.dumps(catalog, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 1: Media Inspector (Pre-flight Inspection)
# ---------------------------------------------------------------------------
@server.tool(
    name="inspect_media",
    description=(
        "Inspects any media file (audio, image, PDF, video) to get technical metadata "
        "(duration, channels, format, image dimensions, or PDF page count) BEFORE "
        "calling expensive models. Zero cost: ₹0.00."
    ),
)
def inspect_media_tool(file_path: str) -> Dict[str, Any]:
    """
    Inspects media file metadata.
    Args:
        file_path: Absolute or relative path to media file.
    """
    try:
        return probe_media_file(file_path)
    except Exception as e:
        return {"error": f"Failed to inspect media '{file_path}': {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 2: Speech-to-Text (Indic-Transcribe with 30s Window Chunking)
# ---------------------------------------------------------------------------
@server.tool(
    name="speech_to_text",
    description=(
        "Transcribes spoken audio into text using Bodhan Indic-Transcribe. "
        "Automatically handles audio files of ANY length by chunking into sub-30s windows. "
        "If language is omitted, automatically performs Language Identification (LID). "
        "Pricing: ₹0.10 per input audio minute."
    ),
)
def speech_to_text_tool(
    audio_path: str,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Transcribes audio to text.
    Args:
        audio_path: Path to the audio file (.wav, .mp3, etc.).
        language: Optional ISO language code (e.g. 'ta', 'hi', 'te'). If omitted, model detects it.
    """
    path = Path(audio_path).resolve()
    if not path.exists():
        return {"error": f"Audio file not found: {audio_path}", "status": "failed"}

    try:
        # Check if file needs chunking to respect the 30.0s window limit
        chunks = chunk_audio_if_needed(path, max_chunk_sec=28.0)
        
        full_transcript = []
        detected_lang = language
        total_duration = 0.0
        total_cost = 0.0
        all_cached = True

        for chunk in chunks:
            chunk_res = client.transcribe(chunk, language=language)
            text = chunk_res.get("text", "").strip()
            if text:
                full_transcript.append(text)
            
            if not detected_lang and chunk_res.get("detected_language"):
                detected_lang = chunk_res["detected_language"]
            
            total_duration += chunk_res.get("duration_seconds", 0.0)
            total_cost += chunk_res.get("cost_inr", 0.0)
            if not chunk_res.get("cached", False):
                all_cached = False

        joined_text = " ".join(full_transcript)
        return {
            "text": joined_text,
            "detected_language": detected_lang or "unknown",
            "duration_seconds": round(total_duration, 2),
            "chunks_processed": len(chunks),
            "cost_inr": round(total_cost, 5),
            "cached": all_cached,
        }

    except BodhanUnsupportedLanguageError as le:
        return {"error": str(le), "unsupported_language": le.language, "status": "impossible"}
    except Exception as e:
        return {"error": f"Transcription failed: {str(e)}", "status": "failed"}


# ---------------------------------------------------------------------------
# Tool 3: Translation (Indic-Translate with Strict Language Guardrails)
# ---------------------------------------------------------------------------
@server.tool(
    name="translate_text",
    description=(
        "Translates text between any of the 22 Scheduled Indian languages and English "
        "using Bodhan Indic-Translate. Halts cleanly if an unsupported language is requested. "
        "Pricing: ₹0.20 per 10,000 output tokens."
    ),
)
def translate_text_tool(
    text: str,
    target_language: str,
    source_language: str = "en",
) -> Dict[str, Any]:
    """
    Translates text.
    Args:
        text: Text to translate.
        target_language: Destination language ISO code (e.g. 'hi', 'mr', 'ta', 'en').
        source_language: Source language ISO code (default: 'en').
    """
    if not text.strip():
        return {"translated_text": "", "cost_inr": 0.0, "status": "success"}

    try:
        res = client.translate(
            text=text,
            target_language=target_language,
            source_language=source_language,
        )
        return res
    except BodhanUnsupportedLanguageError as le:
        return {
            "error": str(le),
            "unsupported_language": le.language,
            "status": "impossible",
            "note": "Task requested an unsupported language. Honest failure reported without fake output.",
        }
    except Exception as e:
        return {"error": f"Translation failed: {str(e)}", "status": "failed"}


# ---------------------------------------------------------------------------
# Tool 4: Speech Synthesis (Indic-Speak with Sentence Slicing & LUFS Normalization)
# ---------------------------------------------------------------------------
@server.tool(
    name="synthesize_speech",
    description=(
        "Converts text into natural speech audio using Bodhan Indic-Speak. "
        "Automatically handles long text by chunking on natural sentence boundaries "
        "and stitching audio with LUFS loudness normalization (-16 LUFS). "
        "Returns the path to the generated audio file. Pricing: ₹6.00 per 10,000 characters."
    ),
)
def synthesize_speech_tool(
    text: str,
    language: str,
    gender: str = "female",
    output_path: Optional[str] = None,
    speed: float = 1.0,
) -> Dict[str, Any]:
    """
    Synthesizes speech from text.
    Args:
        text: The text to speak aloud.
        language: ISO language code (e.g. 'hi', 'bn', 'te', 'ta').
        gender: Speaker gender ('female' or 'male').
        output_path: Optional destination path for output .wav file.
        speed: Speech rate multiplier (default 1.0).
    """
    if not text.strip():
        return {"error": "Input text cannot be empty", "status": "failed"}

    norm_lang = normalize_lang_code(language)
    if not is_language_supported(norm_lang):
        return {
            "error": f"Language '{language}' is not supported for speech synthesis.",
            "unsupported_language": language,
            "status": "impossible",
        }

    # Pick voice name matching gender
    available_voices = INDIC_SPEAK_SAMPLE_VOICES.get(norm_lang, ["Amit", "Ananya"])
    # By convention: index 1 is female (if available), index 0 is male
    selected_voice = available_voices[1] if (gender.lower() == "female" and len(available_voices) > 1) else available_voices[0]

    try:
        # Split text into sentence chunks to respect Indic-Speak's sentence limits
        chunks = split_text_for_tts(text, max_chars=350)
        
        audio_clips = []
        total_chars = 0
        total_cost = 0.0

        for idx, chunk in enumerate(chunks):
            chunk_res = client.speak(
                text=chunk,
                language=norm_lang,
                voice=selected_voice,
                speed=speed,
            )
            audio_clips.append(chunk_res["audio_path"])
            total_chars += chunk_res.get("character_count", len(chunk))
            total_cost += chunk_res.get("cost_inr", 0.0)

        # Target output destination
        final_dest = Path(output_path).resolve() if output_path else Path(client.cache_dir / f"final_speech_{norm_lang}.wav")
        final_audio = concatenate_audio_clips(audio_clips, final_dest)
        duration_sec = client.get_audio_duration(final_audio)

        return {
            "audio_path": str(final_audio),
            "duration_seconds": round(duration_sec, 2),
            "character_count": total_chars,
            "voice": selected_voice,
            "language": norm_lang,
            "cost_inr": round(total_cost, 5),
            "status": "success",
        }

    except BodhanUnsupportedLanguageError as le:
        return {"error": str(le), "unsupported_language": le.language, "status": "impossible"}
    except Exception as e:
        return {"error": f"Speech synthesis failed: {str(e)}", "status": "failed"}


# ---------------------------------------------------------------------------
# Tool 5: OCR (Indic-OCR with Multi-Page PDF Support)
# ---------------------------------------------------------------------------
@server.tool(
    name="extract_text_ocr",
    description=(
        "Extracts text from images (.png, .jpg) or multi-page PDF documents (.pdf) "
        "using Bodhan Indic-OCR. Reconstructs proper reading order and detects scripts. "
        "Pricing: ₹0.20 per image / page."
    ),
)
def extract_text_ocr_tool(
    file_path: str,
) -> Dict[str, Any]:
    """
    Extracts text from an image or multi-page PDF document.
    Args:
        file_path: Path to the image or PDF file.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        return {"error": f"File not found: {file_path}", "status": "failed"}

    suffix = path.suffix.lower()

    try:
        # Plain text / Markdown fallback (free, no OCR needed)
        if suffix in {".txt", ".md", ".csv", ".srt", ".vtt"}:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {
                "extracted_text": content,
                "detected_script": "Latin",
                "page_count": 1,
                "cost_inr": 0.0,
                "status": "success",
            }

        # Excel worksheet (.xlsx) handling
        if suffix == ".xlsx":
            import zipfile
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(path, "r") as z:
                strings = []
                if "xl/sharedStrings.xml" in z.namelist():
                    tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
                    for si in tree.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
                        t_text = "".join(t.text for t in si.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t") if t.text)
                        strings.append(t_text)
                rows = []
                if "xl/worksheets/sheet1.xml" in z.namelist():
                    tree = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
                    for row in tree.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
                        cols = []
                        for c in row.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                            val = ""
                            v_elem = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                            if v_elem is not None and v_elem.text:
                                if c.attrib.get("t") == "s":
                                    idx = int(v_elem.text)
                                    val = strings[idx] if idx < len(strings) else ""
                                else:
                                    val = v_elem.text
                            else:
                                t_elem = c.find(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
                                if t_elem is not None and t_elem.text:
                                    val = t_elem.text
                            cols.append(val)
                        if any(cols):
                            rows.append(cols)
                if rows:
                    max_cols = max(len(r) for r in rows)
                    padded_rows = [r + [""] * (max_cols - len(r)) for r in rows]
                    header = "| " + " | ".join(padded_rows[0]) + " |"
                    sep = "| " + " | ".join(["---"] * max_cols) + " |"
                    body = ["| " + " | ".join(r) + " |" for r in padded_rows[1:]]
                    table_md = "\n".join([header, sep] + body)
                else:
                    table_md = ""
            return {
                "extracted_text": table_md,
                "detected_script": "Latin",
                "page_count": 1,
                "cost_inr": 0.0,
                "status": "success",
            }

        # Word document (.docx) handling
        if suffix == ".docx":
            import zipfile
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(path, "r") as z:
                if "word/document.xml" in z.namelist():
                    tree = ET.fromstring(z.read("word/document.xml"))
                    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                    body = tree.find("w:body", ns)
                    content_blocks = []
                    if body is not None:
                        for child in body:
                            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                            if tag == "p":
                                texts = [t.text for t in child.iter(f"{{{ns['w']}}}t") if t.text]
                                if texts:
                                    content_blocks.append("".join(texts))
                            elif tag == "tbl":
                                for tr in child.findall("w:tr", ns):
                                    row_cells = []
                                    for tc in tr.findall("w:tc", ns):
                                        cell_texts = [t.text for t in tc.iter(f"{{{ns['w']}}}t") if t.text]
                                        row_cells.append("".join(cell_texts).strip())
                                    if any(row_cells):
                                        content_blocks.append("| " + " | ".join(row_cells) + " |")
                    doc_text = "\n\n".join(content_blocks)
                else:
                    doc_text = ""
            return {
                "extracted_text": doc_text,
                "detected_script": "Latin",
                "page_count": 1,
                "cost_inr": 0.0,
                "status": "success",
            }

        # Multi-page PDF handling
        if suffix == ".pdf":
            page_images = extract_pdf_pages_as_images(path)
            if not page_images:
                return {"error": f"Failed to extract pages from PDF '{file_path}'", "status": "failed"}

            combined_text = []
            detected_script = "Indic"
            total_cost = 0.0

            for idx, page_img in enumerate(page_images, 1):
                page_res = client.ocr(page_img)
                text = page_res.get("extracted_text", "").strip()
                if text:
                    combined_text.append(f"--- Page {idx} ---\n{text}")
                detected_script = page_res.get("detected_script", detected_script)
                total_cost += page_res.get("cost_inr", 0.0)

            return {
                "extracted_text": "\n\n".join(combined_text),
                "detected_script": detected_script,
                "pages_processed": len(page_images),
                "file_path": str(path),
                "cost_inr": round(total_cost, 5),
                "status": "success",
            }

        # Single image handling
        res = client.ocr(path)
        res["pages_processed"] = 1
        res["status"] = "success"
        return res

    except Exception as e:
        return {"error": f"OCR extraction failed: {str(e)}", "status": "failed"}


# ---------------------------------------------------------------------------
# Stdio Server Entrypoint
# ---------------------------------------------------------------------------
def run_stdio_server():
    """Runs the MCP server over standard input/output."""
    server.run(transport="stdio")


if __name__ == "__main__":
    run_stdio_server()
    