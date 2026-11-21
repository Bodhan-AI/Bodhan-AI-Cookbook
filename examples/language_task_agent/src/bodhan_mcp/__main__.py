"""Independent MCP stdio server. Protocol messages only on stdout."""

from __future__ import annotations
import os
import asyncio
import time
import uuid
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from bodhan_core.gateway import Gateway
from bodhan_core.runtime import Limits, Run, ToolError, decimal
from bodhan_core.tools import TOOL_DEFS, Tools
from bodhan_core.results import (
    ErrorResult,
    FilesResult,
    ReadResult,
    WriteResult,
    TranscriptResult,
    TranslationResult,
    SpeechResult,
    OCRResult,
    SubtitlesResult,
)

load_dotenv()
server = FastMCP("Bodhan Language Tools")
_service: Tools | None = None
_lock = asyncio.Lock()


def service() -> Tools:
    global _service
    if _service is None:
        root = Path(os.environ.get("BODHAN_INPUT_ROOT", "."))
        root.mkdir(parents=True, exist_ok=True)
        run = Run(
            root,
            Path(os.environ.get("BODHAN_OUTPUT_ROOT", "./outputs/mcp"))
            / ("session_" + uuid.uuid4().hex),
            Limits(
                max_cost=decimal(os.environ.get("BODHAN_MAX_COST_INR", "20")),
                max_steps=int(os.environ.get("BODHAN_MAX_STEPS", "30")),
                call_timeout=float(os.environ.get("BODHAN_CALL_TIMEOUT", "45")),
                run_timeout=float(os.environ.get("BODHAN_RUN_TIMEOUT", "300")),
            ),
        )
        _service = Tools(run, Gateway(run))
    return _service


async def safe_call(name: str, args: dict) -> dict:
    try:
        async with _lock:
            tool_service = service()
            # Session cost remains cumulative. Steps/time apply to each tool call,
            # including all its internal chunks and verification requests.
            tool_service.run.steps = 0
            tool_service.run.started = time.monotonic()
            return await tool_service.call(name, args)
    except ToolError as exc:
        return exc.as_dict()
    except (OSError, ValueError):
        return ToolError(
            "server_configuration", "Check MCP input/output roots and numeric budget settings."
        ).as_dict()


@server.tool(description=TOOL_DEFS["list_files"][1])
async def list_files() -> FilesResult | ErrorResult:
    return await safe_call("list_files", {})


@server.tool(description=TOOL_DEFS["read_text"][1])
async def read_text(source: str, offset: int = 0, limit: int = 6000) -> ReadResult | ErrorResult:
    return await safe_call("read_text", {"source": source, "offset": offset, "limit": limit})


@server.tool(description=TOOL_DEFS["write_english"][1])
async def write_english(text: str) -> WriteResult | ErrorResult:
    return await safe_call("write_english", {"text": text})


@server.tool(description=TOOL_DEFS["write_translation_index"][1])
async def write_translation_index(translations: list[str]) -> TranslationResult | ErrorResult:
    return await safe_call("write_translation_index", {"translations": translations})


@server.tool(description=TOOL_DEFS["transcribe"][1])
async def transcribe(source: str, language: str | None = None, prefer_pauses: bool = False, window_seconds: int = 25) -> TranscriptResult | ErrorResult:
    return await safe_call("transcribe", {"source": source, "language": language, "prefer_pauses": prefer_pauses, "window_seconds": window_seconds})


@server.tool(description=TOOL_DEFS["translate"][1])
async def translate(
    source: str, target_language: str, preserve_tables: bool = True
) -> TranslationResult | ErrorResult:
    return await safe_call(
        "translate",
        {"source": source, "target_language": target_language, "preserve_tables": preserve_tables},
    )


@server.tool(description=TOOL_DEFS["synthesize"][1])
async def synthesize(
    source: str, language: str, voice: str | None = None, format: Literal["wav", "mp3"] = "wav"
) -> SpeechResult | ErrorResult:
    return await safe_call(
        "synthesize", {"source": source, "language": language, "voice": voice, "format": format}
    )


@server.tool(description=TOOL_DEFS["ocr"][1])
async def ocr(
    source: str, table_format: Literal["html", "markdown"] = "html"
) -> OCRResult | ErrorResult:
    return await safe_call("ocr", {"source": source, "table_format": table_format})


@server.tool(description=TOOL_DEFS["subtitles"][1])
async def subtitles(
    source: str, target_language: str = "en", source_language: str | None = None,
    window_seconds: int = 20,
    timing_window_seconds: int = 5,
) -> SubtitlesResult | ErrorResult:
    return await safe_call(
        "subtitles",
        {"source": source, "target_language": target_language, "source_language": source_language, "window_seconds": window_seconds, "timing_window_seconds": timing_window_seconds},
    )


if __name__ == "__main__":
    server.run(transport="stdio")
