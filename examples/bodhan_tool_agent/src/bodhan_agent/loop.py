"""End-to-end task execution loop orchestrating resolve, pre-flight, plan, act, and report."""

import time
from pathlib import Path
from typing import Any

from bodhan_agent.budget import BudgetManager
from bodhan_agent.executor import StepResult, execute_step
from bodhan_agent.planner import plan as generate_plan
from bodhan_agent.reporter import ExecutionReporter
from bodhan_agent.resolver import resolve
from bodhan_agent.trace import TraceLogger


async def run_task(
    task: str,
    files_dir: Path | str,
    out_dir: Path | str,
    max_steps: int = 30,
    max_cost_inr: float = 20.0,
    llm_client: Any = None,
    tool_client: Any = None,
) -> dict[str, Any]:
    """Execute the end-to-end agent loop adhering to SPEC §10."""
    start_time = time.perf_counter()
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    trace = TraceLogger(out_path / "trace.jsonl")
    reporter = ExecutionReporter(out_path)
    budget = BudgetManager(max_cost_inr=max_cost_inr)

    status = "success"
    assumptions: list[str] = []
    missed_targets: list[str] = []
    executed_steps: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []
    generated_artifacts: list[Path] = []
    final_output_text = ""

    # 1. RESOLVE inputs from directory
    res_inputs = resolve(files_dir, task)
    input_report_entries = [
        {
            "path": f.path,
            "id": f.id,
            "selected": f.id in res_inputs.selected,
            "reason": res_inputs.rationale if f.id in res_inputs.selected else "Not selected",
        }
        for f in res_inputs.inventory
    ]
    trace.record_resolve(
        inventory=[f.model_dump() for f in res_inputs.inventory],
        selected=res_inputs.selected,
        rationale=res_inputs.rationale,
    )

    # Check for missing explicit file requested (Constraint C8: Impossible task -> honest stop)
    if res_inputs.missing_file_requested:
        status = "impossible"
        missed_targets.append(res_inputs.missing_file_requested)
        final_output_text = f"Task cannot be fulfilled: explicitly requested file '{res_inputs.missing_file_requested}' does not exist in inputs directory."
        total_time_s = time.perf_counter() - start_time
        trace.record_final(status=status, total_cost_inr=budget.spent_inr, total_time_s=total_time_s)
        reporter.write_report_json(
            status=status,
            inputs=input_report_entries,
            steps=[],
            verifications=[],
            total_cost_inr=0.0,
            total_time_s=total_time_s,
            assumptions=["Task rejected due to missing explicit input file."],
            missed_targets=missed_targets,
        )
        reporter.write_result_md(
            task=task,
            status=status,
            rationale=res_inputs.rationale,
            final_answer=final_output_text,
            artifacts=[],
            assumptions=["Requested file not found; zero artifacts produced per Constraint C8."],
        )
        return {"status": status, "cost_inr": 0.0, "time_s": total_time_s}

    # Check for unsupported language (e.g. French, German) in task description
    task_words = [w.strip(".,;:?!'\"()[]{}") for w in task.lower().split()]
    if any(w in ("french", "français", "german", "deutsch", "spanish", "chinese", "japanese") for w in task_words):
        status = "impossible"
        missed_targets.append("unsupported_language")
        final_output_text = "Task cannot be fulfilled: Bodhan Indic models support only Indian languages and English (Translate/OCR). Requested non-Indian language is not supported."
        total_time_s = time.perf_counter() - start_time
        trace.record_final(status=status, total_cost_inr=0.0, total_time_s=total_time_s)
        reporter.write_report_json(
            status=status,
            inputs=input_report_entries,
            steps=[],
            verifications=[],
            total_cost_inr=0.0,
            total_time_s=total_time_s,
            assumptions=["Rejected unsupported foreign language request honestly."],
            missed_targets=missed_targets,
        )
        reporter.write_result_md(
            task=task,
            status=status,
            rationale="Rejected unsupported non-Indian language request per SPEC §3 and C8.",
            final_answer=final_output_text,
            artifacts=[],
            assumptions=["Non-Indian language requested; zero artifacts produced."],
        )
        return {"status": status, "cost_inr": 0.0, "time_s": total_time_s}

    selected_files = [f for f in res_inputs.inventory if f.id in res_inputs.selected]
    if not selected_files:
        status = "impossible"
        final_output_text = "No matching input files found in directory to perform task."
        total_time_s = time.perf_counter() - start_time
        trace.record_final(status=status, total_cost_inr=0.0, total_time_s=total_time_s)
        reporter.write_report_json(
            status=status,
            inputs=input_report_entries,
            steps=[],
            verifications=[],
            total_cost_inr=0.0,
            total_time_s=total_time_s,
            assumptions=["No input files matched the task requirements."],
            missed_targets=["no_inputs_matched"],
        )
        reporter.write_result_md(
            task=task,
            status=status,
            rationale=res_inputs.rationale,
            final_answer=final_output_text,
            artifacts=[],
            assumptions=["Zero artifacts produced due to no matching inputs."],
        )
        return {"status": status, "cost_inr": 0.0, "time_s": total_time_s}

    id_to_path = {f.id: f.path for f in res_inputs.inventory}

    # 2. PRE-FLIGHT budget estimation (Golden Rule 6)
    estimated_cost = budget.estimate(selected_files, task)
    can_afford_preflight = budget.can_afford(estimated_cost)
    trace.record_preflight(
        estimated_cost_inr=estimated_cost,
        max_cost_inr=budget.max_cost_inr,
        affordable=can_afford_preflight,
    )

    if not can_afford_preflight:
        status = "budget_exhausted"
        final_output_text = (
            f"Pre-flight budget check refused task: estimated cost ₹{estimated_cost:.2f} "
            f"exceeds ceiling ₹{budget.max_cost_inr:.2f}. Zero tool calls made."
        )
        total_time_s = time.perf_counter() - start_time
        trace.record_final(status=status, total_cost_inr=0.0, total_time_s=total_time_s)
        reporter.write_report_json(
            status=status,
            inputs=input_report_entries,
            steps=[],
            verifications=[],
            total_cost_inr=0.0,
            total_time_s=total_time_s,
            assumptions=["Pre-flight cost estimation exceeded max budget."],
            missed_targets=["budget_insufficient"],
        )
        reporter.write_result_md(
            task=task,
            status=status,
            rationale="Refused execution at pre-flight stage due to budget constraints.",
            final_answer=final_output_text,
            artifacts=[],
            assumptions=["Zero spend incurred per Golden Rule 6."],
        )
        return {"status": status, "cost_inr": 0.0, "time_s": total_time_s}

    # 3. PLAN generation
    plan_obj = await generate_plan(task, res_inputs.inventory, selected_files, llm_client=llm_client)
    if plan_obj.is_fallback:
        assumptions.append("Used deterministic fallback planner for canonical workflow.")
    trace.record_plan(plan=plan_obj.model_dump(), rationale=plan_obj.rationale)

    # 4. STEP LOOP execution
    step_results: dict[str, StepResult] = {}
    step_count = 0

    for step in plan_obj.steps:
        if step_count >= max_steps:
            status = "partial"
            assumptions.append(f"Halted at maximum step limit ({max_steps}).")
            break

        # Check step affordability
        # Estimate single step cost
        step_est = 0.50
        if not budget.can_afford(step_est):
            status = "budget_exhausted"
            assumptions.append(f"Stopped cleanly: remaining budget (₹{budget.remaining_inr:.2f}) cannot afford step '{step.id}'.")
            break

        step_res = await execute_step(
            step=step,
            id_to_path=id_to_path,
            previous_step_results=step_results,
            out_dir=str(artifacts_dir),
            client=tool_client,
        )
        step_results[step.id] = step_res
        budget.charge(step_res.cost_inr)
        step_count += 1

        trace.record_tool_call(
            tool=step_res.tool,
            args=step_res.args,
            result_summary=step_res.result_summary,
            latency_ms=step_res.latency_ms,
            cost_inr=step_res.cost_inr,
            error=step_res.error,
        )

        executed_steps.append({
            "step_id": step.id,
            "tool": step.tool,
            "cost_inr": step_res.cost_inr,
            "latency_ms": step_res.latency_ms,
            "summary": step_res.result_summary,
            "error": step_res.error,
        })

        if step_res.error:
            status = "blocked"
            break

        # Capture artifact paths and final content
        data = step_res.result_data
        raw_text = None
        if hasattr(data, "transcript"):
            raw_text = data.transcript
            final_output_text = data.transcript
            txt_path = artifacts_dir / f"{step.id}_transcript.txt"
            txt_path.write_text(data.transcript, encoding="utf-8")
            generated_artifacts.append(txt_path)
        elif hasattr(data, "translated"):
            raw_text = data.translated
            final_output_text = data.translated
            txt_path = artifacts_dir / f"{step.id}_translated.txt"
            txt_path.write_text(data.translated, encoding="utf-8")
            generated_artifacts.append(txt_path)
        elif hasattr(data, "markdown"):
            raw_text = data.markdown
            final_output_text = data.markdown
            md_path = artifacts_dir / f"{step.id}_extracted.md"
            md_path.write_text(data.markdown, encoding="utf-8")
            generated_artifacts.append(md_path)

        # Untrusted content scan and wrapping (SPEC §8.7)
        if raw_text:
            from bodhan_agent import untrusted
            hits = untrusted.scan(raw_text)
            for hit in hits:
                trace.record_security("prompt_injection_detected", hit.pattern_name, hit.matched_text)

        if hasattr(data, "path") and data.path:
            p = Path(data.path)
            if p.exists():
                generated_artifacts.append(p)
                sidecar = p.with_suffix(".ai.txt")
                if sidecar.exists():
                    generated_artifacts.append(sidecar)

        # 5. Automated Verifications (SPEC §8.6)
        if step.tool == "synthesize_speech" and hasattr(data, "path") and data.path:
            tts_text = step.args.get("text")
            if isinstance(tts_text, dict) and "from_step" in tts_text:
                dep_id = tts_text["from_step"]
                prev_data = step_results.get(dep_id, StepResult(step_id="", tool="", args={})).result_data
                tts_text = getattr(prev_data, "translated", getattr(prev_data, "transcript", getattr(prev_data, "markdown", "")))
            elif isinstance(tts_text, str) and tts_text in step_results:
                prev_data = step_results[tts_text].result_data
                tts_text = getattr(prev_data, "translated", getattr(prev_data, "transcript", getattr(prev_data, "markdown", "")))
            elif (not tts_text or tts_text.startswith("s")) and step.depends_on:
                prev_data = step_results[step.depends_on[0]].result_data
                tts_text = getattr(prev_data, "translated", getattr(prev_data, "transcript", getattr(prev_data, "markdown", "")))
            if tts_text:
                from bodhan_agent.verifier import verify_audio_back_transcription
                ver = await verify_audio_back_transcription(data.path, tts_text, client=tool_client)
                ver_entry = {
                    "check": ver.check_name,
                    "passed": ver.passed,
                    "metric": ver.metric,
                    "details": ver.details,
                }
                verifications.append(ver_entry)
                trace.record_verification(
                    check=ver.check_name,
                    metric=ver.metric,
                    passed=ver.passed,
                    details=ver.details,
                )

        if step.tool == "translate_text" and hasattr(data, "translated"):
            src_text = step.args.get("text")
            if isinstance(src_text, dict) and "from_step" in src_text:
                dep_id = src_text["from_step"]
                prev_data = step_results.get(dep_id, StepResult(step_id="", tool="", args={})).result_data
                src_text = getattr(prev_data, "translated", getattr(prev_data, "transcript", getattr(prev_data, "markdown", "")))
            elif isinstance(src_text, str) and src_text in step_results:
                prev_data = step_results[src_text].result_data
                src_text = getattr(prev_data, "translated", getattr(prev_data, "transcript", getattr(prev_data, "markdown", "")))
            elif (not src_text or src_text.startswith("s")) and step.depends_on:
                prev_data = step_results[step.depends_on[0]].result_data
                src_text = getattr(prev_data, "translated", getattr(prev_data, "transcript", getattr(prev_data, "markdown", "")))
            if src_text:
                from bodhan_agent.verifier import verify_table_parity, verify_translation_coverage
                src_segs = [s.strip() for s in src_text.splitlines() if s.strip()]
                tgt_segs = [s.strip() for s in data.translated.splitlines() if s.strip()]
                ver = verify_translation_coverage(src_segs, tgt_segs)
                ver_entry = {
                    "check": ver.check_name,
                    "passed": ver.passed,
                    "metric": ver.metric,
                    "details": ver.details,
                }
                verifications.append(ver_entry)
                trace.record_verification(
                    check=ver.check_name,
                    metric=ver.metric,
                    passed=ver.passed,
                    details=ver.details,
                )

                if "|" in src_text and "|" in data.translated:
                    tbl_ver = verify_table_parity(src_text, data.translated)
                    tbl_entry = {
                        "check": tbl_ver.check_name,
                        "passed": tbl_ver.passed,
                        "metric": tbl_ver.metric,
                        "details": tbl_ver.details,
                    }
                    verifications.append(tbl_entry)
                    trace.record_verification(
                        check=tbl_ver.check_name,
                        metric=tbl_ver.metric,
                        passed=tbl_ver.passed,
                        details=tbl_ver.details,
                    )

        # SRT generation and verification if requested
        if "srt" in task.lower():
            srt_segments = []
            dur_s = None
            if hasattr(data, "segments") and data.segments:
                srt_segments = [(s.start_s, s.end_s, s.text) for s in data.segments]
                dur_s = getattr(data, "duration_s", None)
            elif step.depends_on:
                prev_data = step_results[step.depends_on[0]].result_data
                if hasattr(prev_data, "segments") and prev_data.segments:
                    dur_s = getattr(prev_data, "duration_s", None)
                    trans_lines = [line.strip() for line in getattr(data, "translated", "").splitlines() if line.strip()]
                    if len(trans_lines) == len(prev_data.segments):
                        srt_segments = [(prev_data.segments[i].start_s, prev_data.segments[i].end_s, trans_lines[i]) for i in range(len(trans_lines))]
                    else:
                        srt_segments = [(prev_data.segments[i].start_s, prev_data.segments[i].end_s, getattr(data, "translated", "")) for i in range(min(1, len(prev_data.segments)))]

            if srt_segments:
                cues = []
                for idx, (st, et, txt) in enumerate(srt_segments, start=1):
                    def _fmt_ts(s: float) -> str:
                        hrs = int(s // 3600)
                        mins = int((s % 3600) // 60)
                        secs = int(s % 60)
                        ms = int(round((s - int(s)) * 1000))
                        return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"
                    cues.append(f"{idx}\n{_fmt_ts(st)} --> {_fmt_ts(et)}\n{txt}\n")
                srt_content = "\n".join(cues)
                srt_file = artifacts_dir / f"{step.id}_subtitles.srt"
                srt_file.write_text(srt_content, encoding="utf-8")
                generated_artifacts.append(srt_file)

                from bodhan_agent.verifier import verify_srt_structure
                srt_ver = verify_srt_structure(srt_content, dur_s)
                srt_entry = {
                    "check": srt_ver.check_name,
                    "passed": srt_ver.passed,
                    "metric": srt_ver.metric,
                    "details": srt_ver.details,
                }
                verifications.append(srt_entry)
                trace.record_verification(
                    check=srt_ver.check_name,
                    metric=srt_ver.metric,
                    passed=srt_ver.passed,
                    details=srt_ver.details,
                )

    total_time_s = time.perf_counter() - start_time
    trace.record_final(status=status, total_cost_inr=budget.spent_inr, total_time_s=total_time_s)

    reporter.write_report_json(
        status=status,
        inputs=input_report_entries,
        steps=executed_steps,
        verifications=verifications,
        total_cost_inr=budget.spent_inr,
        total_time_s=total_time_s,
        assumptions=assumptions,
        missed_targets=missed_targets,
    )
    reporter.write_result_md(
        task=task,
        status=status,
        rationale=plan_obj.rationale,
        final_answer=final_output_text,
        artifacts=generated_artifacts,
        assumptions=assumptions,
    )

    return {
        "status": status,
        "cost_inr": budget.spent_inr,
        "time_s": total_time_s,
        "artifacts": [str(a) for a in generated_artifacts],
    }
