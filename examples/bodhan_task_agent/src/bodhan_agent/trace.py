"""Append-only JSONL trace: one line per step, flushed immediately so a crash keeps the history.

Every LLM response is stored verbatim (content + tool calls), which makes a run replayable:
``python -m bodhan_agent --replay out/trace.jsonl --out out2`` feeds the recorded decisions back
through the same agent code, and the Bodhan cache makes the replayed tool calls free.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET = re.compile(r"\b(sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,})")


def _redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return _SECRET.sub("[redacted]", obj)
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class Trace:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self._seq = 0
        self._t0 = time.monotonic()
        self.events: list[dict[str, Any]] = []

    def write(self, type_: str, step: int, **fields: Any) -> dict[str, Any]:
        self._seq += 1
        event = {
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "t_ms": int((time.monotonic() - self._t0) * 1000),
            "step": step,
            "type": type_,
            **_redact(fields),
        }
        self.events.append(event)
        self._fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        return event

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def replay_responses(events: list[dict[str, Any]], new_out: Path) -> list[dict[str, Any]]:
    """The recorded LLM responses, with paths under the original run's output folder rebased onto ``new_out``."""
    start = next((e for e in events if e["type"] == "run_start"), {})
    old = start.get("out_dir")
    new = str(Path(new_out).resolve())
    out = []
    for e in events:
        if e["type"] != "llm":
            continue
        resp = json.loads(json.dumps(e["response"]))
        if old and old != new:
            for tc in resp.get("tool_calls") or []:
                tc["arguments"] = tc["arguments"].replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1])
        out.append(resp)
    return out
