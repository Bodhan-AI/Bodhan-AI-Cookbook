"""Audit report generation for Bodhan Task Agent.

Generates:
1. out/report.json (machine-readable metrics and verification)
2. out/result.md (human-readable structured Markdown with relative links)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from bodhan_mcp.common import get_ist_now_iso


def unwrap_finish_explanation(explanation: str) -> str:
    """If explanation is a JSON object with 'explanation' and 'status' keys, return only the explanation text."""
    if not isinstance(explanation, str):
        return str(explanation or "")
    stripped = explanation.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            if (
                isinstance(parsed, dict)
                and "explanation" in parsed
                and "status" in parsed
                and isinstance(parsed["explanation"], str)
            ):
                return parsed["explanation"].strip()
        except Exception:
            pass
    return explanation


def write_agent_reports(
    out_dir: Path,
    task: str,
    status: str,
    steps_executed: int,
    total_cost_inr: float,
    total_time_seconds: float,
    verification_results: List[Dict[str, Any]],
    quality_target_misses: List[str],
    artifacts: List[str],
    assumptions: List[str],
    step_summaries: List[Dict[str, Any]],
    explanation: str = "",
    http_requests_count: int = 0,
    llm_usage: Optional[Dict[str, Any]] = None,
    llm_ms: float = 0.0,
    tool_ms: float = 0.0,
    deliverables: Optional[Dict[str, str]] = None,
    rate_limit_wait_ms: float = 0.0,
    llm_model: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Tuple[Path, Path]:
    """Generates out/report.json and out/result.md using relative paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = out_dir / "report.json"
    result_md_path = out_dir / "result.md"

    # Relative artifact paths: list only files written to out/artifacts, never input files
    rel_artifacts = []
    artifacts_base = (out_dir / "artifacts").resolve()
    for art in artifacts:
        try:
            art_p = Path(art).resolve()
            if art_p.is_relative_to(artifacts_base):
                rel = art_p.relative_to(out_dir.resolve()).as_posix()
                if rel not in rel_artifacts:
                    rel_artifacts.append(rel)
        except Exception:
            pass

    usage_dict = llm_usage or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_calls_count": 0,
    }

    total_ms = round(total_time_seconds * 1000.0, 2)
    overhead_ms = max(0.0, round(total_ms - llm_ms - tool_ms - rate_limit_wait_ms, 2))
    clean_explanation = unwrap_finish_explanation(explanation)
    resolved_llm_model = (
        llm_model.strip()
        if isinstance(llm_model, str) and llm_model.strip()
        else (os.getenv("LLM_MODEL") or "").strip()
    )

    # 1. report.json
    report_data = {
        "status": status,
        "task": task,
        "timestamp": get_ist_now_iso(),
        "run_id": run_id or "",
        "llm_model": resolved_llm_model,
        "steps_executed": steps_executed,
        "http_requests_count": http_requests_count,
        "total_cost_inr": round(total_cost_inr, 6),
        "bodhan_cost_inr": round(total_cost_inr, 6),
        "llm_usage": usage_dict,
        "total_time_seconds": round(total_time_seconds, 3),
        "llm_ms": round(llm_ms, 2),
        "tool_ms": round(tool_ms, 2),
        "overhead_ms": overhead_ms,
        "rate_limit_wait_ms": round(rate_limit_wait_ms, 2),
        "time_breakdown_ms": {
            "llm_ms": round(llm_ms, 2),
            "tool_ms": round(tool_ms, 2),
            "overhead_ms": overhead_ms,
            "rate_limit_wait_ms": round(rate_limit_wait_ms, 2),
            "total_ms": total_ms,
        },
        "step_summaries": step_summaries,
        "verification_results": verification_results,
        "quality_target_misses": quality_target_misses,
        "artifacts": rel_artifacts,
        "deliverables": deliverables or {},
        "explanation": clean_explanation,
    }
    report_json_path.write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    duration_timing = (
        f"LLM: {llm_ms:.1f}ms, Tools: {tool_ms:.1f}ms, "
        f"Rate-Limit Wait: {rate_limit_wait_ms:.1f}ms, Overhead: {overhead_ms:.1f}ms"
    )

    # 2. result.md
    md_lines = [
        "# Bodhan Task Agent Execution Result",
        "",
        "## Summary",
        f"- **Status**: `{status.upper()}`",
        f"- **Task**: {task}",
        f"- **Total Steps**: {steps_executed}",
        f"- **HTTP Requests Made**: {http_requests_count} *(verified from spend ledger)*",
        f"- **Bodhan Model Cost**: Rs {total_cost_inr:.4f}",
        f"- **LLM Usage**: {usage_dict.get('llm_calls_count', 0)} calls, {usage_dict.get('total_tokens', 0)} tokens ({usage_dict.get('prompt_tokens', 0)} prompt, {usage_dict.get('completion_tokens', 0)} completion)",
        f"- **Total Duration**: {total_time_seconds:.2f}s ({duration_timing})",
        "",
    ]

    if clean_explanation:
        md_lines.extend(
            [
                "## Outcome Explanation",
                clean_explanation,
                "",
            ]
        )

    if deliverables:
        if status == "completed":
            md_lines.append("## Deliverables")
        else:
            md_lines.append("## Deliverables (Partial)")
            md_lines.append("*(Partial deliverables generated prior to execution interruption)*")
            md_lines.append("")
        seen_deliverables: Set[str] = set()
        for d_title, d_content in deliverables.items():
            clean_c = d_content.strip()
            if clean_c in seen_deliverables:
                continue
            seen_deliverables.add(clean_c)
            md_lines.append(f"### {d_title}")
            md_lines.append(d_content)
            md_lines.append("")

    if assumptions:
        md_lines.append("## Assumptions")
        for a in assumptions:
            md_lines.append(f"- {a}")
        md_lines.append("")

    if rel_artifacts:
        if status == "completed":
            md_lines.append("## Artifacts Generated")
        else:
            md_lines.append("## Artifacts Generated (Partial)")
            md_lines.append("*(Partial artifacts generated prior to execution interruption)*")
            md_lines.append("")
        for a in rel_artifacts:
            md_lines.append(f"- [{a}]({a})")
        md_lines.append("")

    if verification_results:
        md_lines.append("## Verification Results")
        for v in verification_results:
            verdict = v.get("verdict", "pass" if v.get("passed", True) else "fail")
            if verdict == "pass":
                symbol = "✓"
            elif verdict == "warn":
                symbol = "⚠️"
            elif verdict == "completed_unverified":
                symbol = "❓"
            else:
                symbol = "✗"
            what = v.get("what", v.get("check", "Verification"))
            details = v.get("details", "")
            score_str = f" (Score: {v.get('score')})" if v.get("score") is not None else ""
            verdict_label = (
                f"{verdict.upper()} (SUPERSEDED)" if v.get("superseded") else verdict.upper()
            )
            md_lines.append(f"- {symbol} **{what}** [{verdict_label}]{score_str}: {details}")
        md_lines.append("")

    if quality_target_misses:
        md_lines.append("## Quality Target Misses")
        for q in quality_target_misses:
            md_lines.append(f"- ⚠️ {q}")
        md_lines.append("")

    if step_summaries:
        md_lines.append("## Step-by-Step Execution Log")
        for s in step_summaries:
            s_num = s.get("step", 0)
            s_name = s.get("name", "")
            s_summary = s.get("summary", "")
            s_cost = s.get("cost_inr", 0.0)
            md_lines.append(f"### Step {s_num}: `{s_name}` (Cost: Rs {s_cost:.4f})")
            md_lines.append(s_summary)
            md_lines.append("")

    result_md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return report_json_path, result_md_path
