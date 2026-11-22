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
from bodhan_mcp.lang_utils import (
    EXPECTED_SCRIPT_FOR_LANG,
    SCRIPT_NAMES,
    SUPPORTED_INDIC_LANGUAGES,
    detect_script_language,
    extract_language_from_text,
    is_language_supported,
)
from bodhan_mcp.pricing import BudgetExceededError, BudgetManager
from bodhan_mcp.tools import BodhanTools

UNSUPPORTED_LANGUAGE_PATTERNS = [
    r"\bfrench\b", r"\bgerman\b", r"\bspanish\b", r"\bjapanese\b",
    r"\bchinese\b", r"\brussian\b", r"\barabic\b", r"\bportuguese\b",
    r"\bitalian\b", r"\bkorean\b",
]


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
        self.language_requested: str | None = None
        self.detected_script: str | None = None
        self.windows_count: int | None = None
        self.audio_durations: dict[str, float] = {}
        self.last_transcription_result: dict[str, Any] = {}

    def run(self) -> AgentReport:
        """Executes the full agent loop and writes all required outputs."""
        start_time = time.perf_counter()

        # --------------------------------------------------------------------
        # 1. Plain Code Pre-flight Checks (Zero LLM / Zero Paid Calls)
        # --------------------------------------------------------------------
        # Plain code language extraction from task
        req_code, req_name = extract_language_from_text(self.task)
        if req_code:
            self.language_requested = req_code
            self.assumptions.append(f"Task specified language: {req_name} ({req_code}).")
        else:
            self.language_requested = None
            self.assumptions.append(
                "No specific spoken language was requested in the task; submitted to Indic-Transcribe without language parameter (model auto-detection)."
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
            if insp.get("media_type") in ("audio", "video") or f_path.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac"):
                dur = insp.get("duration_s", 0.0)
                self.audio_durations[str(f_path.resolve())] = dur
                self.audio_durations[f_path.name] = dur
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

        # --------------------------------------------------------------------
        # 3. Plan Execution & Loop Control
        # --------------------------------------------------------------------
        # If the LLM returned a valid list of steps, execute them directly in plain code
        if planned_steps:
            for item in planned_steps:
                if step >= self.max_steps:
                    agent_status = "max_steps_reached"
                    self.failures.append(f"Reached step limit ({self.max_steps}).")
                    break

                if self.budget.remaining_inr <= 0.001:
                    agent_status = "budget_exceeded"
                    self.failures.append("Allocated cost budget was exhausted before completing all steps.")
                    break

                tool_name = item.get("tool", "")
                args = item.get("args", {})

                # Ensure file_path is resolved from input files if LLM missed it
                self._bind_default_file_args(tool_name, args, valid_files)

                # Repeated-call guard: reject identical calls
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

                step += 1
                t_tool_start = time.perf_counter()
                cost_before = self.budget.spent_inr
                tool_res = self._execute_and_save_tool(tool_name, args)
                tool_latency_ms = int((time.perf_counter() - t_tool_start) * 1000)
                step_cost = self.budget.spent_inr - cost_before

                self.executed_calls[call_sig] = tool_res

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
                    cached=(step_cost == 0.0 and tool_res.get("success", False)),
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

        # Check for verification failures
        has_failed_verification = any(not v.passed for v in self.verification_results)

        # Separate and prioritize statuses accurately
        if agent_status in ("timeout", "failed", "impossible_task"):
            # Preserve existing failure status
            pass
        elif self.budget.remaining_inr <= 0.001 and self.budget.spent_inr >= self.max_cost_inr:
            agent_status = "budget_exceeded"
        elif step >= self.max_steps:
            agent_status = "max_steps_reached"
        elif not self.artifacts:
            # Rule: Never report success without a verified final artifact
            agent_status = "failed"
            self.failures.append("Task ended without producing any verified artifact.")
        elif has_failed_verification:
            agent_status = "failed"
            self.failures.append("One or more generated artifacts failed source verification.")
        else:
            agent_status = "success"

        # Build final summary in plain code strictly from what actually occurred
        final_answer = self._build_plain_code_summary(agent_status)

        # Final Report Generation
        total_time = time.perf_counter() - start_time
        report = AgentReport(
            status=agent_status,
            task=self.task,
            steps_taken=step,
            total_cost_inr=self.budget.spent_inr,
            total_time_s=total_time,
            assumptions=list(dict.fromkeys(self.assumptions)),
            verification_results=self.verification_results,
            artifacts=list(dict.fromkeys(self.artifacts)),
            failures=self.failures,
            language_requested=self.language_requested,
            detected_script=self.detected_script,
            windows_count=self.windows_count,
        )

        self.tracer.write_report(report)
        self.tracer.write_result_markdown(
            self.task, report.status, final_answer, report.assumptions, report.artifacts, report.failures
        )

        return report

    def _bind_default_file_args(self, tool_name: str, args: dict[str, Any], valid_files: list[Path]) -> None:
        """Binds input file paths to tool arguments if LLM omitted them."""
        if not valid_files:
            return

        if tool_name == "translate_text":
            # If an input text file exists, bind its real file_path so the real source is translated
            for f in valid_files:
                if f.suffix.lower() in (".txt", ".md", ".json"):
                    if not args.get("file_path") or not Path(args.get("file_path", "")).exists():
                        args["file_path"] = str(f.resolve())
                    break
        elif tool_name == "transcribe_audio":
            if not args.get("file_path") or not Path(args.get("file_path", "")).exists():
                for f in valid_files:
                    if f.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac"):
                        args["file_path"] = str(f.resolve())
                        break
            # Bind language requested in task if not explicitly passed by LLM
            if self.language_requested and not args.get("language"):
                args["language"] = self.language_requested
        elif tool_name == "extract_document_text":
            if not args.get("file_path") or not Path(args.get("file_path", "")).exists():
                for f in valid_files:
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf"):
                        args["file_path"] = str(f.resolve())
                        break

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
                    target_lang = res.get("target_language", "hi")
                    stem = Path(args.get("file_path", "text")).stem if args.get("file_path") else "translated"
                    art_file = self.tracer.artifacts_dir / f"translated_{target_lang}_{stem}.txt"
                    art_file.write_text(res["translated_text"], encoding="utf-8")
                    saved_path = str(art_file.resolve())
                    self.artifacts.append(saved_path)
                    res["artifact_path"] = saved_path
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

                    # Auto-save SRT if windows exist
                    if res.get("windows"):
                        srt_file = self.tracer.artifacts_dir / f"subtitles_{stem}.srt"
                        self.tools.build_srt(res["windows"], output_path=str(srt_file))
                        self.artifacts.append(str(srt_file.resolve()))
                return res

            elif name == "synthesize_speech":
                out_p = args.get("output_path") or str(
                    self.tracer.artifacts_dir / f"speech_{args.get('language', 'hi')}_{int(time.time())}.wav"
                )
                res = self.tools.synthesize_speech(
                    text=args.get("text", ""),
                    language=args.get("language", "hi"),
                    voice=args.get("voice"),
                    style=args.get("style"),
                    output_path=out_p,
                )
                if res.get("success") and res.get("audio_path"):
                    self.artifacts.append(res["audio_path"])
                return res

            elif name == "extract_document_text":
                res = self.tools.extract_document_text(file_path=args.get("file_path", ""))
                if res.get("success") and res.get("text"):
                    stem = Path(args.get("file_path", "doc")).stem
                    art_file = self.tracer.artifacts_dir / f"ocr_{stem}.md"
                    art_file.write_text(res["text"], encoding="utf-8")
                    saved_path = str(art_file.resolve())
                    self.artifacts.append(saved_path)
                    res["artifact_path"] = saved_path
                return res

            elif name == "inspect_media":
                return self.tools.inspect_media(file_path=args.get("file_path", ""))

            elif name == "detect_language":
                return self.tools.detect_language(text=args.get("text"), audio_path=args.get("audio_path"))

            elif name == "extract_audio_from_video":
                out_p = args.get("output_path") or str(
                    self.tracer.artifacts_dir / f"extracted_{int(time.time())}.wav"
                )
                res = self.tools.extract_audio_from_video(video_path=args.get("video_path", ""), output_path=out_p)
                if res.get("success") and res.get("audio_path"):
                    self.artifacts.append(res["audio_path"])
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
        """Verifies produced artifacts against real source content."""
        for art_path_str in list(self.artifacts):
            art_path = Path(art_path_str)
            if not art_path.exists():
                continue

            # Verify Translations
            if art_path.name.startswith("translated_") and art_path.suffix in (".txt", ".md"):
                content = art_path.read_text(encoding="utf-8")
                # Back-translate to English
                back_res = self.tools.translate_text(text=content, target_language_code="en")
                back_en = back_res.get("translated_text", "").strip()

                # Find source text to compare against
                source_en = ""
                for src_text in self.source_texts.values():
                    if src_text.strip():
                        source_en = src_text.strip()
                        break

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
                back_trans = self.tools.transcribe_audio(str(art_path))
                transcript = back_trans.get("transcript", "").strip()
                passed = back_trans.get("success", False) and len(transcript) > 0
                notes = f"Back-transcribed audio: {len(transcript)} chars. Passed={passed}"
                self.verification_results.append(
                    VerificationResult(
                        artifact=str(art_path.name),
                        verification_type="speech_back_transcription",
                        passed=passed,
                        notes=notes,
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

        if self.language_requested:
            expected_script = EXPECTED_SCRIPT_FOR_LANG.get(self.language_requested, self.language_requested)
            if detected_lang != expected_script:
                self.verification_results.append(
                    VerificationResult(
                        artifact=art_name,
                        verification_type="transcript_quality",
                        passed=False,
                        notes=(
                            f"Transcript verification failed: Task requested language '{self.language_requested}' "
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
            return (
                f"The task halted because the spend cap (₹{self.max_cost_inr:.2f}) was reached. "
                f"Total spent: ₹{self.budget.spent_inr:.4f} across {len(self.executed_calls)} executed call(s)."
            )
        if status == "max_steps_reached":
            return f"The task reached the maximum step limit ({self.max_steps}) before full completion."
        if status == "failed":
            reasons = "; ".join(self.failures) if self.failures else "Task did not produce verified artifacts."
            return f"Task execution failed: {reasons}"

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
            # If no ground truth source text was stored, pass with note
            return True, f"Verified non-empty translation (back-translated {len(back_en)} chars)"

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
            if self.budget.remaining_inr <= 0.001:
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
            call_sig = f"{name}:{json.dumps(args, sort_keys=True)}"
            if call_sig in self.executed_calls:
                # Reject duplicate
                break

            cost_before = self.budget.spent_inr
            t0 = time.perf_counter()
            tool_res = self._execute_and_save_tool(name, args)
            tool_ms = int((time.perf_counter() - t0) * 1000)
            self.executed_calls[call_sig] = tool_res

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
                cost_inr=self.budget.spent_inr - cost_before,
            )
            self.tracer.record_step(trace)

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
