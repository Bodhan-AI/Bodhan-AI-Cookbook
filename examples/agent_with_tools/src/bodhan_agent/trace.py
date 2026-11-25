"""Local run record: trace.jsonl (one line per step + a final summary line), report.json and result.md."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class RunRecorder:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.artifacts_dir = out_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = out_dir / "trace.jsonl"
        self.trace_path.write_text("", encoding="utf-8")
        self.started = time.time()
        self.steps = 0
        self.cost_inr = 0.0

    def _write(self, line: dict) -> None:
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def step(self, **fields: Any) -> None:
        """Append one trace line (kind: plan | tool | verify; plan / tool / args / result_summary, latency_s, cost_inr)."""
        self.steps += 1
        self.cost_inr += fields.get("cost_inr", 0.0)
        self._write({"step": self.steps, "t": round(time.time() - self.started, 3), **fields})

    def artifacts(self) -> list[str]:
        return sorted(p.relative_to(self.out_dir).as_posix() for p in self.artifacts_dir.rglob("*") if p.is_file())

    def finish(self, *, status: str, task: str, answer: str, trace_url: str | None, max_steps: int, max_cost_inr: float,
               verifications: list[dict] | None = None, stop_reason: str = "", llm_turns: int = 0, call_timeout_s: float = 0.0,
               assumptions: list[str] | None = None, security_flags: list[dict] | None = None) -> None:
        checks = [{k: v for k, v in v.items() if k != "fingerprint"} for v in (verifications or [])]
        failed = [c for c in checks if c["status"] == "failed"]
        unverified = [c for c in checks if c["status"] == "skipped"]
        artifacts = self.artifacts()
        elapsed = round(time.time() - self.started, 2)
        report = {
            "status": status,
            "stop_reason": stop_reason or None,
            "steps": self.steps,
            "llm_turns": llm_turns,
            "total_cost_inr": round(self.cost_inr, 6),
            "total_time_s": elapsed,
            "budgets": {"max_steps": max_steps, "max_cost_inr": max_cost_inr, "call_timeout_s": call_timeout_s},
            "verification": checks,
            "verification_summary": {"passed": sum(c["status"] == "passed" for c in checks), "failed": len(failed), "skipped": len(unverified)},
            "assumptions": assumptions or [],
            "security_flags": security_flags or [],
            "artifacts": artifacts,
            "langfuse_trace_url": trace_url,
            "notes": ["Cost counts Bodhan tool calls only; LLM token cost is not included."],
        }
        (self.out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        self._write({"step": self.steps + 1, "kind": "summary", "status": status, "stop_reason": stop_reason or None, "steps": self.steps,
                     "cost_inr": round(self.cost_inr, 6), "total_time_s": elapsed, "verification": report["verification_summary"], "artifacts": artifacts})

        md = [f"# Result\n\n**Status:** {status}\n\n**Task:** {task}\n\n## Answer\n\n{answer}\n"]
        if artifacts:
            md.append("## Artifacts\n\n" + "\n".join(f"- [{a}]({a})" for a in artifacts) + "\n")
        if assumptions:
            md.append("## Assumptions\n\n" + "\n".join(f"- {a}" for a in assumptions) + "\n")
        if security_flags:
            md.append("## Security notes\n\nInstruction-like text (possible prompt injection) was found inside input content. "
                      "It was not followed and the run was halted:\n\n"
                      + "\n".join(f"- `{f['tool']}`: {'; '.join(f['snippets'])}" for f in security_flags) + "\n")
        if checks:
            lines = [f"- **{c['status']}** · {c['kind']} · {c['subject']}" + (f" — {'; '.join(c['problems'])}" if c["problems"] else "") for c in checks]
            md.append("## Verification\n\n" + "\n".join(lines) + "\n")
        md.append(f"**Cost:** Rs {self.cost_inr:.4f} · **Steps:** {self.steps} · **Time:** {elapsed}s\n")
        (self.out_dir / "result.md").write_text("\n".join(md), encoding="utf-8")
