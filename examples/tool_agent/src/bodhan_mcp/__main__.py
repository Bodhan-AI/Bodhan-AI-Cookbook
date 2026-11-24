from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from bodhan_agent.errors import ToolError
from bodhan_agent.settings import Settings
from bodhan_agent.tools import ToolExecutor

logging.basicConfig(level=logging.INFO)
server = FastMCP("Bodhan Indic Tools")


def _run(name: str, arguments: dict[str, Any], required_model: str) -> dict[str, Any]:
    try:
        settings = Settings.from_env(required_models=(required_model,))
        artifact_dir = Path(os.getenv("BODHAN_MCP_ARTIFACTS_DIR", "./bodhan_mcp_artifacts")).expanduser().resolve()
        executor = ToolExecutor(settings, artifact_dir)
        return executor.call(name, arguments, max_cost_inr=float("inf"), spent_inr=0.0)
    except ToolError as exc:
        return {"error": {"code": exc.code, "message": str(exc)}}
    except Exception as exc:
        logging.exception("MCP tool failed unexpectedly")
        return {"error": {"code": "internal_tool_error", "message": str(exc)}}


@server.tool()
def transcribe_audio(file_ref: str, language: str | None = None) -> dict[str, Any]:
    """Transcribe an audio file using Bodhan Indic-Transcribe. Audio is split into <=30s windows internally. Since the API response has no timestamps, estimated sentence timings are marked as such."""
    return _run("transcribe_audio", {"file_ref": file_ref, "language": language}, "transcribe")


@server.tool()
def translate_text(text: str, target_language: str, script: str = "native") -> dict[str, Any]:
    """Translate with Bodhan Indic-Translate. Long input is split internally at paragraph boundaries; supported script modes are native, roman and codemix."""
    return _run("translate_text", {"text": text, "target_language": target_language, "script": script}, "translate")


@server.tool()
def synthesize_speech(text: str, language: str, voice: str, output_path: str, style: str | None = None) -> dict[str, Any]:
    """Generate AI-labelled WAV speech with Bodhan Indic-Speak. Long text is split and joined. output_path must be inside BODHAN_MCP_ARTIFACTS_DIR (default ./bodhan_mcp_artifacts)."""
    return _run("synthesize_speech", {"text": text, "language": language, "voice": voice, "output_path": output_path, "style": style}, "speak")


@server.tool()
def ocr_document(file_ref: str) -> dict[str, Any]:
    """Extract text using Bodhan Indic-OCR from one image, a folder of page images, or a scanned PDF. Returns page-labelled text and cost."""
    return _run("ocr_document", {"file_ref": file_ref}, "ocr")


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
