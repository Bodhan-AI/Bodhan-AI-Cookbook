"""Throwaway probe: does the chat route honour the target language?
Detects the output script by Unicode block, so we do not rely on how the terminal draws it."""

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy encoding
load_dotenv()
base = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai").strip().rstrip("/")
key = os.getenv("BODHAN_TRANSLATE_KEY", "").strip()
URL = base + "/v1/chat/completions"
H = {"Authorization": f"Bearer {key}"}

SCRIPTS = {
    "Devanagari": (0x0900, 0x097F), "Bengali": (0x0980, 0x09FF), "Tamil": (0x0B80, 0x0BFF),
    "Telugu": (0x0C00, 0x0C7F), "Latin": (0x0041, 0x007A),
}


def dominant_script(s: str) -> str:
    counts = {n: 0 for n in SCRIPTS}
    for ch in s:
        for n, (lo, hi) in SCRIPTS.items():
            if lo <= ord(ch) <= hi:
                counts[n] += 1
    best = max(counts, key=counts.get)
    return best if counts[best] else "none"


EN = "The meeting has been postponed to tomorrow."
HI = "बैठक कल तक के लिए स्थगित कर दी गई है।"

tests = [
    ("A target=ta", {"messages": [{"role": "user", "content": EN}], "target_language": "ta"}),
    ("A target=bn", {"messages": [{"role": "user", "content": EN}], "target_language": "bn"}),
    ("A target=te", {"messages": [{"role": "user", "content": EN}], "target_language": "te"}),
    ("B prompt Tamil", {"messages": [{"role": "user", "content": f"Translate to Tamil: {EN}"}]}),
    ("A hi->en", {"messages": [{"role": "user", "content": HI}], "target_language": "en"}),
    ("C bare hi input", {"messages": [{"role": "user", "content": HI}]}),
]

first = True
for name, extra in tests:
    r = httpx.post(URL, headers=H, json={"model": "indic-translate", **extra}, timeout=60)
    if r.status_code != 200:
        print(f"{name}: HTTP {r.status_code} {r.text[:200]}")
        continue
    d = r.json()
    msg = d["choices"][0]["message"]
    out = msg["content"]
    print(f"{name}: script={dominant_script(out)} | finish={d['choices'][0].get('finish_reason')} "
          f"| usage={d.get('usage')} | msg_keys={list(msg.keys())}")
    print("   output:", out[:90])
    if first:  # save one full response so we can read its exact shape
        Path("sample_data").mkdir(exist_ok=True)
        Path("sample_data/translate_chat_response.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        first = False