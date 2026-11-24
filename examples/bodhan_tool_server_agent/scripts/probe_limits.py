"""Probe the real size limits behind translate CHUNK_CHARS and speak CHUNK_CHARS (tiny spend, ~Rs 1).
Run: uv run python scripts/probe_limits.py [translate|speak]"""
import asyncio
import io
import json
import os
import sys
import wave
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("BODHAN_WORKDIR", ".bodhan_work")
os.environ["BODHAN_MAX_COST_INR"] = "2.0"
from bodhan_mcp import core
from bodhan_mcp.languages import pick_voice

SRC = " ".join(p.read_text() for p in sorted(Path("test_data/audio/hi_in").glob("*.txt")))  # real Hindi sentences


def cut(n: int) -> str:
    return SRC[:n].rsplit(" ", 1)[0]


async def translate():
    for n in (1200, 2400, 4000):
        t = cut(n)
        body = {"model": "indic-translate", "messages": [{"role": "user", "content": t}], "target_language": "en"}
        try:
            j = (await core.post("translate", "/v1/chat/completions", json=body)).json()
            ch = j["choices"][0]
            out = ch["message"]["content"]
            print(f"translate {len(t)} chars -> {len(out)} chars, finish={ch.get('finish_reason')}, truncated={j.get('truncated')}, "
                  f"completion_tokens={(j.get('usage') or {}).get('completion_tokens')}, tail: ...{out[-60:]!r}")
        except Exception as e:
            print(f"translate {len(t)} chars: ERROR {e}")
            break


async def speak():
    for n in (220, 400, 600):
        t = cut(n)
        try:
            r = await core.post("speak", "/v1/audio/speech", json={"model": "indic-speak", "input": t, "voice": pick_voice("hi", None), "instructions": json.dumps({"lang": "hi"})})
            with wave.open(io.BytesIO(r.content)) as w:
                d = w.getnframes() / w.getframerate()
            print(f"speak {len(t)} chars -> {d:.1f} s audio ({len(t) / d:.1f} chars/s)")
        except Exception as e:
            print(f"speak {len(t)} chars: ERROR {e}")
            break


asyncio.run({"translate": translate, "speak": speak}[sys.argv[1]]() if len(sys.argv) > 1 else asyncio.gather(translate(), speak()))
