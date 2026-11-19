"""MCP server over stdio:  python -m bodhan_mcp [--out-dir DIR]"""

import argparse
import functools
import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import languages as L
from . import tools
from .bodhan import Bodhan, BodhanError, load_env

INSTRUCTIONS = """Tools for Indian-language speech, translation, text-to-speech and OCR (Bodhan models).
Pass files by path; tools write outputs to files and return their paths. Typical chains:
audio -> transcribe_audio -> translate_text / make_subtitles; scan -> ocr_document -> translate_text;
text -> translate_text -> synthesize_speech. Errors are JSON with code, message and hint."""


def _safe(fn):
    """Turn every failure into an MCP tool error with a JSON body, so bad input never crashes the client."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except BodhanError as e:
            raise ToolError(json.dumps(e.to_dict(), ensure_ascii=False)) from e
        except Exception as e:  # noqa: BLE001 - report, don't crash
            raise ToolError(json.dumps({"code": "internal_error", "message": repr(e)[:300], "hint": "",
                                        "retryable": False})) from e

    return wrapper


def build_server(out_dir: Path, bodhan: Bodhan | None = None) -> MCPServer:
    tools.configure(bodhan or Bodhan(), out_dir)
    server = MCPServer("bodhan", instructions=INSTRUCTIONS, log_level="WARNING")
    for fn in tools.TOOLS:
        server.add_tool(_safe(fn))

    @server.resource("bodhan://voices", mime_type="application/json",
                     description="Indic-Speak voice library: language -> {female: [...], male: [...]}")
    def voices() -> str:
        return json.dumps(L.VOICES, ensure_ascii=False)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="bodhan_outputs", help="where tools write their output files")
    args = parser.parse_args()
    load_env()
    build_server(Path(args.out_dir)).run("stdio")


if __name__ == "__main__":
    main()
