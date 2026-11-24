from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.lowlevel import Server

from bodhan_mcp.config import Settings
from bodhan_mcp.core.client import BodhanClient
from bodhan_mcp.core.registry import ToolRegistry
from bodhan_mcp.tools.ocr import OcrTool
from bodhan_mcp.tools.synthesize import SynthesizeTool
from bodhan_mcp.tools.transcribe import TranscribeTool
from bodhan_mcp.tools.translate import TranslateTool

INSTRUCTIONS = (
    "Tools for Bodhan's Indic language models. Pass files by path, never as inline content: inputs are read from "
    "the server's input folder (or other allowed folders) and results are written to its output folder, with the "
    "path returned. Every result includes `usage` with the estimated cost in rupees. Errors are JSON objects with "
    "status, code, message, hint and retryable; follow the hint before retrying."
)


def build_server(settings: Settings | None = None, client: BodhanClient | None = None) -> Server[Any]:
    settings = settings or Settings.from_env()
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    bodhan = client or BodhanClient(settings)
    registry = ToolRegistry(
        [
            TranscribeTool(bodhan, settings),
            TranslateTool(bodhan, settings),
            SynthesizeTool(bodhan, settings),
            OcrTool(bodhan, settings),
        ]
    )

    @asynccontextmanager
    async def lifespan(_: Server[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await bodhan.aclose()

    return Server(
        "bodhan",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        on_list_tools=registry.list_tools,
        on_call_tool=registry.call_tool,
    )
