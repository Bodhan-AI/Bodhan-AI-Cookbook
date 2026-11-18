#!/usr/bin/env python3
"""A cold read of the MCP server: an off-the-shelf client, the tool descriptions alone.

Spawns `python -m bodhan_mcp` over stdio, the way any MCP client would, and
does what the assignment asks a stranger's client to do: list the tools, read
the resources, call a free tool, and act on a structured error. No paid call is
made by this script.

    python scripts/cold_read.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WANTED = {"probe_media", "detect_language", "transcribe_audio", "ocr_document", "translate_text", "speak_text"}


def text_of(result) -> str:
    return "\n".join(getattr(part, "text", "") for part in result.content)


def resource_text(result) -> str:
    return "\n".join(getattr(part, "text", "") for part in result.contents)


def error_of(result) -> dict:
    body = text_of(result)
    return json.loads(body[body.index("{") :])["error"]


def _tone() -> Path:
    """A second of 440 Hz, written with the stdlib, so the probe check needs no media file."""
    import math
    import wave

    path = Path(tempfile.mkdtemp(prefix="bodhan-cold-")) / "tone.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        frames = bytearray()
        for i in range(16000):
            sample = int(0.2 * 32767 * math.sin(2 * math.pi * 440 * i / 16000))
            frames += sample.to_bytes(2, "little", signed=True)
        handle.writeframes(bytes(frames))
    return path


async def check(client) -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    tools = {t.name: t for t in (await client.list_tools()).tools}
    schema = {name: getattr(t, "input_schema", None) or t.inputSchema for name, t in tools.items()}
    rows.append(("six tools, each with a description and typed fields", set(tools) == WANTED and all(schema[n]["properties"] for n in tools), ", ".join(sorted(tools))))
    rows.append(("translate_text needs only source_path and target_language", set(schema["translate_text"]["required"]) == {"source_path", "target_language"}, str(sorted(schema["translate_text"]["required"]))))

    resources = {str(r.uri) for r in (await client.list_resources()).resources}
    rows.append(("three resources advertised", {"bodhan://languages", "bodhan://voices", "bodhan://pricing"} <= resources, ", ".join(sorted(resources))))
    voices = resource_text(await client.read_resource("bodhan://voices"))
    rows.append(("the voice resource names a Tamil female voice", "Anitha" in voices, f"{len(voices)} chars"))

    sample = ROOT / "sample_data" / "sample_hindi.wav"
    if not sample.is_file():
        sample = _tone()
    inventory = json.loads(text_of(await client.call_tool("probe_media", {"paths": [str(sample)]})))
    item = inventory["items"][0]
    rows.append(("probe_media reports kind, duration and cost", item["kind"] == "audio" and item["duration_s"] and item["est_asr_cost_inr"] is not None, f"{item['duration_s']}s, Rs {item['est_asr_cost_inr']}"))

    with tempfile.TemporaryDirectory() as tmp:
        english = Path(tmp) / "note.txt"
        english.write_text("The meeting moved to Thursday at ten.", encoding="utf-8")
        guess = json.loads(text_of(await client.call_tool("detect_language", {"text_path": str(english)})))
        rows.append(("detect_language says English for plain Latin text", guess["language"] == "en", str(guess)))
        refused = await client.call_tool("translate_text", {"source_path": str(english), "target_language": "fr"})
        err = error_of(refused)
        rows.append(("French is refused with a code and a hint", refused.is_error and err["code"] == "UNSUPPORTED_LANGUAGE" and bool(err["hint"]), err["message"][:70]))
        missing = await client.call_tool("transcribe_audio", {"audio_path": str(Path(tmp) / "nope.wav")})
        rows.append(("a missing file is a structured error, not a traceback", missing.is_error and "Traceback" not in text_of(missing), error_of(missing)["code"]))
    return rows


async def main_async() -> int:
    from mcp import StdioServerParameters
    from mcp.client import Client

    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], cwd=str(ROOT), env={**os.environ})
    async with Client(params) as client:
        rows = await check(client)
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<58} {detail}")
    failed = [r for r in rows if not r[1]]
    print(f"\n{len(rows) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
