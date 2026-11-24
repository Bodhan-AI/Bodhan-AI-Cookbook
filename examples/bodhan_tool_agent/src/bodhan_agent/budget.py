"""Pre-call cost estimates so the agent can refuse a call *before* it overspends."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_mcp import config, media


def _text_len(args: dict, field: str = "text") -> int:
    if args.get(field):
        return len(str(args[field]))
    p = args.get(f"{field}_path") or args.get("transcript_path")
    if p and Path(p).is_file():
        body = Path(p).read_text(encoding="utf-8", errors="replace")
        if p.endswith(".json"):
            try:
                body = json.loads(body).get("text", body)
            except (json.JSONDecodeError, AttributeError):
                pass
        return len(body)
    return 0


def estimate_cost(tool: str, args: dict) -> float:
    try:
        if tool == "synthesize_speech":
            chars = _text_len(args)
            tts = chars / 10000 * config.PRICE_SPEAK_PER_10K_CHARS
            verify = (chars / 14) / 60 * config.PRICE_TRANSCRIBE_PER_MIN if args.get("verify", True) else 0
            return round(tts * 1.15 + verify, 4)  # 15% headroom for regenerated sentences
        if tool in ("translate_text", "create_subtitles"):
            return round(_text_len(args) / 2.5 / 10000 * config.PRICE_TRANSLATE_PER_10K_OUT_TOKENS, 4)
        if tool == "transcribe_audio":
            p = Path(args.get("path", ""))
            if p.is_file():
                return round(media.probe(p)["duration_sec"] / 60 * config.PRICE_TRANSCRIBE_PER_MIN, 4)
        if tool == "ocr_document":
            p = Path(args.get("path", ""))
            pages = media.pdf_page_count(p) if p.suffix.lower() == ".pdf" and p.is_file() else 1
            return round(pages * config.PRICE_OCR_PER_IMAGE, 4)
    except Exception:  # noqa: BLE001 - an estimate must never break the run
        return 0.0
    return 0.0
