"""
Main agent loop: plan → execute → verify → report.

The agent runs entirely non-interactively. When it encounters an impossible
task or exhausts its budget it stops cleanly and writes an honest report.
"""
from __future__ import annotations

import time
from pathlib import Path

from bodhan_mcp.tools.media_info import get_media_info

from .budget import BudgetExceeded, BudgetManager
from .cache import ResultCache
from .executor import _resolve_refs, execute_step
from .llm_client import get_llm_client, get_llm_model
from .planner import plan_task
from .trace import StepRecord, TraceWriter
from .verifier import verify_audio, verify_translation


async def run(
    task: str,
    files: list[Path],
    out_dir: Path,
    max_steps: int,
    max_cost_inr: float,
) -> None:
    """Execute the task end-to-end, write all outputs to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    budget = BudgetManager(max_steps=max_steps, max_cost_inr=max_cost_inr)
    cache = ResultCache(out_dir / "cache.db")
    tracer = TraceWriter(out_dir)
    llm_client = get_llm_client()
    llm_model = get_llm_model()

    verification_results: list[dict] = []
    status = "failed"
    result_md_lines: list[str] = []

    try:
        # ── 1. Inspect files ─────────────────────────────────────────────────
        for f in files:
            get_media_info(f)

        # ── 2. Plan ──────────────────────────────────────────────────────────
        plan = await plan_task(task, files, llm_client, llm_model, max_cost_inr)

        if plan["impossible"]:
            reason = (
                plan["impossible_reason"]
                or "Task cannot be completed with the available tools."
            )
            status = "impossible"
            result_md_lines.append(f"# Task Report\n\n**Task:** {task}\n")
            result_md_lines.append(
                f"## Result\n\n❌ **This task cannot be completed.**\n\n{reason}"
            )
            if plan["assumptions"]:
                notes = "\n".join(f"- {a}" for a in plan["assumptions"])
                result_md_lines.append(f"## Notes\n{notes}")
            return

        result_md_lines.append(f"# Task Report\n\n**Task:** {task}\n")
        if plan["assumptions"]:
            assumptions = "\n".join(f"- {a}" for a in plan["assumptions"])
            result_md_lines.append(f"## Assumptions\n{assumptions}")

        # ── 3. Execute steps ─────────────────────────────────────────────────
        step_results: dict[str, dict] = {}
        step_num = 0

        for step in plan["steps"]:
            step_id = step["id"]
            tool = step["tool"]
            estimated_cost = float(step.get("estimated_cost_inr", 0.0))

            try:
                budget.assert_can_proceed(estimated_cost)
            except BudgetExceeded as exc:
                result_md_lines.append(f"\n⚠️ **Stopped early:** {exc}")
                status = "partial"
                break

            # Cache check (key on tool + resolved args text)
            cache_key = {"step": step_id, "tool": tool, "args": str(step.get("args", {}))}
            cached_result = cache.get(tool, cache_key)
            cached = cached_result is not None

            t0 = time.monotonic()
            if cached:
                result = cached_result
                actual_cost = 0.0
            else:
                result, actual_cost = await execute_step(step, files, step_results, artifacts_dir)
                cache.set(tool, cache_key, result)

            latency = time.monotonic() - t0
            budget.record_step(actual_cost)
            step_num += 1
            step_results[step_id] = result

            # ── Verify outputs that need checking ────────────────────────────
            verified: bool | None = None
            if tool == "speak" and result.get("audio_path"):
                try:
                    spoken_text = str(
                        _resolve_refs(step["args"].get("text", ""), files, step_results)
                    )
                    v = await verify_audio(
                        Path(result["audio_path"]),
                        spoken_text,
                        step["args"].get("language", ""),
                    )
                    verification_results.append({"step": step_id, "type": "audio", **v})
                    verified = v["ok"]
                    budget.record_step(v["cost_inr"])
                except Exception as v_err:
                    verification_results.append({
                        "step": step_id,
                        "type": "audio",
                        "ok": False,
                        "error": str(v_err),
                        "cost_inr": 0.0,
                    })
                    verified = False

            elif tool == "translate" and result.get("translated_text"):
                try:
                    resolved_src = str(
                        _resolve_refs(step["args"].get("text", ""), files, step_results)
                    )
                    v = await verify_translation(
                        source_text=resolved_src,
                        translated_text=result["translated_text"],
                        source_lang=step["args"].get("source_lang", "en"),
                        target_lang=step["args"].get("target_lang", ""),
                        llm_client=llm_client,
                        llm_model=llm_model,
                    )
                    verification_results.append({"step": step_id, "type": "translation", **v})
                    verified = v["ok"]
                    budget.record_step(v["cost_inr"])
                except Exception as v_err:
                    verification_results.append({
                        "step": step_id,
                        "type": "translation",
                        "ok": False,
                        "error": str(v_err),
                        "cost_inr": 0.0,
                    })
                    verified = False

            tracer.record(
                StepRecord(
                    step=step_num,
                    tool=tool,
                    args_summary=_summarise(step.get("args", {})),
                    result_summary=_summarise(result),
                    latency_s=round(latency, 2),
                    cost_inr=round(actual_cost, 4),
                    cached=cached,
                    verified=verified,
                )
            )

        status = "completed"
        result_md_lines.append("\n## Outputs\n")
        for f in sorted(artifacts_dir.iterdir()):
            result_md_lines.append(f"- [{f.name}](artifacts/{f.name})")

    except Exception as exc:
        result_md_lines.append(f"\n❌ **Error:** {exc}")
        status = "failed"

    finally:
        (out_dir / "result.md").write_text("\n\n".join(result_md_lines), encoding="utf-8")
        tracer.write_report(
            status=status,
            task=task,
            budget_summary=budget.summary(),
            verification_results=verification_results,
        )
        cache.close()


def _summarise(d: dict) -> str:
    """Short human-readable summary of a dict (truncated to 120 chars)."""
    s = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(d.items())[:4])
    return s[:120]


def _write_result_md(
    out_dir: Path,
    task: str,
    impossible_reason: str,
    assumptions: list[str],
) -> None:
    lines = [
        f"# Task Report\n\n**Task:** {task}\n",
        f"## Result\n\n❌ **This task cannot be completed.**\n\n{impossible_reason}",
    ]
    if assumptions:
        lines.append("## Notes\n" + "\n".join(f"- {a}" for a in assumptions))
    (out_dir / "result.md").write_text("\n\n".join(lines), encoding="utf-8")
