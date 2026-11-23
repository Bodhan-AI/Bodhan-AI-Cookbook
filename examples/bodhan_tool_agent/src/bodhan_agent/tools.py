import asyncio
import re
import subprocess
from pathlib import Path

import bodhan_mcp.server as server
from bodhan_mcp import audio


def load_tools():
    """Read the tool list from the MCP server itself, so the agent and server never disagree."""
    listed = asyncio.run(server.mcp.list_tools())
    schemas, functions = [], {}
    for tool in listed:
        schemas.append({
            "type": "function",
            "function": {"name": tool.name, "description": (tool.description or "")[:500], "parameters": tool.inputSchema},
        })
        functions[tool.name] = getattr(server, tool.name)
    return schemas, functions


def _clean(raw) -> Path:
    return Path(str(raw or "").strip().strip('"').strip("'"))


def _file_chars(raw) -> int:
    try:
        return len(_clean(raw).read_text(encoding="utf-8"))
    except Exception:
        return 0


def _audio_seconds(raw) -> float:
    ffmpeg = audio.find_ffmpeg()
    path = _clean(raw)
    if not ffmpeg or not path.is_file():
        return 30.0
    result = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True, text=True)
    found = re.search(r"Duration: (\d+):(\d+):([\d.]+)", result.stderr)
    if not found:
        return 30.0
    hours, minutes, seconds = found.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def estimate_cost(tool: str, args: dict) -> float:
    """Cost in rupees BEFORE the call. Cache hits cost 0, so this is a safe upper bound."""
    if tool == "speak_text":
        chars = len(args.get("text") or "") or _file_chars(args.get("text_path"))
        return chars / 10000 * 6.0
    if tool == "ocr_document":
        path = _clean(args.get("file_path"))
        pages = 1
        if path.suffix.lower() == ".pdf" and path.is_file():
            try:
                import pypdfium2
                pages = len(pypdfium2.PdfDocument(str(path)))
            except Exception:
                pages = 1
        return min(pages, int(args.get("max_pages", 20))) * 0.20
    if tool == "transcribe_audio":
        return _audio_seconds(args.get("audio_path")) / 60 * 0.10
    if tool in ("translate_text", "translate_file"):
        chars = len(args.get("text") or "") or _file_chars(args.get("text_path"))
        return chars / 2 / 10000 * 0.20
    if tool == "make_subtitles":
        return _audio_seconds(args.get("audio_path")) / 60 * 0.10 + 0.05
    return 0.0