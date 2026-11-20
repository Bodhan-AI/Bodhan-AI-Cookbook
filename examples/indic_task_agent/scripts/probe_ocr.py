"""Throwaway probe: send one image to the OCR API and print the RAW response, three ways."""

import base64
import sys
from pathlib import Path

import httpx

from bodhan_mcp.config import load_settings

sys.stdout.reconfigure(encoding="utf-8")
s = load_settings()
img = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_data/page.png")
print("image:", img, "|", img.stat().st_size, "bytes")
b64 = base64.b64encode(img.read_bytes()).decode()

for label, extra in [("no table_format", {}),
                     ("table_format=markdown", {"table_format": "markdown"}),
                     ("table_format=html", {"table_format": "html"})]:
    body = {
        "model": "indic-ocr",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}],
        **extra,
    }
    r = httpx.post(s.base_url + "/v1/chat/completions",
                   headers={"Authorization": f"Bearer {s.key_for('ocr')}"}, json=body, timeout=180)
    print(f"\n=== {label} -> HTTP {r.status_code} | {r.headers.get('content-type')} | {len(r.content)} bytes")
    print("RAW:", r.text[:700])