from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from bodhan_mcp.bodhan_client import BodhanClient, CostLedger
from bodhan_mcp.media_utils import ensure_dir, inspect_media_file

load_dotenv()

mcp = FastMCP("bodhan-indic-tools")

_SHARED_LEDGER = CostLedger()


def _get_client() -> BodhanClient:
    return BodhanClient(ledger=_SHARED_LEDGER)


def _outputs_dir() -> Path:
    base = os.environ.get("BODHAN_OUTPUT_DIR") or "examples/bodhan_tool_agent/outputs"
    return ensure_dir(base)


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def inspect_media(file_path: str) -> str:
    """Inspect a local audio, PDF, image, or text file on disk and return metadata (duration, page count, dimensions, size)."""
    try:
        info = inspect_media_file(file_path)
        return _json_text({"status": "ok", "media": info})
    except Exception as exc:
        return _json_text({"status": "error", "error": str(exc)})


@mcp.tool()
def indic_transcribe(file_path: str, language: str | None = None) -> str:
    """Transcribe a speech audio file using Bodhan indic-transcribe (automatically chunks audio > 30s)."""
    try:
        client = _get_client()
        res = client.transcribe_audio(
            audio_path=file_path,
            language=language,
            work_dir=_outputs_dir() / "work_audio",
        )
        return _json_text(
            {
                "status": "ok",
                "text": res["text"],
                "language": res["language"],
                "chunks_processed": res["chunks_processed"],
                "duration_seconds": res["duration_seconds"],
                "estimated_cost_inr": res["estimated_cost_inr"],
                "ledger": _SHARED_LEDGER.to_dict(),
            }
        )
    except Exception as exc:
        return _json_text({"status": "error", "error": str(exc)})


@mcp.tool()
def indic_translate(
    text: str,
    target_language: str,
    source_language: str | None = None,
    script: str = "native",
) -> str:
    """Translate text across Indian languages and English using Bodhan indic-translate."""
    try:
        client = _get_client()
        res = client.translate_text(
            text=text,
            target_language=target_language,
            source_language=source_language,
            script=script,
        )
        return _json_text(
            {
                "status": "ok",
                "translated_text": res["translated_text"],
                "source_language": res["source_language"],
                "target_language": res["target_language"],
                "script": res["script"],
                "estimated_cost_inr": res["estimated_cost_inr"],
                "ledger": _SHARED_LEDGER.to_dict(),
            }
        )
    except Exception as exc:
        return _json_text({"status": "error", "error": str(exc)})


@mcp.tool()
def indic_speak(
    text: str,
    language: str,
    voice: str,
    style: str | None = None,
    output_filename: str | None = None,
) -> str:
    """Synthesize speech from text using Bodhan indic-speak and save a WAV file under outputs/."""
    try:
        client = _get_client()
        out_dir = _outputs_dir()
        safe_name = (output_filename or "speech_output.wav").strip()
        if not safe_name.lower().endswith(".wav"):
            safe_name = f"{safe_name}.wav"
        out_path = out_dir / Path(safe_name).name

        res = client.synthesize_speech(
            text=text,
            language=language,
            voice=voice,
            output_path=out_path,
            style=style,
            work_dir=out_dir / "work_tts",
        )
        return _json_text(
            {
                "status": "ok",
                "audio_path": res["audio_path"],
                "language": res["language"],
                "voice": res["voice"],
                "style": res["style"],
                "segments": res["segments"],
                "estimated_cost_inr": res["estimated_cost_inr"],
                "ledger": _SHARED_LEDGER.to_dict(),
            }
        )
    except Exception as exc:
        return _json_text({"status": "error", "error": str(exc)})


@mcp.tool()
def indic_ocr(file_path: str, table_format: str = "markdown") -> str:
    """Extract text, layout, and tables from an image or PDF using Bodhan indic-ocr."""
    try:
        client = _get_client()
        res = client.ocr_image_or_pdf(
            file_path=file_path,
            table_format=table_format,
            work_dir=_outputs_dir() / "work_ocr",
        )
        return _json_text(
            {
                "status": "ok",
                "source_file": res["source_file"],
                "page_count": res["page_count"],
                "markdown": res["markdown"],
                "pages": res["pages"],
                "estimated_cost_inr": res["estimated_cost_inr"],
                "ledger": _SHARED_LEDGER.to_dict(),
            }
        )
    except Exception as exc:
        return _json_text({"status": "error", "error": str(exc)})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()