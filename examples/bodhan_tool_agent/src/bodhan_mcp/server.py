"""
Bodhan MCP Server factory.

Creates and configures the FastMCP server and registers all 7 tool modules.
All stdio output from this process is strictly on stderr so the JSON-RPC
transport on stdout is never corrupted.
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys

from mcp.server.fastmcp import FastMCP

from bodhan_core.cache import ToolCache
from bodhan_core.client import BodhanClient
from bodhan_core.cost import CostMeter
from bodhan_core.workspace import Workspace

# Redirect the root logger to stderr so nothing leaks onto stdout.
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

log = logging.getLogger(__name__)


def create_server() -> FastMCP:
    """Build and return a fully-configured FastMCP server instance."""
    mcp = FastMCP("Bodhan Tool Server")

    # ── shared infrastructure ────────────────────────────────────────────────
    cache_dir = pathlib.Path(
        os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")
    )
    cache = ToolCache(cache_dir)
    cost_meter = CostMeter()
    client = BodhanClient(cache=cache, cost_meter=cost_meter)
    workspace = Workspace(
        pathlib.Path(os.environ.get("BODHAN_WORKSPACE", "."))
    )
    workspace.ensure_dirs()

    # ── register all tool modules ────────────────────────────────────────────
    from .tools import (  # noqa: PLC0415
        detect_language,
        inspect_media,
        list_voices,
        ocr,
        speak,
        transcribe,
        translate,
    )

    for tool_module in (
        inspect_media,
        detect_language,
        transcribe,
        translate,
        speak,
        ocr,
        list_voices,
    ):
        tool_module.register(mcp, client, workspace, cost_meter)
        log.info("Registered tool module: %s", tool_module.__name__)

    return mcp
