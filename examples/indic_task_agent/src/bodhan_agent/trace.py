"""Trace writer: one JSON object per line, flushed per event so a crash still leaves a readable run."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

# Only these keys may ever reach the trace from tool arguments; arguments are paths and codes by contract.
_SAFE_ARGS = {"path", "paths", "source_path", "text_path", "audio_path", "input_path", "out_dir", "target_language", "language", "voice", "gender", "style", "format", "script", "pages", "table_format", "dpi", "max_tokens", "max_cost_inr", "force", "instruction", "inputs", "target_chars", "facts"}


def safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that is not a path, a code or a number — the trace must never carry body text."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k not in _SAFE_ARGS:
            continue
        if isinstance(v, str) and len(v) > 300:
            out[k] = v[:300] + f"…({len(v)} chars)"
        elif isinstance(v, list):
            out[k] = [x if not isinstance(x, str) or len(x) <= 300 else x[:300] + "…" for x in v]
        else:
            out[k] = v
    return out


class Trace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "type": event, **fields}
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        return record
