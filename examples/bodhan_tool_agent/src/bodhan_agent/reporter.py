#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .verifier import sanitize_arguments
from bodhan_mcp.client import InrLedger


@dataclass
class Reporter:
    """Emits trace.jsonl, report.json, and result.md to output dir."""

    out_dir: Path
    artifacts_dir: Path
    ledger: InrLedger
    max_steps: int
    max_cost_inr: float

    trace_path: Path = field(init=False)
    _steps: list[dict[str, Any]] = field(default_factory=list)
    _start_time: float = field(default=0.0)
    _flushed_count: int = field(default=0)

    def __post_init__(self):
        self.trace_path = self.out_dir / "trace.jsonl"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._start_time = time.time()

    def log_step_start(self, step: int, tool: str, arguments: dict):
        """Record step start for tracing."""
        entry = {
            "step": step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plan_rationale": f"Starting step {step}: calling {tool}",
            "tool": tool,
            "arguments": sanitize_arguments(arguments),
            "result_summary": "",
            "latency_ms": 0,
            "cost_inr": 0.0,
            "cumulative_cost_inr": round(self.ledger.total, 4),
            "status": "started",
        }
        self._steps.append(entry)
        self._flush_trace()

    def log_step_end(
        self, step: int, tool: str, arguments: dict, result: dict,
        latency_ms: float, cost_inr: float, cached: bool = False,
    ):
        """Record step completion for tracing."""
        summary = self._extract_summary(result)
        entry = {
            "step": step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plan_rationale": f"Completed step {step}: called {tool}",
            "tool": tool,
            "arguments": sanitize_arguments(arguments),
            "result_summary": summary,
            "latency_ms": round(latency_ms, 1),
            "cost_inr": round(cost_inr, 4),
            "cumulative_cost_inr": round(self.ledger.total, 4),
            "status": "completed",
            "cached": cached,
        }
        self._steps.append(entry)
        self._flush_trace()

    def _flush_trace(self):
        """Append new steps to trace.jsonl."""
        with open(self.trace_path, "a", encoding="utf-8") as f:
            for entry in self._steps[self._flushed_count:]:
                f.write(json.dumps(
                    entry, ensure_ascii=False, default=str
                ) + "\n")
        self._flushed_count = len(self._steps)

    def _extract_summary(self, result: dict) -> str:
        """Extract a concise summary from a tool result."""
        if "error" in result:
            return f"ERROR: {result.get('message', 'Unknown')}"
        if "result" in result:
            r = result["result"]
            try:
                data = json.loads(r)
                if isinstance(data, dict):
                    keys_to_show = [
                        "text", "translation", "output_file",
                        "duration_sec", "pages", "language",
                        "detected_script",
                    ]
                    parts = []
                    for k in keys_to_show:
                        if k in data and data[k]:
                            v = data[k]
                            if isinstance(v, str) and len(v) > 50:
                                v = v[:50] + "..."
                            parts.append(f"{k}={v}")
                    return ", ".join(parts) if parts else r[:100]
                elif isinstance(data, str):
                    return data[:200]
            except (json.JSONDecodeError, TypeError):
                pass
            return r[:200]
        return str(result)[:200]

    async def finalize(self, agent_result: dict, elapsed_sec: float):
        """Write report.json and result.md based on final state."""
        executed_steps = []
        cached_savings = 0.0
        for entry in getattr(self, "_steps", []):
            cached = entry.get("cached", False)
            cost = entry.get("cost_inr", 0.0)
            if cached:
                cached_savings += cost
            executed_steps.append({
                "tool": entry.get("tool", "?"),
                "cost_inr": cost,
                "result_summary": entry.get("result_summary", ""),
                "cached": cached,
            })
        agent_result["steps"] = executed_steps

        status = agent_result.get("status", "failed")
        if status == "budget_exceeded":
            status = "budget_insufficient"
        verification_results = agent_result.get(
            "verification_results", {}
        )
        missed_targets = agent_result.get("missed_targets", [])

        ver_map = {}
        for k, v in verification_results.items():
            ver_map[k] = {"passed": v.get("passed", False)}

        art_list = []
        if self.artifacts_dir.exists():
            art_list = [str(a) for a in self.artifacts_dir.iterdir()]

        # Collect AI-generation flags from verification results
        ai_generated_artifacts: list[str] = []
        for k, v in verification_results.items():
            if k.startswith("tts_") and v.get("passed") is True:
                ai_generated_artifacts.append(k.replace("tts_", ""))
            elif k.startswith("tts_") and v.get("passed") is False:
                reason = v.get("details", {}).get("error", "")
                if reason:
                    missed_targets.append(f"TTS {k}: {reason}")

        report = {
            "status": status,
            "steps_used": agent_result.get("steps_used", 0),
            "max_steps": self.max_steps,
            "total_cost_inr": round(self.ledger.total, 4),
            "cached_savings_inr": round(cached_savings, 4),
            "max_cost_inr": self.max_cost_inr,
            "elapsed_sec": round(elapsed_sec, 2),
            "assumptions": agent_result.get("assumptions", []),
            "verification_results": ver_map,
            "ai_generated_audio_languages": ai_generated_artifacts,
            "artifacts": art_list,
            "missed_targets": missed_targets,
        }

        report_path = self.out_dir / "report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        result_md = self._build_result_md(agent_result, report)
        result_md_path = self.out_dir / "result.md"
        result_md_path.write_text(result_md, encoding="utf-8")

    def _build_result_md(self, agent_result: dict, report: dict) -> str:
        """Build human-readable result.md content."""
        lines = []
        lines.append("# Task Result")
        lines.append("")
        tc = report['total_cost_inr']
        mc = report['max_cost_inr']
        lines.append(f"**Status:** {report['status']}")
        lines.append(
            f"**Steps used:** {report['steps_used']} / "
            f"{report['max_steps']}"
        )
        lines.append(f"**Total cost:** \u20b9{tc:.4f} / \u20b9{mc:.2f}")
        lines.append(f"**Elapsed time:** {report['elapsed_sec']:.2f}s")
        lines.append("")

        steps = agent_result.get("steps", [])
        if steps:
            lines.append("## What Was Done")
            lines.append("")
            for s in steps:
                cost = s.get("cost_inr", 0.0)
                tool = s.get("tool", "?")
                summary = s.get("result_summary", "")[:60]
                lines.append(f"- `{tool}` — ₹{cost:.4f} — {summary}")
            lines.append("")

        final_answer = agent_result.get("final_answer", "")
        if final_answer:
            lines.append("## Final Answer")
            lines.append("")
            lines.append(final_answer)
            lines.append("")

        assumptions = agent_result.get("assumptions", [])
        if assumptions:
            lines.append("## Assumptions")
            lines.append("")
            for a in assumptions:
                lines.append(f"- {a}")
            lines.append("")

        verification = report.get("verification_results", {})
        if verification:
            lines.append("## Verification Results")
            lines.append("")
            for name, details in verification.items():
                passed = details.get("passed", False)
                icon = "PASS" if passed else "FAIL"
                lines.append(f"- **{name}**: [{icon}]")
            lines.append("")

        missed = report.get("missed_targets", [])
        if missed:
            lines.append("## Missed Targets")
            lines.append("")
            for m in missed:
                lines.append(f"- {m}")
            lines.append("")

        rel_artifacts = []
        if self.artifacts_dir.exists():
            rel_artifacts = [p for p in self.artifacts_dir.iterdir() if p.is_file()]
        if rel_artifacts:
            lines.append("## Artifacts")
            lines.append("")
            for art_path in sorted(rel_artifacts, key=lambda p: p.name):
                rel = art_path.relative_to(self.out_dir)
                is_ai_audio = any(
                    art_path.name.endswith(ext)
                    for ext in (".wav", ".mp3", ".ogg")
                ) and report.get("ai_generated_audio_languages")
                label = " [AI-generated audio]" if is_ai_audio else ""
                lines.append(f"- [`{rel}`]({rel}){label}")
            lines.append("")

        ai_langs = report.get("ai_generated_audio_languages", [])
        if ai_langs:
            lines.append("## AI Disclosure")
            lines.append("")
            lines.append(
                "**Generated audio** was produced by Bodhan Indic-Speak "
                f"for language(s): {', '.join(ai_langs)}. "
                "All Indian-language text originated from Bodhan AI tools "
                "(Indic-Transcribe, Indic-OCR, Indic-Translate, Indic-Speak). "
                "The planner LLM was used only for task planning and English summaries."
            )
            lines.append("")

        return "\n".join(lines)
