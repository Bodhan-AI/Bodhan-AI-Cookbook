"""Trace writer: appends JSON lines to trace.jsonl."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _sanitize_for_trace(obj: Any, depth: int = 0) -> Any:
    """Summarize data structures so trace.jsonl stays compact and never has media/base64."""
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            k_lower = k.lower()
            if any(term in k_lower for term in ["key", "token", "secret", "password", "auth"]):
                sanitized[k] = "[REDACTED]"
            elif "base64" in k_lower or "bytes" in k_lower or "audio_data" in k_lower:
                sanitized[k] = f"[BINARY_DATA {len(str(v))} chars]"
            elif isinstance(v, str) and len(v) > 500:
                sanitized[k] = v[:200] + f"... [{len(v)} total chars]"
            else:
                sanitized[k] = _sanitize_for_trace(v, depth + 1)
        return sanitized
    elif isinstance(obj, list):
        if len(obj) > 10:
            return [_sanitize_for_trace(item, depth + 1) for item in obj[:5]] + [f"... and {len(obj) - 5} more items"]
        return [_sanitize_for_trace(item, depth + 1) for item in obj]
    elif isinstance(obj, str):
        if len(obj) > 500:
            return obj[:200] + f"... [{len(obj)} total chars]"
        return obj
    return obj


class TraceWriter:
    """Writes detailed structured execution trace to trace.jsonl."""

    def __init__(self, out_dir: str | Path):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.trace_file = self.out_dir / "trace.jsonl"
        self._file_handle = open(self.trace_file, "a", encoding="utf-8")
        logger.info("TraceWriter initialized at %s", self.trace_file)

    def write_record(self, record: dict[str, Any]) -> None:
        """Write a single event/step record to trace.jsonl."""
        record_with_meta = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "time_epoch": round(time.time(), 4),
            **_sanitize_for_trace(record),
        }
        line = json.dumps(record_with_meta, ensure_ascii=False)
        self._file_handle.write(line + "\n")
        self._file_handle.flush()

    def record_plan(self, plan: list[Any], rationale: str = "") -> None:
        """Record the initial or revised execution plan."""
        steps_summary = []
        for step in plan:
            if hasattr(step, "model_dump"):
                d = step.model_dump()
            elif isinstance(step, dict):
                d = step
            else:
                d = {"id": getattr(step, "id", str(step))}
            steps_summary.append({
                "id": d.get("id"),
                "tool": d.get("tool"),
                "purpose": d.get("purpose"),
                "depends_on": d.get("depends_on", []),
            })
        self.write_record({
            "event": "plan",
            "num_steps": len(plan),
            "steps": steps_summary,
            "rationale": rationale,
        })

    def record_step_start(self, step_id: str, tool: str, args: dict[str, Any]) -> None:
        """Record start of a tool step."""
        self.write_record({
            "event": "step_start",
            "step_id": step_id,
            "tool": tool,
            "args_summary": args,
        })

    def record_step_complete(
        self,
        step_id: str,
        tool: str,
        args: dict[str, Any],
        result: dict[str, Any],
        latency_ms: float,
        cost_inr: float,
        cached: bool = False,
    ) -> None:
        """Record completion of a tool step."""
        self.write_record({
            "event": "step_complete",
            "step_id": step_id,
            "tool": tool,
            "args_summary": args,
            "result_summary": result,
            "latency_ms": round(latency_ms, 2),
            "cost_inr": round(cost_inr, 4),
            "cache_hit": cached,
        })

    def record_step_failed(
        self,
        step_id: str,
        tool: str,
        args: dict[str, Any],
        error: str,
        latency_ms: float,
        attempts: int,
    ) -> None:
        """Record failure of a tool step."""
        self.write_record({
            "event": "step_failed",
            "step_id": step_id,
            "tool": tool,
            "args_summary": args,
            "error": error,
            "latency_ms": round(latency_ms, 2),
            "attempts": attempts,
        })

    def record_decision(self, decision_type: str, details: dict[str, Any], rationale: str = "") -> None:
        """Record an LLM decision (interpret, replan, judgment)."""
        self.write_record({
            "event": "decision",
            "decision_type": decision_type,
            "details": details,
            "rationale": rationale,
        })

    def record_verification(self, verifications: list[dict[str, Any]]) -> None:
        """Record verification outputs."""
        self.write_record({
            "event": "verification",
            "verifications": verifications,
        })

    def close(self) -> None:
        """Flush and close file handle."""
        try:
            if not self._file_handle.closed:
                self._file_handle.flush()
                self._file_handle.close()
        except Exception as e:
            logger.warning("Error closing trace handle: %s", e)
