"""trace.jsonl writer and compact result summaries."""

from __future__ import annotations

import hashlib
import json
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_ARGS_CHARS = 50_000
CURRENT_STEP: ContextVar[int] = ContextVar("agent_step", default=0)

_SUMMARY_KEYS = ("path", "transcript_path", "segments_path", "subtitles_path", "source_subtitles_path", "duration_s", "language",
                 "language_used", "target_language", "voice", "segments", "cues", "pages", "pages_ocr", "chars", "skipped", "note",
                 "notes", "embedded_artifacts", "missing")


def _short(value: Any, limit: int = 300) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [_short(v, limit) for v in value[:12]] + (["…"] if len(value) > 12 else [])
    if isinstance(value, dict):
        return {k: _short(v, limit) for k, v in list(value.items())[:20]}
    return value


def summarize_result(result: dict) -> dict:
    if not result.get("ok"):
        return {"error": result.get("error")}
    out: dict[str, Any] = {k: _short(result[k]) for k in _SUMMARY_KEYS if k in result}
    for key in ("verification",):
        v = result.get(key)
        if isinstance(v, dict):
            out["verification"] = {k: _short(v.get(k)) for k in ("status", "problems", "warnings", "mean_similarity", "duration") if k in v}
            bt = (v.get("checks") or {}).get("back_translation")
            if bt:
                out["verification"]["back_translation"] = {k: bt.get(k) for k in ("language", "mean_chrf", "min_length_ratio")}
    for key in ("detection", "source_detection"):
        d = result.get(key)
        if isinstance(d, dict):
            out[key] = {k: d.get(k) for k in ("language", "script", "confidence")}
    for key in ("transcript", "content", "translation", "first_cues", "heard"):
        block = result.get(key)
        if isinstance(block, dict):
            out[f"{key}_preview"] = _short(block.get("text_preview", ""), 160)
            if block.get("injection_warning"):
                out["injection_warning"] = block["injection_warning"]
    if "files" in result:
        out["files"] = [{k: f.get(k) for k in ("name", "kind", "duration_s", "pages")} for f in result["files"][:20]]
    return out


class TraceWriter:
    """Appends one JSON object per line. Nested events (API calls, retries,
    verification) from the tools are attributed to the step that caused them."""

    def __init__(self, path: Path, append: bool = False):
        self.path = path
        self.t0 = time.monotonic()
        self.events = 0
        if not append:  # a resumed run continues the same trace
            path.write_text("", encoding="utf-8")

    def write(self, event: dict[str, Any]) -> None:
        record = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "t": round(time.monotonic() - self.t0, 3), **event}
        record.setdefault("step", CURRENT_STEP.get())
        args = record.pop("arguments", None)
        line = _short(record, 2000)
        if args is not None:  # tool arguments are kept whole so a run can be replayed; huge ones are hashed
            raw = json.dumps(args, ensure_ascii=False, default=str)
            line["arguments"] = args if len(raw) <= MAX_ARGS_CHARS else {"sha256": hashlib.sha256(raw.encode()).hexdigest(), "chars": len(raw),
                                                                          "preview": raw[:500]}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        self.events += 1

    def tool_event(self, event: dict[str, Any]) -> None:
        """Hook given to RunContext/BodhanClient for nested events."""
        self.write({**event, "nested": True})
