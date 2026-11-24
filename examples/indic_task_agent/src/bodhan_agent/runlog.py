"""trace.jsonl: one JSON line per event, written as the run happens."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def redact_paths(text: str) -> str:
    """Show paths relative to the working folder (or ~), so shared traces do not reveal local folder names."""
    cwd, home = str(Path.cwd()), str(Path.home())
    return text.replace(cwd + "/", "./").replace(cwd, ".").replace(home, "~")


# Result fields worth showing in a one-line progress summary, in this order.
HIGHLIGHTS = ("language", "dominant_script", "pages", "cues", "chunks", "passed", "verified", "speech_seconds_estimate", "audio_seconds")


def progress_line(kind: str, data: dict[str, Any]) -> str | None:
    """One human-readable line for a trace event, for watching a run live. None for events not worth showing."""
    if kind == "start":
        return f"task: {data.get('task')}  (LLM: {data.get('llm_model')}, budget INR {data.get('max_cost_inr')}, {data.get('max_steps')} turns)"
    if kind == "intake":
        files = data.get("files", [])
        parts = [f"{Path(f['path']).name} ({f.get('error') or f.get('kind')})" for f in files[:6]]
        return "inputs: " + (", ".join(parts) or "none") + (f" and {len(files) - 6} more" if len(files) > 6 else "")
    if kind == "llm":
        names = [c.get("name") for c in data.get("tool_calls", [])]
        return f"step {data.get('step')}  LLM {data.get('latency_ms', 0) / 1000:.1f} s -> " + (", ".join(names) or "(no tool call)")
    if kind == "tool":
        result = data.get("result") or {}
        if data.get("ok"):
            shown = [f"{k}={result[k]}" for k in HIGHLIGHTS if k in result and not isinstance(result[k], (dict, list))]
            mark = "ok"
        else:
            shown = [str((result.get("error") or {}).get("code", "error"))]
            mark = "refused" if data.get("tool") in ("finish", "save_english_text") else "error"
        cost = f"  INR {data['cost_inr']:.4f}" if data.get("cost_inr") else ""
        return f"        {mark:7} {data.get('tool')}  {data.get('latency_ms', 0) / 1000:.1f} s{cost}  " + " ".join(shown)
    if kind == "llm_error":
        return f"LLM error: {data.get('code')}"
    if kind == "finish":
        return f"finished: {data.get('status')}  INR {data.get('cost_inr', 0):.4f}  {data.get('duration_s')} s"
    return None


class RunLog:
    def __init__(self, out_dir: Path, progress: Callable[[str], None] | None = None):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / "trace.jsonl"
        self.path.write_text("", encoding="utf-8")
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._progress = progress

    def event(self, kind: str, **data: Any) -> None:
        line = {"t_s": round(time.monotonic() - self._t0, 3), "kind": kind, **data}
        encoded = redact_paths(json.dumps(line, ensure_ascii=False, default=str))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(encoded + "\n")
            if self._progress:
                text = progress_line(kind, data)
                if text:
                    self._progress(redact_paths(text))


def shorten(value: Any, *, max_str: int = 1500, max_items: int = 8) -> Any:
    """Keep tool results small enough for the LLM context and the trace. Full outputs stay in the files."""
    if isinstance(value, str):
        if len(value) <= max_str:
            return value
        return value[:max_str] + f" ... [{len(value) - max_str} more characters in the output file]"
    if isinstance(value, list):
        items = [shorten(v, max_str=max_str, max_items=max_items) for v in value[:max_items]]
        if len(value) > max_items:
            items.append(f"... {len(value) - max_items} more items")
        return items
    if isinstance(value, dict):
        return {k: shorten(v, max_str=max_str, max_items=max_items) for k, v in value.items()}
    return value
