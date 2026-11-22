"""Autonomous task agent loop: plan, act, verify, retry, stop.

Imports tools directly from bodhan_mcp and executes multi-step Indic language tasks.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bodhan_agent.llm import (
    LLMClient,
    LLMEmptyResponseError,
    LLMError,
    LLMInvalidJSONError,
    LLMTimeoutError,
    SYSTEM_PROMPT,
)
from bodhan_agent.models import AgentReport, StepTrace, VerificationResult
from bodhan_agent.tracer import AgentTracer
from bodhan_mcp.cache import DiskCache
from bodhan_mcp.config import Settings, load_settings
from bodhan_mcp.ffmpeg_utils import get_audio_duration
from bodhan_mcp.lang_utils import (
    EXPECTED_SCRIPT_FOR_LANG,
    SCRIPT_NAMES,
    SUPPORTED_INDIC_LANGUAGES,
    detect_script_language,
    extract_language_from_text,
    extract_task_languages,
    is_language_supported,
)
from bodhan_mcp.pricing import (
    BudgetExceededError,
    BudgetManager,
    calculate_ocr_cost,
    calculate_speak_cost,
    calculate_transcribe_cost,
    calculate_translate_cost,
)
from bodhan_mcp.tools import BodhanTools

UNSUPPORTED_LANGUAGE_PATTERNS = [
    r"\bfrench\b", r"\bgerman\b", r"\bspanish\b", r"\bjapanese\b",
    r"\bchinese\b", r"\brussian\b", r"\barabic\b", r"\bportuguese\b",
    r"\bitalian\b", r"\bkorean\b",
]


def clean_ocr_text(text: str) -> str:
    """Strips OCR page markers like '--- Page 1 ---' from text."""
    if not text:
        return ""
    cleaned = re.sub(r"(?m)^\s*---\s*Page\s+\d+\s*---\s*$", "", text)
    cleaned = re.sub(r"---\s*Page\s+\d+\s*---", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


PLACEHOLDER_REGEX = re.compile(
    r"<[^>]+>|\{\{[^}]+\}\}|output_from_\w+|\b(?:TODO|PLACEHOLDER)\b",
    re.IGNORECASE,
)


class TaskAgent:
    """Autonomous agent executing complex multi-step language tasks using Bodhan models."""

    def __init__(
        self,
        task: str,
        files: list[str | Path],
        out_dir: str | Path,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        settings: Settings | None = None,
        llm_client: LLMClient | None = None,
        tools: BodhanTools | None = None,
    ):
        self.task = task.strip()
        self.raw_files = [Path(f) for f in files]
        self.out_dir = Path(out_dir)
        self.max_steps = max_steps
        self.max_cost_inr = max_cost_inr

        self.settings = settings or load_settings()
        self.tracer = AgentTracer(self.out_dir)
        self.budget = BudgetManager(max_cost_inr=self.max_cost_inr)
        self.cache = DiskCache()

        self.tools = tools or BodhanTools(
            settings=self.settings,
            cache=self.cache,
            budget=self.budget,
        )

        self.llm = llm_client or LLMClient(
            base_url=self.settings.llm_base_url,
            model=self.settings.llm_model,
            api_key=self.settings.llm_api_key,
            timeout=self.settings.llm_timeout_s,
            max_retries=self.settings.llm_max_retries,
        )

        self.assumptions: list[str] = []
        self.artifacts: list[str] = []
        self.failures: list[str] = []
        self.verification_results: list[VerificationResult] = []
        self.source_texts: dict[str, str] = {}
        self.executed_calls: dict[str, dict[str, Any]] = {}
        self.step_outputs: list[dict[str, Any]] = []
        self.last_produced_text: str | None = None
        self.last_artifact_path: str | None = None
        self.last_artifact_stem: str | None = None
        self.upstream_sources: dict[str, dict[str, Any]] = {}
        self.audio_language: str | None = None
        self.target_language: str | None = None
        self.language_requested: str | None = None
        self.detected_script: str | None = None
        self.windows_count: int | None = None
        self.audio_durations: dict[str, float] = {}
        self.last_transcription_result: dict[str, Any] = {}
        self.step_records: list[dict[str, Any]] = []

    def run(self) -> AgentReport:
        """Executes the full agent loop and writes all required outputs."""
        start_time = time.perf_counter()
        # Reset trace, artifacts directory, and all in-memory state so every run starts completely fresh
        self.tracer.reset()
        self.step_records.clear()
        self.artifacts.clear()
        self.step_outputs.clear()
        self.upstream_sources.clear()
        self.failures.clear()
        self.assumptions.clear()
        self.executed_calls.clear()
        self.verification_results.clear()
        self.source_texts.clear()
        self.audio_durations.clear()
        self.last_produced_text = None
        self.last_artifact_path = None
        self.last_artifact_stem = None

        # --------------------------------------------------------------------
        # 1. Plain Code Pre-flight Checks (Zero LLM / Zero Paid Calls)
        # --------------------------------------------------------------------
        # Plain code language extraction from task
        lang_info = extract_task_languages(self.task)
        self.audio_language = lang_info["audio_language"]
        self.target_language = lang_info["target_language"]
        self.language_requested = self.audio_language or self.target_language

        if lang_info["is_ambiguous"]:
            self.audio_language = None
            self.target_language = None
            self.language_requested = None
            self.assumptions.append(
                f"Language assignment in task is ambiguous ({lang_info['ambiguity_reason']}); "
                "sending no language parameter (relying on model auto-detection)."
            )
        else:
            if self.audio_language and self.target_language and self.audio_language == self.target_language:
                self.assumptions.append(
                    f"Audio language and target language are both {SUPPORTED_INDIC_LANGUAGES[self.audio_language]} ({self.audio_language}); "
                    "skipping translation as source and target languages are identical."
                )
                self.target_language = None
                self.language_requested = self.audio_language
            else:
                if self.audio_language:
                    self.assumptions.append(
                        f"Task specified audio language: {SUPPORTED_INDIC_LANGUAGES[self.audio_language]} ({self.audio_language})."
                    )
                if self.target_language:
                    self.assumptions.append(
                        f"Task specified target language: {SUPPORTED_INDIC_LANGUAGES[self.target_language]} ({self.target_language})."
                    )
            if not self.audio_language and not self.target_language:
                self.assumptions.append(
                    "No specific spoken language was requested in the task; submitted without language parameter (model auto-detection)."
                )

        # A. Check for impossible unsupported languages in prompt
        for pat in UNSUPPORTED_LANGUAGE_PATTERNS:
            m = re.search(pat, self.task, re.IGNORECASE)
            if m:
                unsupported_name = m.group(0).capitalize()
                final_answer = (
                    f"The task requested translation/processing in {unsupported_name}. "
                    f"Bodhan Indic models exclusively support the 22 official scheduled Indian languages and English. "
                    f"{unsupported_name} is not supported. The task cannot be completed, and no fake output is generated."
                )
                report = AgentReport(
                    status="impossible_task",
                    task=self.task,
                    steps_taken=0,
                    total_cost_inr=0.0,
                    total_time_s=time.perf_counter() - start_time,
                    assumptions=["Verified that requested target language is unsupported by Bodhan Indic models."],
                    failures=[f"Requested unsupported language: {unsupported_name}"],
                )
                self.tracer.write_report(report)
                self.tracer.write_result_markdown(
                    self.task, report.status, final_answer, report.assumptions, [], report.failures
                )
                return report

        # B. Verify files exist, inspect them, and load real source text in plain code
        valid_files: list[Path] = []
        file_summaries: list[dict[str, Any]] = []

        for f_path in self.raw_files:
            if not f_path.exists():
                final_answer = f"Required input file was not found on disk: {f_path}. Halting without fabrication."
                report = AgentReport(
                    status="failed",
                    task=self.task,
                    steps_taken=0,
                    total_cost_inr=0.0,
                    total_time_s=time.perf_counter() - start_time,
                    failures=[f"File not found: {f_path}"],
                )
                self.tracer.write_report(report)
                self.tracer.write_result_markdown(
                    self.task, report.status, final_answer, [], [], report.failures
                )
                return report

            valid_files.append(f_path)
            insp = self.tools.inspect_media(str(f_path))
            file_summaries.append(insp)

            # Record factual plain-code file assumptions and duration tracking
            is_vid = insp.get("media_type") == "video" or f_path.suffix.lower() in (
                ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"
            )
            is_aud = insp.get("media_type") == "audio" or f_path.suffix.lower() in (
                ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"
            )
            if is_vid or is_aud:
                dur = insp.get("duration_s", 0.0)
                self.audio_durations[str(f_path.resolve())] = dur
                self.audio_durations[f_path.name] = dur
                if is_vid:
                    self.assumptions.append(
                        f"Video input '{f_path.name}' ({dur:.1f}s) prepared for audio extraction and transcription."
                    )
                else:
                    self.assumptions.append(
                        f"Audio input '{f_path.name}' ({dur:.1f}s) prepared for 16 kHz mono WAV conversion and <=30s window segmentation."
                    )
            elif f_path.suffix.lower() in (".txt", ".md", ".json"):
                chars = insp.get("char_count", 0)
                self.assumptions.append(f"Text file '{f_path.name}' ({chars} chars) loaded as source text in plain code.")
            elif f_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf"):
                self.assumptions.append(f"Document file '{f_path.name}' targeted for Indic-OCR extraction.")

            # Store full source text in plain code for text/markdown files
            if f_path.suffix.lower() in (".txt", ".md", ".json"):
                try:
                    raw_content = f_path.read_text(encoding="utf-8", errors="replace")
                    self.source_texts[str(f_path.resolve())] = raw_content
                    self.source_texts[f_path.name] = raw_content
                except Exception:
                    pass

        # C. Budget check (money only)
        if self.max_cost_inr <= 0.0:
            final_answer = "Maximum cost budget is set to 0.00 INR. Cannot perform operations."
            report = AgentReport(
                status="budget_exceeded",
                task=self.task,
                steps_taken=0,
                total_cost_inr=0.0,
                total_time_s=time.perf_counter() - start_time,
                failures=["Initial budget was 0 INR."],
            )
            self.tracer.write_report(report)
            self.tracer.write_result_markdown(self.task, report.status, final_answer, [], [], report.failures)
            return report

        # --------------------------------------------------------------------
        # 2. One-Shot Planning (Fast Single LLM Call with Timeout)
        # --------------------------------------------------------------------
        step = 0
        final_answer = ""
        agent_status = "success"

        step += 1
        try:
            plan_dict, plan_llm_latency_ms = self.llm.generate_plan(self.task, file_summaries)
        except LLMTimeoutError as te:
            agent_status = "timeout"
            final_answer = f"Task timed out during LLM planning: {te}"
            self.failures.append(str(te))
            plan_trace = StepTrace(
                step=step,
                timestamp=datetime.now(timezone.utc).isoformat(),
                plan="Generate structured task plan",
                tool="generate_plan",
                arguments={"task": self.task, "files": [f.name for f in valid_files]},
                result_summary=f"Timed out: {te}",
                latency_ms=int(self.settings.llm_timeout_s * 1000),
                llm_latency_ms=int(self.settings.llm_timeout_s * 1000),
                tool_latency_ms=0,
                cost_inr=0.0,
            )
            self.tracer.record_step(plan_trace)
            report = AgentReport(
                status="timeout",
                task=self.task,
                steps_taken=step,
                total_cost_inr=self.budget.spent_inr,
                total_time_s=time.perf_counter() - start_time,
                assumptions=self.assumptions,
                verification_results=self.verification_results,
                artifacts=self.artifacts,
                failures=self.failures,
            )
            self.tracer.write_report(report)
            self.tracer.write_result_markdown(
                self.task, report.status, final_answer, report.assumptions, report.artifacts, report.failures
            )
            return report

        except (LLMInvalidJSONError, LLMEmptyResponseError, LLMError, Exception) as le:
            is_timeout = isinstance(le, LLMTimeoutError) or "timeout" in str(le).lower()
            agent_status = "timeout" if is_timeout else "failed"
            final_answer = f"LLM planning failed: {le}"
            self.failures.append(str(le))
            plan_trace = StepTrace(
                step=step,
                timestamp=datetime.now(timezone.utc).isoformat(),
                plan="Generate structured task plan",
                tool="generate_plan",
                arguments={"task": self.task, "files": [f.name for f in valid_files]},
                result_summary=f"{agent_status.capitalize()}: {le}",
                latency_ms=0,
                llm_latency_ms=0,
                tool_latency_ms=0,
                cost_inr=0.0,
            )
            self.tracer.record_step(plan_trace)
            report = AgentReport(
                status=agent_status,
                task=self.task,
                steps_taken=step,
                total_cost_inr=self.budget.spent_inr,
                total_time_s=time.perf_counter() - start_time,
                assumptions=self.assumptions,
                verification_results=self.verification_results,
                artifacts=self.artifacts,
                failures=self.failures,
            )
            self.tracer.write_report(report)
            self.tracer.write_result_markdown(
                self.task, report.status, final_answer, report.assumptions, report.artifacts, report.failures
            )
            return report

        planned_steps = plan_dict.get("steps", [])

        # Record planning step in trace.jsonl (factual summary only)
        plan_trace = StepTrace(
            step=step,
            timestamp=datetime.now(timezone.utc).isoformat(),
            plan="Generate structured task plan",
            tool="generate_plan",
            arguments={"task": self.task, "files": [f.name for f in valid_files]},
            result_summary=f"Generated plan with {len(planned_steps)} step(s).",
            latency_ms=plan_llm_latency_ms,
            llm_latency_ms=plan_llm_latency_ms,
            tool_latency_ms=0,
            cost_inr=0.0,
        )
        self.tracer.record_step(plan_trace)
        self.step_records.append({
            "step": step,
            "tool": "generate_plan",
            "cached": False,
            "would_be_cost_inr": 0.0,
            "cost_charged_inr": 0.0,
            "arguments": {"task": self.task, "files": [f.name for f in valid_files]},
            "result_summary": f"Generated plan with {len(planned_steps)} step(s).",
        })

        # --------------------------------------------------------------------
        # 3. Plan Execution & Loop Control
        # --------------------------------------------------------------------
        # Automatic video audio extraction insertion (Requirement 3)
        video_files = [
            f for f in valid_files
            if f.suffix.lower() in (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v")
        ]
        if video_files and planned_steps:
            has_extract = any(s.get("tool") == "extract_audio_from_video" for s in planned_steps)
            if not has_extract:
                trans_idx = next(
                    (i for i, s in enumerate(planned_steps) if s.get("tool") == "transcribe_audio"), None
                )
                if trans_idx is not None:
                    v_file = video_files[0]
                    planned_steps.insert(
                        trans_idx,
                        {
                            "tool": "extract_audio_from_video",
                            "args": {"video_path": str(v_file.resolve())},
                        },
                    )
                    self.assumptions.append(
                        f"Inserted extract_audio_from_video step for video input '{v_file.name}' before transcription."
                    )

        # If the LLM returned a valid list of steps, execute them directly in plain code
        if planned_steps:
            for item in planned_steps:
                if step >= self.max_steps:
                    agent_status = "max_steps_reached"
                    self.failures.append(f"Reached step limit ({self.max_steps}).")
                    break

                tool_name = item.get("tool", "")
                args = item.get("args", {})

                # Ensure file_path is resolved from input files if LLM missed it
                self._bind_default_file_args(tool_name, args, valid_files)

                # Path resolution in plain code: resolve bare filenames or relative paths (Requirement 2)
                resolved_file_ok, file_err = self._resolve_file_arguments(tool_name, args, valid_files)
                if not resolved_file_ok:
                    step += 1
                    rej_trace = StepTrace(
                        step=step,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        plan=f"Reject step {tool_name} with unknown file argument",
                        tool=tool_name,
                        arguments=args,
                        result_summary=f"Failed: {file_err}",
                        latency_ms=0,
                        llm_latency_ms=0,
                        tool_latency_ms=0,
                        cost_inr=0.0,
                    )
                    self.tracer.record_step(rej_trace)
                    self.failures.append(f"Step {step} ({tool_name}) failed: {file_err}")
                    continue

                # Redundant translation skip rule (Requirement 4)
                if tool_name == "translate_text":
                    target_code = args.get("target_language_code") or self.target_language
                    if target_code and self.audio_language and target_code == self.audio_language:
                        step += 1
                        skip_msg = (
                            f"Skipped redundant translate_text step: task language "
                            f"({target_code}) matches audio language ({self.audio_language})."
                        )
                        if skip_msg not in self.assumptions:
                            self.assumptions.append(skip_msg)
                        rej_trace = StepTrace(
                            step=step,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            plan=f"Skip redundant translation to {target_code}",
                            tool=tool_name,
                            arguments=args,
                            result_summary=skip_msg,
                            latency_ms=0,
                            llm_latency_ms=0,
                            tool_latency_ms=0,
                            cost_inr=0.0,
                        )
                        self.tracer.record_step(rej_trace)
                        self.step_records.append({
                            "step": step,
                            "tool": tool_name,
                            "cached": False,
                            "would_be_cost_inr": 0.0,
                            "cost_charged_inr": 0.0,
                            "arguments": args,
                            "result_summary": skip_msg,
                        })
                        continue

                # Repeated-call guard: reject identical calls before reference mutation
                raw_call_sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
                if raw_call_sig in self.executed_calls:
                    step += 1
                    rej_trace = StepTrace(
                        step=step,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        plan=f"Guard rejected repeated call to {tool_name}",
                        tool=tool_name,
                        arguments=args,
                        result_summary="Rejected: Identical tool call was already completed. Skipping duplicate execution.",
                        latency_ms=0,
                        llm_latency_ms=0,
                        tool_latency_ms=0,
                        cost_inr=0.0,
                    )
                    self.tracer.record_step(rej_trace)
                    continue

                # Resolve step references and check for unresolved placeholders in plain code
                resolved_ok, err_msg = self._resolve_step_references(tool_name, args, valid_files)
                if not resolved_ok:
                    step += 1
                    rej_trace = StepTrace(
                        step=step,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        plan=f"Reject step {tool_name} with unresolved placeholder",
                        tool=tool_name,
                        arguments=args,
                        result_summary=f"Failed: {err_msg}",
                        latency_ms=0,
                        llm_latency_ms=0,
                        tool_latency_ms=0,
                        cost_inr=0.0,
                    )
                    self.tracer.record_step(rej_trace)
                    self.failures.append(f"Step {step} ({tool_name}) failed: {err_msg}")
                    continue

                # Repeated-call guard: reject identical calls after reference resolution
                call_sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
                if call_sig in self.executed_calls:
                    step += 1
                    rej_trace = StepTrace(
                        step=step,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        plan=f"Guard rejected repeated call to {tool_name}",
                        tool=tool_name,
                        arguments=args,
                        result_summary="Rejected: Identical tool call was already completed. Skipping duplicate execution.",
                        latency_ms=0,
                        llm_latency_ms=0,
                        tool_latency_ms=0,
                        cost_inr=0.0,
                    )
                    self.tracer.record_step(rej_trace)
                    continue

                # Pre-step budget estimate and enforcement (Requirement 5)
                est_cost, est_desc = self._estimate_step_cost(tool_name, args)
                is_step_cached = self._is_step_cached(tool_name, args)

                if not is_step_cached and est_cost > self.budget.remaining_inr:
                    step += 1
                    agent_status = "budget_exceeded"
                    fail_msg = (
                        f"Estimated cost of {tool_name} for {est_desc} (₹{est_cost:.4f}) exceeds "
                        f"remaining budget (₹{self.budget.remaining_inr:.4f}). "
                        f"Task stopped before making paid API calls."
                    )
                    self.failures.append(fail_msg)
                    self.step_records.append({
                        "step": step,
                        "tool": tool_name,
                        "cached": False,
                        "would_be_cost_inr": round(est_cost, 4),
                        "cost_charged_inr": 0.0,
                        "arguments": args,
                        "result_summary": f"Halted: {fail_msg}",
                    })
                    rej_trace = StepTrace(
                        step=step,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        plan=f"Pre-check budget for {tool_name}",
                        tool=tool_name,
                        arguments=args,
                        result_summary=f"Halted: {fail_msg}",
                        latency_ms=0,
                        llm_latency_ms=0,
                        tool_latency_ms=0,
                        cost_inr=0.0,
                        cached=False,
                        would_be_cost_inr=round(est_cost, 4),
                        cost_charged_inr=0.0,
                    )
                    self.tracer.record_step(rej_trace)
                    break

                step += 1
                t_tool_start = time.perf_counter()
                cost_before = self.budget.spent_inr
                tool_res = self._execute_and_save_tool(tool_name, args)
                tool_latency_ms = int((time.perf_counter() - t_tool_start) * 1000)
                step_cost = self.budget.spent_inr - cost_before

                self.executed_calls[raw_call_sig] = tool_res
                self.executed_calls[call_sig] = tool_res

                # Check if tool halted due to budget during execution
                if tool_res.get("budget_exceeded") or (
                    not tool_res.get("success", False) and "budget" in str(tool_res.get("error", "")).lower()
                ):
                    agent_status = "budget_exceeded"
                    fail_msg = f"Step {step} ({tool_name}) exceeded budget: {tool_res.get('error')}"
                    self.failures.append(fail_msg)
                    would_be_val = tool_res.get("would_be_cost_inr", est_cost)
                    self.step_records.append({
                        "step": step,
                        "tool": tool_name,
                        "cached": False,
                        "would_be_cost_inr": round(would_be_val, 4),
                        "cost_charged_inr": round(step_cost, 4),
                        "arguments": args,
                        "result_summary": f"Failed: {tool_res.get('error')}",
                    })
                    trace = StepTrace(
                        step=step,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        plan=f"Execute {tool_name}",
                        tool=tool_name,
                        arguments=args,
                        result_summary=self._summarize_result(tool_res),
                        latency_ms=tool_latency_ms,
                        llm_latency_ms=0,
                        tool_latency_ms=tool_latency_ms,
                        cost_inr=step_cost,
                        cached=False,
                        would_be_cost_inr=round(would_be_val, 4),
                        cost_charged_inr=round(step_cost, 4),
                    )
                    self.tracer.record_step(trace)
                    break

                cached_flag = tool_res.get("cached", (step_cost == 0.0 and tool_res.get("success", False)))
                charged_amt = tool_res.get("cost_inr", step_cost)
                would_be_amt = tool_res.get("would_be_cost_inr", charged_amt if not cached_flag else est_cost)

                self.step_records.append({
                    "step": step,
                    "tool": tool_name,
                    "cached": cached_flag,
                    "would_be_cost_inr": round(would_be_amt, 4),
                    "cost_charged_inr": round(charged_amt, 4),
                    "arguments": args,
                    "result_summary": self._summarize_result(tool_res),
                })

                trace = StepTrace(
                    step=step,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    plan=f"Execute {tool_name}",
                    tool=tool_name,
                    arguments=args,
                    result_summary=self._summarize_result(tool_res),
                    latency_ms=tool_latency_ms,
                    llm_latency_ms=0,
                    tool_latency_ms=tool_latency_ms,
                    cost_inr=charged_amt,
                    cached=cached_flag,
                    would_be_cost_inr=round(would_be_amt, 4),
                    cost_charged_inr=round(charged_amt, 4),
                )
                self.tracer.record_step(trace)

                if not tool_res.get("success", False):
                    self.failures.append(f"Step {step} ({tool_name}) failed: {tool_res.get('error')}")

        else:
            # Fallback to interactive tool loop if plan was empty
            agent_status, final_answer = self._run_interactive_fallback(valid_files, file_summaries, step, final_answer)

        # --------------------------------------------------------------------
        # 4. Final Verification & Status Determination
        # --------------------------------------------------------------------
        # Verify all produced artifacts against real source
        self._verify_all_artifacts()

        # Collect failure notes from any failed verification into self.failures
        for v in self.verification_results:
            if not v.passed and v.notes not in self.failures:
                self.failures.append(v.notes)

        # Check for verification failures and artifact states
        has_failed_verification = any(not v.passed for v in self.verification_results)
        all_verifications_failed = (
            len(self.verification_results) > 0 and all(not v.passed for v in self.verification_results)
        )
        has_artifacts = len(self.artifacts) > 0
        any_failed_step = len(self.failures) > 0

        # Check if final requested artifact is missing based on task intent
        task_lower = self.task.lower()
        task_wants_speech = bool(
            re.search(r"\b(?:synthesiz\w*|speak\w*|read\s+(?:it\s+)?aloud|tts|generate\s+speech|voice\s+output)\b", task_lower)
        )
        task_wants_subtitles = bool(re.search(r"\b(?:subtitles|srt)\b", task_lower))
        task_wants_translation = bool(re.search(r"\b(?:translat\w*)\b", task_lower))
        task_wants_transcription = bool(re.search(r"\b(?:transcrib\w*)\b", task_lower))
        task_wants_ocr = bool(re.search(r"\b(?:ocr|extract\s+(?:the\s+)?text)\b", task_lower))

        has_speech = any(a.endswith(".wav") and "speech" in Path(a).name for a in self.artifacts)
        has_subtitles = any(a.endswith(".srt") for a in self.artifacts)
        has_translation = any("translated_" in Path(a).name for a in self.artifacts)
        has_transcript = any("transcript_" in Path(a).name for a in self.artifacts)
        has_ocr = any("ocr_" in Path(a).name for a in self.artifacts)

        final_artifact_missing = False
        if task_wants_speech and not has_speech:
            final_artifact_missing = True
            self.failures.append("Task requested speech/audio output, but no speech audio artifact was produced.")
        if task_wants_subtitles and not has_subtitles:
            final_artifact_missing = True
            self.failures.append("Task requested subtitles, but no SRT artifact was produced.")
        if task_wants_translation and not has_translation and not task_wants_speech:
            final_artifact_missing = True
            self.failures.append("Task requested translation, but no translated artifact was produced.")
        if task_wants_transcription and not has_transcript:
            final_artifact_missing = True
            self.failures.append("Task requested audio transcription, but no transcript artifact was produced.")
        if task_wants_ocr and not has_ocr and not has_translation:
            final_artifact_missing = True
            self.failures.append("Task requested OCR text extraction, but no OCR artifact was produced.")

        # Separate and prioritize statuses accurately
        if agent_status in ("timeout", "impossible_task", "budget_exceeded"):
            pass
        elif self.budget.remaining_inr <= 0.001 and self.budget.spent_inr >= self.max_cost_inr:
            agent_status = "budget_exceeded"
        elif any("budget" in f.lower() or "spend cap" in f.lower() or "exceeds remaining budget" in f.lower() for f in self.failures):
            agent_status = "budget_exceeded"
        elif step >= self.max_steps and (any_failed_step or final_artifact_missing):
            agent_status = "max_steps_reached"
        elif not has_artifacts or all_verifications_failed:
            agent_status = "failed"
            if not has_artifacts:
                self.failures.append("Task ended without producing any verified artifact.")
        elif any_failed_step or has_failed_verification or final_artifact_missing:
            # Some artifacts were produced, but a required step failed or verification failed
            agent_status = "partial"
        else:
            agent_status = "success"

        # Build final summary in plain code strictly from what actually occurred
        final_answer = self._build_plain_code_summary(agent_status)

        # Final Report Generation
        total_time = time.perf_counter() - start_time
        total_would_be = sum(s.get("would_be_cost_inr", 0.0) for s in self.step_records)
        report = AgentReport(
            status=agent_status,
            task=self.task,
            steps_taken=step,
            total_cost_inr=self.budget.spent_inr,
            total_would_be_cost_inr=round(total_would_be, 4),
            total_time_s=total_time,
            steps=self.step_records,
            assumptions=list(dict.fromkeys(self.assumptions)),
            verification_results=self.verification_results,
            artifacts=list(dict.fromkeys(self.artifacts)),
            failures=self.failures,
            audio_language=self.audio_language,
            target_language=self.target_language,
            language_requested=self.language_requested,
            detected_script=self.detected_script,
            windows_count=self.windows_count,
        )

        self.tracer.write_report(report)
        self.tracer.write_result_markdown(
            self.task, report.status, final_answer, report.assumptions, report.artifacts, report.failures
        )

        return report

    def _resolve_step_references(
        self,
        tool_name: str,
        args: dict[str, Any],
        valid_files: list[Path],
    ) -> tuple[bool, str | None]:
        """Resolves output references from earlier steps and rejects unresolved placeholders."""
        # 1. Inspect existing arguments for placeholder strings and resolve them
        for key, val in list(args.items()):
            if isinstance(val, str) and PLACEHOLDER_REGEX.search(val):
                resolved_text: str | None = None
                resolved_path: str | None = None
                resolved_stem: str | None = None

                val_lower = val.lower()

                # Find candidate step output matching the placeholder
                candidate_step = None
                if "extract_document_text" in val_lower or "ocr" in val_lower:
                    for s in reversed(self.step_outputs):
                        if s.get("tool") == "extract_document_text":
                            candidate_step = s
                            break
                elif "extract_audio_from_video" in val_lower or "extract_audio" in val_lower or "extracted" in val_lower:
                    for s in reversed(self.step_outputs):
                        if s.get("tool") == "extract_audio_from_video":
                            candidate_step = s
                            break
                elif "transcribe_audio" in val_lower or "transcription" in val_lower or "transcript" in val_lower:
                    for s in reversed(self.step_outputs):
                        if s.get("tool") == "transcribe_audio":
                            candidate_step = s
                            break
                elif "translate_text" in val_lower or "translation" in val_lower:
                    for s in reversed(self.step_outputs):
                        if s.get("tool") == "translate_text":
                            candidate_step = s
                            break
                elif "synthesize_speech" in val_lower or "speech" in val_lower:
                    for s in reversed(self.step_outputs):
                        if s.get("tool") == "synthesize_speech":
                            candidate_step = s
                            break
                elif re.search(r"step_(\d+)", val_lower):
                    m_idx = re.search(r"step_(\d+)", val_lower)
                    if m_idx:
                        idx = int(m_idx.group(1)) - 1
                        if 0 <= idx < len(self.step_outputs):
                            candidate_step = self.step_outputs[idx]
                elif any(kw in val_lower for kw in ("output", "result", "previous")):
                    if self.step_outputs:
                        candidate_step = self.step_outputs[-1]

                if candidate_step:
                    resolved_text = candidate_step.get("cleaned_text") or candidate_step.get("text")
                    resolved_path = candidate_step.get("artifact_path")
                    resolved_stem = candidate_step.get("artifact_stem")

                # Substitute if placeholder resolved
                if key in ("text", "input", "content") and resolved_text:
                    args[key] = clean_ocr_text(resolved_text)
                    if resolved_stem:
                        args["source_artifact_stem"] = resolved_stem
                elif key in ("file_path", "audio_path", "video_path") and resolved_path:
                    args[key] = resolved_path
                    if resolved_stem:
                        args["source_artifact_stem"] = resolved_stem
                elif resolved_text and tool_name in ("translate_text", "synthesize_speech"):
                    args[key] = clean_ocr_text(resolved_text)
                    if resolved_stem:
                        args["source_artifact_stem"] = resolved_stem

        # 2. Chaining rule: within a plan, if an earlier step produced text (for example a translation),
        # synthesize_speech and translate_text must use that produced text.
        # A file_path pointing at an original input file must never override it.
        # If both are present, use the step output and record a plain-code note in the report.
        if tool_name in ("translate_text", "synthesize_speech"):
            if self.last_produced_text and self.last_produced_text.strip():
                clean_prev_text = clean_ocr_text(self.last_produced_text)
                fp = args.get("file_path")
                curr_text = args.get("text", "")

                if fp:
                    # Both an earlier step output text AND a file_path are present.
                    # Previous step output takes strict priority over input file.
                    args["text"] = clean_prev_text
                    if self.last_artifact_stem:
                        args["source_artifact_stem"] = self.last_artifact_stem
                    note = (
                        f"Chaining override: Prioritized previous step's generated text output "
                        f"over input file '{fp}' for {tool_name}."
                    )
                    if note not in self.assumptions:
                        self.assumptions.append(note)
                    args.pop("file_path", None)
                elif not curr_text or not curr_text.strip():
                    args["text"] = clean_prev_text
                    if self.last_artifact_stem:
                        args["source_artifact_stem"] = self.last_artifact_stem
                else:
                    # Check if curr_text matches an original input file's content
                    is_input_text = any(
                        curr_text.strip() == src.strip() for src in self.source_texts.values() if src.strip()
                    )
                    if is_input_text:
                        args["text"] = clean_prev_text
                        if self.last_artifact_stem:
                            args["source_artifact_stem"] = self.last_artifact_stem
                        note = (
                            f"Chaining override: Prioritized previous step's generated text output "
                            f"over original input text for {tool_name}."
                        )
                        if note not in self.assumptions:
                            self.assumptions.append(note)
            else:
                # No earlier step produced text (first step in plan)
                curr_text = args.get("text", "")
                has_valid_file = bool(args.get("file_path") and Path(args["file_path"]).exists())
                if tool_name == "synthesize_speech" and (not curr_text or not curr_text.strip()) and has_valid_file:
                    p = Path(args["file_path"])
                    if p.suffix.lower() in (".txt", ".md", ".json"):
                        args["text"] = clean_ocr_text(p.read_text(encoding="utf-8"))
                        args["source_artifact_stem"] = p.stem

        # 3. Chaining rule: if an earlier step extracted audio from video, transcribe_audio must use it
        if tool_name == "transcribe_audio":
            for s in reversed(self.step_outputs):
                if s.get("tool") == "extract_audio_from_video" and s.get("artifact_path"):
                    fp = args.get("file_path", "")
                    if (
                        not fp
                        or Path(fp).suffix.lower() in (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v")
                        or PLACEHOLDER_REGEX.search(fp)
                    ):
                        args["file_path"] = s["artifact_path"]
                        if s.get("artifact_stem"):
                            args["source_artifact_stem"] = s["artifact_stem"]
                    break

        # 4. Clean OCR page markers from text if present
        if args.get("text"):
            args["text"] = clean_ocr_text(args["text"])

        # 5. Strictly reject any remaining unresolved placeholders
        for key, val in args.items():
            if isinstance(val, str):
                m = PLACEHOLDER_REGEX.search(val)
                if m:
                    return False, f"Unresolved placeholder '{m.group(0)}' in argument '{key}'"
            elif isinstance(val, (list, dict)):
                val_str = json.dumps(val)
                m = PLACEHOLDER_REGEX.search(val_str)
                if m:
                    return False, f"Unresolved placeholder '{m.group(0)}' in argument '{key}'"

        return True, None

    def _resolve_file_arguments(
        self, tool_name: str, args: dict[str, Any], valid_files: list[Path]
    ) -> tuple[bool, str | None]:
        """Resolves bare filenames or relative paths to input file absolute paths, or fails if unknown."""
        available_names = [f.name for f in valid_files]

        for key, val in list(args.items()):
            # Only resolve arguments representing input file paths (not output destinations)
            if key in (
                "file_path",
                "video_path",
                "audio_path",
                "input_path",
                "document_path",
                "pdf_path",
                "image_path",
            ) or (key.endswith(("_path", "_file")) and not key.startswith(("output_", "out_"))):
                if not isinstance(val, str) or not val.strip():
                    continue

                # Don't fail placeholders here; they are resolved in _resolve_step_references
                if PLACEHOLDER_REGEX.search(val):
                    continue

                val_path = Path(val)

                # 1. Match against input files: bare name, relative path, or stem
                matched_file = None
                for f in valid_files:
                    if (
                        val == f.name
                        or val == str(f)
                        or val_path.name == f.name
                        or val.lower() == f.name.lower()
                    ):
                        matched_file = f
                        break

                if matched_file:
                    args[key] = str(matched_file.resolve())
                    continue

                # 2. Match against artifacts generated so far in the run
                matched_art = None
                for art in self.artifacts:
                    art_p = Path(art)
                    if val == art_p.name or val == str(art_p) or val_path.name == art_p.name:
                        matched_art = art_p
                        break

                if matched_art and matched_art.exists():
                    args[key] = str(matched_art.resolve())
                    continue

                # 3. Check if it already exists as an absolute or local path
                if val_path.exists():
                    args[key] = str(val_path.resolve())
                    continue

                # 4. If it matches nothing and does not exist on disk, fail with clear listing
                return (
                    False,
                    f"File argument '{val}' for '{key}' does not match any known input file or generated artifact. "
                    f"Available input files: {available_names}",
                )

        return True, None

    def _bind_default_file_args(self, tool_name: str, args: dict[str, Any], valid_files: list[Path]) -> None:
        """Binds input file paths to tool arguments if LLM omitted them."""
        if not valid_files:
            return

        if tool_name == "translate_text":
            # If an input text file exists, bind its real file_path so the real source is translated
            # ONLY IF no earlier step produced text and file_path was omitted!
            if not self.last_produced_text:
                if not args.get("file_path"):
                    for f in valid_files:
                        if f.suffix.lower() in (".txt", ".md", ".json"):
                            args["file_path"] = str(f.resolve())
                            break
            if self.target_language and not args.get("target_language_code"):
                args["target_language_code"] = self.target_language
        elif tool_name == "synthesize_speech":
            if (self.target_language or self.language_requested) and not args.get("language"):
                args["language"] = self.target_language or self.language_requested
        elif tool_name == "transcribe_audio":
            if not args.get("file_path"):
                for s in reversed(self.step_outputs):
                    if s.get("tool") == "extract_audio_from_video" and s.get("artifact_path"):
                        args["file_path"] = s["artifact_path"]
                        break
                if not args.get("file_path"):
                    for f in valid_files:
                        if f.suffix.lower() in (
                            ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac",
                            ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"
                        ):
                            args["file_path"] = str(f.resolve())
                            break
            # Bind audio language requested in task if not explicitly passed by LLM
            if self.audio_language and not args.get("language"):
                args["language"] = self.audio_language
        elif tool_name == "extract_audio_from_video":
            if not args.get("video_path") and not args.get("file_path"):
                for f in valid_files:
                    if f.suffix.lower() in (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"):
                        args["video_path"] = str(f.resolve())
                        break
        elif tool_name == "extract_document_text":
            if not args.get("file_path"):
                for f in valid_files:
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf"):
                        args["file_path"] = str(f.resolve())
                        break

    def _estimate_step_cost(self, tool_name: str, args: dict[str, Any]) -> tuple[float, str]:
        """Estimates the INR cost of executing a tool call before calling the API."""
        if tool_name == "transcribe_audio":
            fp = args.get("file_path", "")
            dur = 0.0
            if fp:
                p = Path(fp)
                dur = (
                    self.audio_durations.get(str(p.resolve()))
                    or self.audio_durations.get(p.name)
                    or (get_audio_duration(p) if p.exists() else 0.0)
                )
            if dur <= 0.0:
                dur = 30.0
            cost = calculate_transcribe_cost(dur)
            return cost, f"{dur:.1f}s audio transcription"

        elif tool_name == "translate_text":
            text = args.get("text", "")
            if not text and args.get("file_path"):
                p = Path(args["file_path"])
                if p.exists() and p.suffix.lower() in (".txt", ".md", ".json"):
                    try:
                        text = p.read_text(encoding="utf-8")
                    except Exception:
                        text = ""
            if not text and self.last_produced_text:
                text = self.last_produced_text
            word_count = len(text.split()) if text else 100
            tokens = max(1, int(word_count * 1.5))
            cost = calculate_translate_cost(tokens)
            return cost, f"{word_count} words ({tokens} est. tokens) translation"

        elif tool_name == "synthesize_speech":
            text = args.get("text", "")
            if not text and args.get("file_path"):
                p = Path(args["file_path"])
                if p.exists() and p.suffix.lower() in (".txt", ".md", ".json"):
                    try:
                        text = p.read_text(encoding="utf-8")
                    except Exception:
                        text = ""
            if not text and self.last_produced_text:
                text = self.last_produced_text
            char_count = len(text) if text else 100
            cost = calculate_speak_cost(char_count)
            return cost, f"{char_count} chars speech synthesis"

        elif tool_name == "extract_document_text":
            fp = args.get("file_path", "")
            page_count = 1
            if fp:
                p = Path(fp)
                if p.suffix.lower() == ".pdf" and p.exists():
                    try:
                        import pypdfium2 as pdfium
                        doc = pdfium.PdfDocument(p)
                        page_count = max(1, len(doc))
                        doc.close()
                    except Exception:
                        page_count = 1
            cost = calculate_ocr_cost(page_count)
            return cost, f"{page_count} page(s) OCR"

        return 0.0, "free operation"

    def _is_step_cached(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Checks if a step's result is already cached in disk cache."""
        try:
            if not hasattr(self.tools, "cache") or not self.tools.cache:
                return False
            cache = self.tools.cache

            if tool_name == "translate_text":
                text = args.get("text", "")
                if not text and args.get("file_path"):
                    p = Path(args["file_path"])
                    if p.exists() and p.suffix.lower() in (".txt", ".md", ".json"):
                        text = p.read_text(encoding="utf-8")
                if not text and self.last_produced_text:
                    text = self.last_produced_text
                target_lang = args.get("target_language_code", "hi")
                script = args.get("target_script", "native")
                key = cache.generate_key("indic-translate", text.strip().encode("utf-8"), target_lang=target_lang, script=script)
                return cache.has(key)

            elif tool_name == "synthesize_speech":
                text = args.get("text", "")
                if not text and args.get("file_path"):
                    p = Path(args["file_path"])
                    if p.exists() and p.suffix.lower() in (".txt", ".md", ".json"):
                        text = p.read_text(encoding="utf-8")
                if not text and self.last_produced_text:
                    text = self.last_produced_text
                lang = args.get("language", "hi")
                voice = args.get("voice") or "default"
                style = args.get("style") or "default"
                key = cache.generate_key("indic-speak", text.strip().encode("utf-8"), language=lang, voice=voice, style=style)
                return cache.has(key)

            elif tool_name == "extract_document_text":
                fp = args.get("file_path", "")
                if fp:
                    p = Path(fp)
                    if p.exists() and p.suffix.lower() != ".pdf":
                        key = cache.generate_key("indic-ocr", p.read_bytes())
                        return cache.has(key)

            elif tool_name == "transcribe_audio":
                # Audio transcription caching is per-window after 16k mono WAV conversion
                return False
        except Exception:
            return False
        return False

    def _execute_and_save_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatches tool call and automatically saves outputs to artifacts/ in plain code."""
        try:
            if name == "translate_text":
                res = self.tools.translate_text(
                    text=args.get("text", ""),
                    file_path=args.get("file_path", ""),
                    target_language_code=args.get("target_language_code", "hi"),
                    target_script=args.get("target_script", "native"),
                )
                if res.get("success") and res.get("translated_text"):
                    target_lang = res.get("target_language") or args.get("target_language_code") or "hi"
                    if args.get("source_artifact_stem"):
                        stem = args["source_artifact_stem"]
                    elif args.get("file_path") and Path(args["file_path"]).exists():
                        stem = Path(args["file_path"]).stem
                    elif self.last_artifact_stem:
                        stem = self.last_artifact_stem
                    else:
                        stem = "text"
                    art_file = self.tracer.artifacts_dir / f"translated_{target_lang}_{stem}.txt"
                    art_file.write_text(res["translated_text"], encoding="utf-8")
                    saved_path = str(art_file.resolve())
                    self.artifacts.append(saved_path)
                    res["artifact_path"] = saved_path

                    input_source = args.get("text", "")
                    if not input_source and args.get("file_path") and Path(args["file_path"]).exists():
                        input_source = Path(args["file_path"]).read_text(encoding="utf-8")

                    self.last_produced_text = res["translated_text"]
                    self.last_artifact_path = saved_path
                    self.last_artifact_stem = art_file.stem
                    self.step_outputs.append({
                        "tool": "translate_text",
                        "text": res["translated_text"],
                        "cleaned_text": res["translated_text"],
                        "artifact_path": saved_path,
                        "artifact_stem": art_file.stem,
                    })
                    self.upstream_sources[art_file.name] = {
                        "source_text": input_source,
                        "source_stem": stem,
                        "target_lang": target_lang,
                    }
                return res

            elif name == "transcribe_audio":
                res = self.tools.transcribe_audio(
                    file_path=args.get("file_path", ""),
                    language=args.get("language"),
                    vad=args.get("vad", True),
                )
                if res.get("success") and res.get("transcript"):
                    stem = Path(args.get("file_path", "audio")).stem
                    # Auto-save transcript
                    art_file = self.tracer.artifacts_dir / f"transcript_{stem}.txt"
                    art_file.write_text(res["transcript"], encoding="utf-8")
                    saved_path = str(art_file.resolve())
                    self.artifacts.append(saved_path)
                    res["artifact_path"] = saved_path

                    # Record metadata from tool
                    self.windows_count = res.get("windows_count", len(res.get("windows", [])))
                    if res.get("detected_script"):
                        self.detected_script = res.get("detected_script")
                    if res.get("language_requested"):
                        self.language_requested = res.get("language_requested")

                    self.last_transcription_result = res
                    dur_val = res.get("duration_s", 0.0) or res.get("total_duration_s", 0.0)
                    self.audio_durations[saved_path] = dur_val
                    self.audio_durations[art_file.name] = dur_val

                    self.last_produced_text = res["transcript"]
                    self.last_artifact_path = saved_path
                    self.last_artifact_stem = art_file.stem
                    self.step_outputs.append({
                        "tool": "transcribe_audio",
                        "text": res["transcript"],
                        "cleaned_text": res["transcript"],
                        "artifact_path": saved_path,
                        "artifact_stem": art_file.stem,
                    })
                    self.upstream_sources[art_file.name] = {
                        "source_text": res["transcript"],
                        "source_file": args.get("file_path"),
                    }

                    # Auto-save SRT if windows exist
                    if res.get("windows"):
                        srt_file = self.tracer.artifacts_dir / f"subtitles_{stem}.srt"
                        self.tools.build_srt(res["windows"], output_path=str(srt_file))
                        self.artifacts.append(str(srt_file.resolve()))
                return res

            elif name == "synthesize_speech":
                lang = args.get("language", "hi")
                if args.get("source_artifact_stem"):
                    stem = args["source_artifact_stem"]
                elif self.last_artifact_stem:
                    stem = self.last_artifact_stem
                elif args.get("file_path") and Path(args.get("file_path", "")).exists():
                    stem = Path(args["file_path"]).stem
                else:
                    stem = f"speech_{int(time.time())}"
                out_p = args.get("output_path") or str(
                    self.tracer.artifacts_dir / f"speech_{lang}_{stem}.wav"
                )
                res = self.tools.synthesize_speech(
                    text=args.get("text", ""),
                    language=lang,
                    voice=args.get("voice"),
                    style=args.get("style"),
                    output_path=out_p,
                )
                if res.get("success") and res.get("audio_path"):
                    self.artifacts.append(res["audio_path"])
                    self.last_artifact_path = res["audio_path"]
                    self.last_artifact_stem = Path(res["audio_path"]).stem
                    self.step_outputs.append({
                        "tool": "synthesize_speech",
                        "artifact_path": res["audio_path"],
                        "artifact_stem": Path(res["audio_path"]).stem,
                        "text": args.get("text", ""),
                    })
                    self.upstream_sources[Path(res["audio_path"]).name] = {
                        "source_text": args.get("text", ""),
                        "source_stem": stem,
                        "lang": lang,
                    }
                return res

            elif name == "extract_document_text":
                res = self.tools.extract_document_text(file_path=args.get("file_path", ""))
                if res.get("success") and res.get("text"):
                    stem = Path(args.get("file_path", "doc")).stem
                    ocr_stem = stem if stem.startswith("ocr_") else f"ocr_{stem}"
                    art_file = self.tracer.artifacts_dir / f"{ocr_stem}.md"
                    art_file.write_text(res["text"], encoding="utf-8")
                    saved_path = str(art_file.resolve())
                    self.artifacts.append(saved_path)
                    res["artifact_path"] = saved_path

                    cleaned = clean_ocr_text(res["text"])
                    self.last_produced_text = cleaned
                    self.last_artifact_path = saved_path
                    self.last_artifact_stem = art_file.stem
                    self.step_outputs.append({
                        "tool": "extract_document_text",
                        "text": res["text"],
                        "cleaned_text": cleaned,
                        "artifact_path": saved_path,
                        "artifact_stem": art_file.stem,
                    })
                    self.upstream_sources[art_file.name] = {
                        "source_text": res["text"],
                        "cleaned_text": cleaned,
                        "source_file": args.get("file_path"),
                    }
                return res

            elif name == "inspect_media":
                return self.tools.inspect_media(file_path=args.get("file_path", ""))

            elif name == "detect_language":
                return self.tools.detect_language(text=args.get("text"), audio_path=args.get("audio_path"))

            elif name == "extract_audio_from_video":
                v_path_str = args.get("video_path") or args.get("file_path", "")
                stem = Path(v_path_str).stem if v_path_str else f"video_{int(time.time())}"
                out_p = args.get("output_path") or str(
                    self.tracer.artifacts_dir / f"extracted_{stem}.wav"
                )
                res = self.tools.extract_audio_from_video(video_path=v_path_str, output_path=out_p)
                if res.get("success") and res.get("audio_path"):
                    self.artifacts.append(res["audio_path"])
                    self.last_artifact_path = res["audio_path"]
                    self.last_artifact_stem = Path(res["audio_path"]).stem
                    dur = float(res.get("duration_s") or 0.0)
                    if dur <= 0.0 and Path(res["audio_path"]).exists():
                        try:
                            dur = get_audio_duration(res["audio_path"])
                        except Exception:
                            dur = 0.0
                    self.audio_durations[res["audio_path"]] = dur
                    self.audio_durations[Path(res["audio_path"]).name] = dur
                    self.step_outputs.append({
                        "tool": "extract_audio_from_video",
                        "artifact_path": res["audio_path"],
                        "artifact_stem": Path(res["audio_path"]).stem,
                    })
                return res

            elif name == "build_srt":
                out_p = args.get("output_path") or str(
                    self.tracer.artifacts_dir / f"subtitles_{int(time.time())}.srt"
                )
                segs = args.get("segments_json", [])
                if isinstance(segs, str):
                    try:
                        segs = json.loads(segs)
                    except json.JSONDecodeError:
                        segs = []
                res = self.tools.build_srt(segs, output_path=out_p)
                if res.get("success") and res.get("srt_path"):
                    self.artifacts.append(res["srt_path"])
                return res

            elif name == "finish_task":
                return {"success": True, "finish": True}

            else:
                return {"success": False, "error": f"Unknown tool: {name}"}

        except BudgetExceededError as be:
            return {"success": False, "error": str(be), "budget_exceeded": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _verify_all_artifacts(self) -> None:
        """Verifies produced artifacts against real source content in plain code."""
        for art_path_str in list(self.artifacts):
            art_path = Path(art_path_str)
            if not art_path.exists():
                continue

            # Verify Translations
            if art_path.name.startswith("translated_") and art_path.suffix in (".txt", ".md"):
                content = art_path.read_text(encoding="utf-8").strip()
                target_lang = "en"
                name_parts = art_path.stem.split("_")
                if len(name_parts) >= 2 and len(name_parts[1]) in (2, 3):
                    target_lang = name_parts[1]

                # Find upstream source text
                up_info = self.upstream_sources.get(art_path.name, {})
                if up_info.get("target_lang"):
                    target_lang = up_info["target_lang"]
                upstream_text = up_info.get("source_text", "").strip()
                if not upstream_text:
                    for src_text in self.source_texts.values():
                        if src_text.strip():
                            upstream_text = src_text.strip()
                            break

                if not upstream_text:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_path.name,
                            verification_type="source_fidelity",
                            passed=False,
                            notes="Verification failed: No upstream source text available for fidelity check.",
                        )
                    )
                    continue

                if not content:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_path.name,
                            verification_type="source_fidelity",
                            passed=False,
                            notes="Translation verification failed: Output translation artifact is empty.",
                        )
                    )
                    continue

                # 1. Output script must match target language (Requirement 4)
                expected_script = EXPECTED_SCRIPT_FOR_LANG.get(target_lang, target_lang)
                expected_script_name = SCRIPT_NAMES.get(expected_script, expected_script)
                detected_target_script = detect_script_language(content)
                detected_script_name = SCRIPT_NAMES.get(detected_target_script, detected_target_script or "Unknown")

                if detected_target_script != expected_script:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_path.name,
                            verification_type="target_script_matching",
                            passed=False,
                            notes=(
                                f"Translation verification failed: Target language '{target_lang}' expected "
                                f"{expected_script_name} script, but translated text is in "
                                f"{detected_script_name} script ({detected_target_script})."
                            ),
                        )
                    )
                    continue

                # 2. Verify that the translation is not simply an exact copy of the source (Requirement 4)
                is_exact_copy = (
                    content.strip().lower() == upstream_text.strip().lower()
                    or any(content.strip().lower() == src.strip().lower() for src in self.source_texts.values() if src.strip())
                )
                if is_exact_copy and target_lang != "en":
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_path.name,
                            verification_type="source_fidelity",
                            passed=False,
                            notes="Translation verification failed: Translated text is an exact copy of the source text (untranslated).",
                        )
                    )
                    continue
                elif content.strip().lower() == upstream_text.strip().lower() and target_lang == "en":
                    detected_up_script = detect_script_language(upstream_text)
                    if detected_up_script != "en":
                        self.verification_results.append(
                            VerificationResult(
                                artifact=art_path.name,
                                verification_type="source_fidelity",
                                passed=False,
                                notes="Translation verification failed: Translated text is an exact copy of the non-English source text.",
                            )
                        )
                        continue

                # 3. Back-translate artifact to English
                if target_lang == "en":
                    back_en = content
                else:
                    back_res = self.tools.translate_text(text=content, target_language_code="en")
                    back_en = back_res.get("translated_text", "").strip()

                # 4. Convert upstream text to English for comparison
                detected_up_script = detect_script_language(upstream_text)
                if detected_up_script == "en":
                    source_en = upstream_text
                else:
                    up_en_res = self.tools.translate_text(text=upstream_text, target_language_code="en")
                    source_en = up_en_res.get("translated_text", "").strip()

                passed, notes = self._verify_translation_fidelity(source_en, back_en)
                self.verification_results.append(
                    VerificationResult(
                        artifact=str(art_path.name),
                        verification_type="source_fidelity",
                        passed=passed,
                        notes=notes,
                    )
                )

            # Verify Synthesized Speech
            elif art_path.suffix.lower() == ".wav" and "speech" in art_path.name:
                art_name = art_path.name
                if not art_path.exists() or art_path.stat().st_size == 0:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_name,
                            verification_type="speech_quality",
                            passed=False,
                            notes="Speech verification failed: Generated audio file is missing or empty (0 bytes).",
                        )
                    )
                    continue

                dur = get_audio_duration(art_path)
                up_info = self.upstream_sources.get(art_name, {})
                source_text = up_info.get("source_text", "").strip()
                speech_lang = up_info.get("lang") or getattr(self, "target_language", None) or getattr(self, "audio_language", None) or "hi"
                expected_script = EXPECTED_SCRIPT_FOR_LANG.get(speech_lang, speech_lang)
                expected_script_name = SCRIPT_NAMES.get(expected_script, expected_script)

                if dur <= 0.2:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_name,
                            verification_type="speech_quality",
                            passed=False,
                            notes=f"Speech verification failed: Audio duration is too short or empty ({dur:.2f}s).",
                        )
                    )
                    continue

                if not source_text:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_name,
                            verification_type="speech_quality",
                            passed=False,
                            notes="Speech verification failed: No intended speech text recorded for verification.",
                        )
                    )
                    continue

                if len(source_text) >= 15:
                    cps = len(source_text) / dur if dur > 0 else 0
                    if cps < 1.0 or cps > 45.0:
                        self.verification_results.append(
                            VerificationResult(
                                artifact=art_name,
                                verification_type="speech_quality",
                                passed=False,
                                notes=(
                                    f"Speech verification failed: Audio duration ({dur:.2f}s) is implausible "
                                    f"for text length ({len(source_text)} chars, {cps:.1f} chars/s)."
                                ),
                            )
                        )
                        continue

                # Check script of the text that was supposed to be spoken
                source_script = detect_script_language(source_text)
                if source_script != expected_script:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_name,
                            verification_type="speech_quality",
                            passed=False,
                            notes=(
                                f"Speech verification failed: Intended speech text for '{speech_lang}' is in "
                                f"{SCRIPT_NAMES.get(source_script, source_script or 'Unknown')} script, "
                                f"expected {expected_script_name} script."
                            ),
                        )
                    )
                    continue

                # Back-transcribe with indic-transcribe (capped at ₹0.30 and budget respected)
                est_cost = calculate_transcribe_cost(dur)
                if est_cost <= 0.30 and self.budget.remaining_inr >= est_cost:
                    back_trans = self.tools.transcribe_audio(str(art_path), language=speech_lang)
                    transcript = back_trans.get("transcript", "").strip()
                    if not transcript:
                        self.verification_results.append(
                            VerificationResult(
                                artifact=art_name,
                                verification_type="speech_back_transcription",
                                passed=False,
                                notes="Speech verification failed: Back-transcription produced no text.",
                            )
                        )
                    else:
                        detected_trans_script = detect_script_language(transcript)
                        if detected_trans_script != expected_script:
                            self.verification_results.append(
                                VerificationResult(
                                    artifact=art_name,
                                    verification_type="speech_back_transcription",
                                    passed=False,
                                    notes=(
                                        f"Speech verification failed: Audio was expected to be spoken in '{speech_lang}' "
                                        f"({expected_script_name} script), but back-transcription returned text in "
                                        f"{SCRIPT_NAMES.get(detected_trans_script, detected_trans_script or 'Unknown')} script: '{transcript[:100]}'."
                                    ),
                                )
                            )
                        else:
                            src_words = source_text.split()
                            trans_words = transcript.split()
                            ratio = len(trans_words) / float(len(src_words)) if len(src_words) > 0 else 1.0
                            if ratio < 0.25 or ratio > 3.5:
                                self.verification_results.append(
                                    VerificationResult(
                                        artifact=art_name,
                                        verification_type="speech_back_transcription",
                                        passed=False,
                                        notes=(
                                            f"Speech verification failed back-transcription length check: "
                                            f"source has {len(src_words)} words, transcript has {len(trans_words)} words (ratio={ratio:.2f})."
                                        ),
                                    )
                                )
                            else:
                                self.verification_results.append(
                                    VerificationResult(
                                        artifact=art_name,
                                        verification_type="speech_back_transcription",
                                        passed=True,
                                        notes=(
                                            f"Verified speech audio ({dur:.2f}s, {len(source_text)} chars) in {expected_script_name} script "
                                            f"with back-transcription fidelity ({len(transcript)} chars)."
                                        ),
                                    )
                                )
                else:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_name,
                            verification_type="speech_quality",
                            passed=True,
                            notes=f"Verified speech audio file exists and duration is plausible ({dur:.2f}s).",
                        )
                    )

            # Verify Audio Transcripts
            elif art_path.name.startswith("transcript_") and art_path.suffix.lower() == ".txt":
                self._verify_transcript_artifact(art_path)

    def _verify_transcript_artifact(self, art_path: Path) -> None:
        """Verifies transcript text quality, duration plausibility, repetition, and script matching in plain code."""
        content = art_path.read_text(encoding="utf-8").strip()
        art_name = art_path.name

        # 1. Not empty check
        if not content:
            self.verification_results.append(
                VerificationResult(
                    artifact=art_name,
                    verification_type="transcript_quality",
                    passed=False,
                    notes="Transcript verification failed: Output transcript is empty.",
                )
            )
            return

        # 2. Duration and CPS plausibility check
        duration_s = (
            self.audio_durations.get(str(art_path.resolve()))
            or self.audio_durations.get(art_path.name)
            or getattr(self, "last_transcription_result", {}).get("duration_s", 0.0)
            or getattr(self, "last_transcription_result", {}).get("total_duration_s", 0.0)
        )

        cps = (len(content) / duration_s) if duration_s > 0 else 0.0
        if duration_s >= 5.0 and cps < 0.8:
            self.verification_results.append(
                VerificationResult(
                    artifact=art_name,
                    verification_type="transcript_quality",
                    passed=False,
                    notes=f"Transcript verification failed: Character rate too low ({cps:.2f} chars/s for {duration_s:.1f}s audio). Possible dropped speech.",
                )
            )
            return

        if cps > 50.0:
            self.verification_results.append(
                VerificationResult(
                    artifact=art_name,
                    verification_type="transcript_quality",
                    passed=False,
                    notes=f"Transcript verification failed: Character rate implausibly high ({cps:.2f} chars/s for {duration_s:.1f}s audio). Possible looping hallucination.",
                )
            )
            return

        # 3. No repeated looping phrases (n-gram repetition check)
        words = content.split()
        if len(words) >= 6:
            tri_grams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
            for i in range(len(tri_grams) - 2):
                if tri_grams[i] == tri_grams[i + 1] == tri_grams[i + 2]:
                    self.verification_results.append(
                        VerificationResult(
                            artifact=art_name,
                            verification_type="transcript_quality",
                            passed=False,
                            notes=f"Transcript verification failed: Detected looping phrase repeated consecutively: '{tri_grams[i]}'.",
                        )
                    )
                    return

        # 4. Detected script and language consistency
        detected_lang = detect_script_language(content)
        detected_script_name = SCRIPT_NAMES.get(detected_lang or "", "Unknown")
        self.detected_script = detected_script_name

        if self.audio_language:
            expected_script = EXPECTED_SCRIPT_FOR_LANG.get(self.audio_language, self.audio_language)
            if detected_lang != expected_script:
                self.verification_results.append(
                    VerificationResult(
                        artifact=art_name,
                        verification_type="transcript_quality",
                        passed=False,
                        notes=(
                            f"Transcript verification failed: Task requested audio language '{self.audio_language}' "
                            f"(expected {SCRIPT_NAMES.get(expected_script, expected_script)} script), "
                            f"but transcript is in {detected_script_name} script ({detected_lang})."
                        ),
                    )
                )
                return

        # 5. Check flagged / suspicious windows
        last_res = getattr(self, "last_transcription_result", {})
        flagged_wins = [w for w in last_res.get("windows", []) if w.get("suspicious")]
        if flagged_wins:
            win_idxs = [w.get("window_index") for w in flagged_wins]
            self.verification_results.append(
                VerificationResult(
                    artifact=art_name,
                    verification_type="transcript_quality",
                    passed=False,
                    notes=f"Transcript verification flagged {len(flagged_wins)} suspicious empty window(s): {win_idxs}.",
                )
            )
            return

        cps_str = f", {cps:.1f} chars/s" if duration_s > 0 else ""
        wins_str = f", {self.windows_count} window(s)" if self.windows_count else ""
        self.verification_results.append(
            VerificationResult(
                artifact=art_name,
                verification_type="transcript_quality",
                passed=True,
                notes=f"Verified transcript: {len(content)} chars{cps_str}{wins_str}, script: {detected_script_name}.",
            )
        )

    def _build_plain_code_summary(self, status: str) -> str:
        """Constructs an honest, plain-code summary of what actually happened."""
        if status == "impossible_task":
            return (
                f"The task requested an unsupported language or invalid operation. "
                f"Bodhan Indic models exclusively support the 22 scheduled Indian languages and English. "
                f"No mock or fabricated output was generated."
            )
        if status == "timeout":
            return f"The task timed out during execution. {self.failures[0] if self.failures else ''}"
        if status == "budget_exceeded":
            budget_err = next((f for f in self.failures if "budget" in f.lower() or "cost" in f.lower() or "exceed" in f.lower()), None)
            if budget_err:
                return f"Task halted due to budget limit: {budget_err}"
            return (
                f"The task halted because the spend cap (₹{self.max_cost_inr:.4f}) was exceeded. "
                f"Total spent: ₹{self.budget.spent_inr:.4f} across {len(self.executed_calls)} executed call(s)."
            )
        if status == "max_steps_reached":
            return f"The task reached the maximum step limit ({self.max_steps}) before full completion."
        if status == "failed":
            reasons = "; ".join(self.failures) if self.failures else "Task did not produce verified artifacts."
            return f"Task execution failed: {reasons}"
        if status == "partial":
            reasons = "; ".join(self.failures) if self.failures else "One or more steps failed."
            return f"The task completed partially with {len(self.artifacts)} artifact(s), but encountered failures: {reasons}"

        # Status is success
        parts = []
        if any("transcript_" in a for a in self.artifacts):
            dur = getattr(self, "last_transcription_result", {}).get("duration_s", "")
            script = self.detected_script or "Indic"
            wins = self.windows_count or 1
            dur_str = f" ({dur}s)" if dur else ""
            lang_str = f" ({self.language_requested})" if self.language_requested else ""
            parts.append(
                f"Transcribed audio{dur_str} across {wins} window(s) using Indic-Transcribe into {script} script{lang_str}"
            )
        if any("subtitles_" in a for a in self.artifacts):
            parts.append("generated approximate window-based SRT subtitles")
        if any("translated_" in a for a in self.artifacts):
            parts.append("translated text using Indic-Translate with verified English back-translation fidelity")
        if any("speech_" in a for a in self.artifacts):
            parts.append("synthesized speech audio using Indic-Speak")
        if any("ocr_" in a for a in self.artifacts):
            parts.append("extracted document text using Indic-OCR")

        if parts:
            return "Completed task: " + "; ".join(parts) + "."
        return "Task completed successfully and artifacts were verified."

    def _verify_translation_fidelity(self, source_en: str, back_en: str) -> tuple[bool, str]:
        """Compares back-translated English against real source English."""
        if not source_en:
            return False, "Translation verification failed: No source text available to verify fidelity against."

        src_words = source_en.split()
        back_words = back_en.split()

        if len(src_words) == 0:
            return False, "Source text was empty."
        if len(back_words) == 0:
            return False, "Back-translated text was empty."

        # 1. Word length ratio check (must be reasonably proportional)
        ratio = len(back_words) / float(len(src_words))
        if ratio < 0.4 or ratio > 2.5:
            return (
                False,
                f"Translation failed length ratio check: source has {len(src_words)} words, "
                f"back-translation has {len(back_words)} words (ratio={ratio:.2f}). Possible invented or truncated text.",
            )

        # 2. Key tokens / content words overlap
        src_tokens = {re.sub(r"\W+", "", w).lower() for w in src_words if len(w) >= 4}
        back_tokens = {re.sub(r"\W+", "", w).lower() for w in back_words if len(w) >= 4}

        overlap = len(src_tokens & back_tokens)
        min_overlap = max(1, int(len(src_tokens) * 0.20))

        if overlap < min_overlap:
            return (
                False,
                f"Translation failed token overlap check: matched {overlap}/{len(src_tokens)} key words. "
                f"Content does not correspond to the source file.",
            )

        return (
            True,
            f"Fidelity check passed: word ratio={ratio:.2f}, key token overlap={overlap}/{len(src_tokens)} words.",
        )

    def _run_interactive_fallback(
        self,
        valid_files: list[Path],
        file_summaries: list[dict[str, Any]],
        step: int,
        final_answer: str,
    ) -> tuple[str, str]:
        """Fallback tool calling loop if one-shot plan generation was empty."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"User Task: {self.task}\n\n"
                    f"Files:\n<untrusted_data files_metadata>\n{json.dumps(file_summaries, ensure_ascii=False, indent=2)}\n</untrusted_data>\n"
                    f"Call the appropriate tool using local file paths."
                ),
            },
        ]

        while step < self.max_steps:
            step += 1
            if self.budget.remaining_inr <= 0.0:
                return "budget_exceeded", "Budget exhausted in fallback loop."

            try:
                content, tool_calls, llm_ms = self.llm.call(messages)
            except LLMTimeoutError as te:
                self.failures.append(str(te))
                return "timeout", f"Fallback loop timed out: {te}"
            except Exception as le:
                self.failures.append(str(le))
                status = "timeout" if "timeout" in str(le).lower() else "failed"
                return status, f"Fallback loop failed: {le}"

            if not tool_calls:
                break

            tc = tool_calls[0]
            name = tc.get("name", "")
            args = tc.get("arguments", {})

            if name == "finish_task":
                final_answer = args.get("final_answer", content)
                break

            self._bind_default_file_args(name, args, valid_files)
            resolved_file_ok, file_err = self._resolve_file_arguments(name, args, valid_files)
            if not resolved_file_ok:
                self.failures.append(f"Step {step} ({name}) failed: {file_err}")
                break
            call_sig = f"{name}:{json.dumps(args, sort_keys=True)}"
            if call_sig in self.executed_calls:
                # Reject duplicate
                break

            # Pre-step budget estimate in fallback loop
            est_cost, est_desc = self._estimate_step_cost(name, args)
            is_step_cached = self._is_step_cached(name, args)
            if not is_step_cached and est_cost > self.budget.remaining_inr:
                fail_msg = (
                    f"Estimated cost of {name} for {est_desc} (₹{est_cost:.4f}) exceeds "
                    f"remaining budget (₹{self.budget.remaining_inr:.4f}). "
                    f"Task stopped before making paid API calls."
                )
                self.failures.append(fail_msg)
                self.step_records.append({
                    "step": step,
                    "tool": name,
                    "cached": False,
                    "would_be_cost_inr": round(est_cost, 4),
                    "cost_charged_inr": 0.0,
                    "arguments": args,
                    "result_summary": f"Halted: {fail_msg}",
                })
                rej_trace = StepTrace(
                    step=step,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    plan=f"Pre-check budget for {name}",
                    tool=name,
                    arguments=args,
                    result_summary=f"Halted: {fail_msg}",
                    latency_ms=0,
                    llm_latency_ms=0,
                    tool_latency_ms=0,
                    cost_inr=0.0,
                    cached=False,
                    would_be_cost_inr=round(est_cost, 4),
                    cost_charged_inr=0.0,
                )
                self.tracer.record_step(rej_trace)
                return "budget_exceeded", fail_msg

            cost_before = self.budget.spent_inr
            t0 = time.perf_counter()
            tool_res = self._execute_and_save_tool(name, args)
            tool_ms = int((time.perf_counter() - t0) * 1000)
            step_cost = self.budget.spent_inr - cost_before
            self.executed_calls[call_sig] = tool_res

            cached_flag = tool_res.get("cached", (step_cost == 0.0 and tool_res.get("success", False)))
            charged_amt = tool_res.get("cost_inr", step_cost)
            would_be_amt = tool_res.get("would_be_cost_inr", charged_amt if not cached_flag else est_cost)

            self.step_records.append({
                "step": step,
                "tool": name,
                "cached": cached_flag,
                "would_be_cost_inr": round(would_be_amt, 4),
                "cost_charged_inr": round(charged_amt, 4),
                "arguments": args,
                "result_summary": self._summarize_result(tool_res),
            })

            trace = StepTrace(
                step=step,
                timestamp=datetime.now(timezone.utc).isoformat(),
                plan=content[:100] if content else f"Call {name}",
                tool=name,
                arguments=args,
                result_summary=self._summarize_result(tool_res),
                latency_ms=llm_ms + tool_ms,
                llm_latency_ms=llm_ms,
                tool_latency_ms=tool_ms,
                cost_inr=charged_amt,
                cached=cached_flag,
                would_be_cost_inr=round(would_be_amt, 4),
                cost_charged_inr=round(charged_amt, 4),
            )
            self.tracer.record_step(trace)

            if tool_res.get("budget_exceeded") or (
                not tool_res.get("success", False) and "budget" in str(tool_res.get("error", "")).lower()
            ):
                fail_msg = f"Step {step} ({name}) exceeded budget: {tool_res.get('error')}"
                self.failures.append(fail_msg)
                return "budget_exceeded", fail_msg

            # Auto-finish once an artifact is produced and verified
            if self.artifacts:
                final_answer = "Task completed and artifacts saved."
                break

        return "success", final_answer

    @staticmethod
    def _summarize_result(res: dict[str, Any]) -> str:
        """Returns concise one-line summary for tracing."""
        if not res.get("success", False):
            return f"Failed: {res.get('error', 'unknown error')[:120]}"

        parts = []
        for k in ("artifact_path", "transcript", "translated_text", "text", "audio_path", "srt_path", "file_name"):
            if k in res:
                val = str(res[k])
                preview = (val[:50] + "...") if len(val) > 50 else val
                parts.append(f"{k}='{preview}'")
        if "cost_inr" in res:
            parts.append(f"cost=₹{res['cost_inr']:.4f}")
        return ", ".join(parts) if parts else "Success"
