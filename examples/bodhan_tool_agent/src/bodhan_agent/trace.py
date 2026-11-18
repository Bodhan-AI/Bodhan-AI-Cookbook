"""Execution tracing for Bodhan Task Agent.

Appends step records in JSON Lines format to out/trace.jsonl.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from bodhan_mcp.common import get_ist_now_iso


class TraceLogger:
    """Manages append-only trace logging in JSONL format."""

    def __init__(self, trace_file_path: Path):
        self.trace_file_path = trace_file_path
        self.trace_file_path.parent.mkdir(parents=True, exist_ok=True)
        # Clear or start new trace
        self.trace_file_path.write_text("", encoding="utf-8")
        self.records = []

    def log_llm_turn(
        self,
        turn_num: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        finish_reason: str = "stop",
        latency_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Appends an LLM turn record (turn number, tokens, finish reason, latency) to trace.jsonl."""
        record = {
            "type": "llm_turn",
            "turn": turn_num,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "finish_reason": finish_reason,
            "latency_ms": round(latency_ms, 2),
            "timestamp": get_ist_now_iso(),
        }
        self.records.append(record)
        with open(self.trace_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def log_step(
        self,
        step_num: int,
        step_type: str,
        name: str,
        arguments: Dict[str, Any],
        result_summary: str,
        latency_seconds: float,
        cost_inr: float,
        cumulative_cost_inr: float,
        details: Optional[Dict[str, Any]] = None,
        injection_suspected: Optional[bool] = None,
        llm_ms: float = 0.0,
        tool_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Appends a single trace event to trace.jsonl."""
        record = {
            "step": step_num,
            "type": step_type,
            "name": name,
            "arguments": arguments,
            "result_summary": result_summary,
            "latency_seconds": round(latency_seconds, 3),
            "llm_ms": round(llm_ms, 2),
            "tool_ms": round(tool_ms, 2),
            "cost_inr": round(cost_inr, 6),
            "cumulative_cost_inr": round(cumulative_cost_inr, 6),
            "timestamp": get_ist_now_iso(),
        }
        if details:
            record["details"] = details
        if injection_suspected is not None:
            record["injection_suspected"] = injection_suspected

        self.records.append(record)
        with open(self.trace_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return record
