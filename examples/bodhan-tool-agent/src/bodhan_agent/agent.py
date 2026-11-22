"""Autonomous task agent loop: plan, act, verify, retry, stop.

Imports tools directly from bodhan_mcp and executes multi-step Indic language tasks.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bodhan_agent.llm import LLMClient, SYSTEM_PROMPT
from bodhan_agent.models import AgentReport, StepTrace, VerificationResult
from bodhan_agent.tracer import AgentTracer
from bodhan_mcp.cache import DiskCache
from bodhan_mcp.config import Settings, load_settings
from bodhan_mcp.lang_utils import is_language_supported
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
        )

        self.assumptions: list[str] = []
        self.artifacts: list[str] = []
        self.failures: list[str] = []
        self.verification_results: list[VerificationResult] = []

    def run(self) -> AgentReport:
        """Executes the full agent loop and writes all required outputs."""
        start_time = time.perf_counter()

        # --------------------------------------------------------------------
        # 1. Plain Code Pre-flight Checks (Zero LLM / Zero Paid Calls)
        # --------------------------------------------------------------------
        # A. Check for impossible unsupported languages in prompt
        for pat in UNSUPPORTED_LANGUAGE_PATTERNS:
            import re
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

        # B. Verify files exist and inspect them
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

        # C. Budget check
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
        # 2. Agent Orchestration Loop (Plan, Act, Verify, Retry, Stop)
        # --------------------------------------------------------------------
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # Compact user message with untrusted data isolation
        initial_user_msg = (
            f"User Task: {self.task}\n\n"
            f"Available Input Files (summarized metadata):\n"
            f"<untrusted_data files_metadata>\n"
            f"{json.dumps(file_summaries, ensure_ascii=False, indent=2)}\n"
            f"</untrusted_data>\n\n"
            f"Please formulate a step-by-step plan and call the appropriate tool. "
            f"Remember: pass only local file paths, keep context lean, and call finish_task when done."
        )
        messages.append({"role": "user", "content": initial_user_msg})

        step = 0
        final_answer = ""
        agent_status = "success"

        while step < self.max_steps:
            step += 1
            t_step_start = time.perf_counter()

            # Check budget before step
            if self.budget.remaining_inr <= 0.001:
                agent_status = "budget_exceeded"
                self.failures.append("Allocated cost budget was exhausted before full completion.")
                final_answer += "\nExecution stopped cleanly because budget was exhausted."
                break

            # LLM Call
            try:
                content, tool_calls = self.llm.call(messages)
            except Exception as e:
                agent_status = "failed"
                self.failures.append(f"LLM call failed: {str(e)}")
                final_answer = f"Agent failed due to LLM error: {str(e)}"
                break

            if not tool_calls:
                # LLM provided thoughts without tool calls; prompt to continue
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": "Please proceed with the next tool call or call finish_task."})
                continue

            # Process the first tool call
            tc = tool_calls[0]
            tool_name = tc.get("name", "")
            args = tc.get("arguments", {})

            # Finish Task Check
            if tool_name == "finish_task":
                final_answer = args.get("final_answer", content or "Task completed.")
                new_assumptions = args.get("assumptions", [])
                if isinstance(new_assumptions, list):
                    self.assumptions.extend(new_assumptions)
                new_artifacts = args.get("artifacts", [])
                if isinstance(new_artifacts, list):
                    for a in new_artifacts:
                        if a and a not in self.artifacts:
                            self.artifacts.append(a)

                trace = StepTrace(
                    step=step,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    plan="Finish task",
                    tool="finish_task",
                    arguments=args,
                    result_summary="Task marked complete by agent.",
                    latency_ms=int((time.perf_counter() - t_step_start) * 1000),
                    cost_inr=0.0,
                )
                self.tracer.record_step(trace)
                break

            # Execute tool call
            cost_before = self.budget.spent_inr
            tool_res = self._execute_tool(tool_name, args)
            step_cost = self.budget.spent_inr - cost_before

            step_latency_ms = int((time.perf_counter() - t_step_start) * 1000)

            # Record Trace
            trace = StepTrace(
                step=step,
                timestamp=datetime.now(timezone.utc).isoformat(),
                plan=content[:150] if content else f"Call {tool_name}",
                tool=tool_name,
                arguments=args,
                result_summary=self._summarize_result(tool_res),
                latency_ms=step_latency_ms,
                cost_inr=step_cost,
                cached=(step_cost == 0.0 and tool_res.get("success", False)),
            )
            self.tracer.record_step(trace)

            # Verification Loop
            if tool_res.get("success", False):
                self._verify_step(tool_name, args, tool_res)

            # Pass result back to LLM wrapped in <untrusted_data>
            # Keep payload lean: do not feed massive text or base64 into context
            lean_res = self._make_lean_result(tool_res)
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if tc.get("id"):
                assistant_msg["tool_calls"] = [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(args)},
                }]
            messages.append(assistant_msg)

            tool_msg = {
                "role": "tool" if tc.get("id") else "user",
                "content": f"<untrusted_data tool_result>\n{json.dumps(lean_res, ensure_ascii=False)}\n</untrusted_data>",
            }
            if tc.get("id"):
                tool_msg["tool_call_id"] = tc["id"]
            messages.append(tool_msg)

        if step >= self.max_steps and not final_answer:
            agent_status = "budget_exceeded"
            self.failures.append(f"Reached maximum steps limit ({self.max_steps}).")
            final_answer = "Task reached maximum step count before finish_task was called."

        # Compile final report
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
        )

        self.tracer.write_report(report)
        self.tracer.write_result_markdown(
            self.task, report.status, final_answer, report.assumptions, report.artifacts, report.failures
        )

        return report

    def _execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatches tool call to BodhanTools instance."""
        try:
            if name == "transcribe_audio":
                return self.tools.transcribe_audio(
                    file_path=args.get("file_path", ""),
                    language=args.get("language"),
                )
            elif name == "translate_text":
                return self.tools.translate_text(
                    text=args.get("text", ""),
                    target_language_code=args.get("target_language_code", "hi"),
                    target_script=args.get("target_script", "native"),
                )
            elif name == "synthesize_speech":
                # Ensure output goes to artifacts directory
                out_p = args.get("output_path")
                if not out_p:
                    out_p = str(self.tracer.artifacts_dir / f"speech_{int(time.time())}.wav")
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
                return self.tools.extract_document_text(file_path=args.get("file_path", ""))
            elif name == "inspect_media":
                return self.tools.inspect_media(file_path=args.get("file_path", ""))
            elif name == "detect_language":
                return self.tools.detect_language(text=args.get("text"), audio_path=args.get("audio_path"))
            elif name == "extract_audio_from_video":
                out_p = args.get("output_path") or str(self.tracer.artifacts_dir / f"extracted_{int(time.time())}.wav")
                res = self.tools.extract_audio_from_video(video_path=args.get("video_path", ""), output_path=out_p)
                if res.get("success") and res.get("audio_path"):
                    self.artifacts.append(res["audio_path"])
                return res
            elif name == "build_srt":
                out_p = args.get("output_path") or str(self.tracer.artifacts_dir / f"subtitles_{int(time.time())}.srt")
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
            else:
                return {
                    "success": False,
                    "error": f"Unknown tool: {name}",
                    "actionable_tip": "Call only available tools.",
                }
        except BudgetExceededError as be:
            return {"success": False, "error": str(be), "budget_exceeded": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _verify_step(self, tool_name: str, args: dict[str, Any], res: dict[str, Any]) -> None:
        """Runs automated verification on tool outputs."""
        if tool_name == "synthesize_speech" and res.get("audio_path"):
            audio_p = res["audio_path"]
            # Back-transcription check
            back_res = self.tools.transcribe_audio(audio_p, language=args.get("language"))
            passed = back_res.get("success", False) and bool(back_res.get("transcript", "").strip())
            notes = f"Back-transcription chars: {len(back_res.get('transcript', ''))}, Audio duration: {res.get('duration_s')}s"
            self.verification_results.append(
                VerificationResult(artifact=audio_p, verification_type="back_transcription", passed=passed, notes=notes)
            )

        elif tool_name == "translate_text" and res.get("translated_text"):
            trans_text = res["translated_text"]
            # Back-translation to English so LLM / agent can verify coverage in English
            target_lang = args.get("target_language_code", "")
            if target_lang != "en":
                back_trans = self.tools.translate_text(trans_text, target_language_code="en")
                passed = back_trans.get("success", False) and len(back_trans.get("translated_text", "")) > 0
                notes = f"Back-translated to English ({len(back_trans.get('translated_text', ''))} chars)"
                self.verification_results.append(
                    VerificationResult(artifact="translation", verification_type="back_translation", passed=passed, notes=notes)
                )

        elif tool_name == "extract_document_text":
            passed = bool(res.get("text", "").strip())
            notes = f"Pages processed: {res.get('pages_count', 0)}, Text length: {len(res.get('text', ''))} chars"
            self.verification_results.append(
                VerificationResult(artifact="document_ocr", verification_type="ocr_coverage", passed=passed, notes=notes)
            )

    @staticmethod
    def _summarize_result(res: dict[str, Any]) -> str:
        """Returns concise one-line summary for tracing."""
        if not res.get("success", False):
            return f"Failed: {res.get('error', 'unknown error')[:120]}"

        parts = []
        for k in ("transcript", "translated_text", "text", "audio_path", "srt_path", "file_name", "language_code"):
            if k in res:
                val = str(res[k])
                preview = (val[:60] + "...") if len(val) > 60 else val
                parts.append(f"{k}='{preview}'")
        if "cost_inr" in res:
            parts.append(f"cost=₹{res['cost_inr']:.4f}")
        return ", ".join(parts) if parts else "Success"

    @staticmethod
    def _make_lean_result(res: dict[str, Any]) -> dict[str, Any]:
        """Trims verbose payloads to keep prompt lean for local 7B models."""
        lean = dict(res)
        if "text" in lean and len(lean["text"]) > 600:
            lean["text_preview"] = lean["text"][:300] + "... [truncated]"
            lean["text_length"] = len(lean["text"])
            del lean["text"]
        if "transcript" in lean and len(lean["transcript"]) > 600:
            lean["transcript_preview"] = lean["transcript"][:300] + "... [truncated]"
            lean["transcript_length"] = len(lean["transcript"])
            del lean["transcript"]
        if "windows" in lean and len(lean["windows"]) > 3:
            lean["windows_summary"] = f"{len(lean['windows'])} windows processed"
            lean["windows_sample"] = lean["windows"][:2]
            del lean["windows"]
        return lean
