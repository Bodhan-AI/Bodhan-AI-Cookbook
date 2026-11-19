"""
Task Agent execution loop implementing Plan -> Act -> Verify -> Retry -> Stop.
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any

from .cache import ToolCache
from .tracer import AgentTracer, RunReport
from .llm import LLMClient
from .prompts import AGENT_SYSTEM_PROMPT

from bodhan_mcp.client import BodhanClient, BodhanAPIError
from bodhan_mcp.models import (
    TranscribeInput,
    TranslateInput,
    SpeakInput,
    OCRInput,
    InspectMediaInput,
    DetectLanguageInput,
)
from bodhan_mcp.tools.transcribe import run_transcribe
from bodhan_mcp.tools.translate import run_translate
from bodhan_mcp.tools.speak import run_speak
from bodhan_mcp.tools.ocr import run_ocr
from bodhan_mcp.tools.media import run_inspect_media, run_detect_language

logger = logging.getLogger(__name__)


class TaskAgent:
    """Autonomous Task Agent orchestrating Bodhan Indic models."""

    def __init__(
        self,
        task: str,
        files_path: Optional[str] = None,
        out_dir: str = "./out",
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
    ):
        self.task = task
        self.files_path = files_path
        self.out_dir = out_dir
        self.max_steps = max_steps
        self.max_cost_inr = max_cost_inr

        self.tracer = AgentTracer(out_dir=self.out_dir, task=self.task)
        self.cache = ToolCache()
        self.bodhan_client = BodhanClient()
        self.llm_client = LLMClient()

    def run(self) -> RunReport:
        """Run the autonomous agent loop until task completion or budget limit."""
        # 1. First check if task itself is inherently impossible (e.g. unsupported language like French)
        feasibility_check = self.llm_client.plan_next_step(
            messages=[
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": f"TASK: {self.task}"}
            ],
            task_text=self.task,
            input_files=[]
        )
        if feasibility_check.get("tool_name") == "fail_task_honestly":
            reason = feasibility_check["tool_args"].get("reason", "Task is impossible with available Indic models.")
            self.tracer.record_step(
                step_number=1,
                plan=feasibility_check.get("thought", "Check task feasibility"),
                tool="fail_task_honestly",
                arguments=feasibility_check.get("tool_args", {}),
                result_summary=reason,
                latency_ms=1.0,
                cost_inr=0.0,
            )
            return self.tracer.finish(
                status="IMPOSSIBLE_TASK",
                final_answer=f"Task aborted honestly: {reason}",
                assumptions=[],
                explanation=reason,
            )

        # 2. Check if specified input file path exists
        if self.files_path and not os.path.exists(self.files_path):
            reason = f"Required input file or directory not found: {self.files_path}"
            self.tracer.record_step(
                step_number=1,
                plan="Validate existence of specified input file",
                tool="fail_task_honestly",
                arguments={"files_path": self.files_path},
                result_summary=reason,
                latency_ms=1.0,
                cost_inr=0.0,
            )
            return self.tracer.finish(
                status="IMPOSSIBLE_TASK",
                final_answer=f"Task aborted honestly: {reason}",
                assumptions=[],
                explanation=reason,
            )

        # Discover input files
        discovered_files = self._discover_input_files()
        files_summary = "\n".join([f"- {f}" for f in discovered_files]) if discovered_files else "No files provided."

        # Construct initial conversation
        initial_user_prompt = (
            f"TASK: {self.task}\n\n"
            f"INPUT FILES:\n{files_summary}\n\n"
            f"ARTIFACTS DIRECTORY: {self.tracer.artifacts_dir}\n"
            f"BUDGET: Max steps = {self.max_steps}, Max Cost = ₹{self.max_cost_inr:.2f}\n\n"
            f"Execute the task step by step. Verify all generated artifacts before finishing."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": initial_user_prompt},
        ]

        step_count = 0
        status = "COMPLETED"
        final_answer = ""
        assumptions: List[str] = []
        explanation: Optional[str] = None

        while step_count < self.max_steps:
            step_count += 1

            # Budget check: Max Cost
            if self.tracer.total_cost_inr >= self.max_cost_inr:
                status = "BUDGET_EXCEEDED"
                explanation = f"Exceeded maximum cost budget of ₹{self.max_cost_inr:.2f} INR (current: ₹{self.tracer.total_cost_inr:.4f}). Stopping cleanly."
                final_answer = f"Execution stopped because budget limit was reached: {explanation}"
                break

            # LLM planning
            plan_res = self.llm_client.plan_next_step(
                messages,
                task_text=self.task,
                input_files=discovered_files,
            )
            thought = plan_res.get("thought", "")
            tool_name = plan_res.get("tool_name", "finish_task")
            tool_args = plan_res.get("tool_args", {})

            # Terminal actions
            if tool_name == "fail_task_honestly":
                status = "IMPOSSIBLE_TASK"
                reason = tool_args.get("reason", "Task cannot be completed with available tools/languages.")
                explanation = reason
                final_answer = f"Task aborted honestly: {reason}"
                self.tracer.record_step(
                    step_number=step_count,
                    plan=thought or "Recognized impossible task condition",
                    tool="fail_task_honestly",
                    arguments=tool_args,
                    result_summary=reason,
                    latency_ms=1.0,
                    cost_inr=0.0,
                )
                break

            if tool_name == "finish_task":
                final_answer = tool_args.get("final_answer", thought or "Task completed.")
                assumptions = tool_args.get("assumptions", [])
                for art in tool_args.get("artifact_paths", []):
                    self.tracer.register_artifact(art)
                self.tracer.record_step(
                    step_number=step_count,
                    plan=thought or "Conclude task execution",
                    tool="finish_task",
                    arguments=tool_args,
                    result_summary="Task marked as complete",
                    latency_ms=1.0,
                    cost_inr=0.0,
                )
                break

            # Execute tool call
            tool_output, cost_inr, latency_ms = self._execute_tool(tool_name, tool_args)

            # Record in tracer
            summary = tool_output[:300] + ("..." if len(tool_output) > 300 else "")
            self.tracer.record_step(
                step_number=step_count,
                plan=thought,
                tool=tool_name,
                arguments=tool_args,
                result_summary=summary,
                latency_ms=latency_ms,
                cost_inr=cost_inr,
            )

            # Append to messages for conversation continuity
            messages.append({"role": "assistant", "content": thought})
            messages.append({"role": "tool", "name": tool_name, "content": tool_output})

        else:
            # Loop ended by exceeding max_steps
            status = "BUDGET_EXCEEDED"
            explanation = f"Exceeded maximum steps allowed ({self.max_steps})."
            final_answer = f"Agent stopped after reaching maximum allowed steps ({self.max_steps})."

        # Conclude run
        report = self.tracer.finish(
            status=status,
            final_answer=final_answer,
            assumptions=assumptions,
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            explanation=explanation,
        )

        return report

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> (str, float, float):
        """Invoke Bodhan tool with caching and error handling."""
        cached = self.cache.get(tool_name, args)
        if cached:
            return cached["output"], 0.0, 1.0  # Cache hit is free and instantaneous

        cost_inr = 0.0
        latency_ms = 0.0
        output_str = ""

        try:
            if tool_name == "transcribe_audio":
                model_arg = TranscribeInput(**args)
                res = run_transcribe(model_arg, self.bodhan_client)
                cost_inr = res.metadata.cost_inr
                latency_ms = res.metadata.latency_ms
                # Quarantine extracted text against prompt injection
                quarantined_text = f"<untrusted_extracted_data source=\"{model_arg.audio_path}\">\n{res.text}\n</untrusted_extracted_data>"
                res_dict = res.model_dump()
                res_dict["text"] = quarantined_text
                output_str = json.dumps(res_dict, indent=2)

                # If this was a verification of synthesized audio
                if "speech_" in args.get("audio_path", ""):
                    self.tracer.add_verification(
                        artifact_name=os.path.basename(args["audio_path"]),
                        check_type="back_transcription",
                        passed=bool(res.text.strip()),
                        details=f"Back-transcribed text: '{res.text[:80]}...'",
                    )

            elif tool_name == "translate_text":
                model_arg = TranslateInput(**args)
                # Strip XML quarantine wrappers before sending text to translation model
                clean_text = args.get("text", "")
                if "<untrusted_extracted_data" in clean_text:
                    import re
                    match = re.search(r"<untrusted_extracted_data[^>]*>(.*?)</untrusted_extracted_data>", clean_text, re.DOTALL)
                    if match:
                        clean_text = match.group(1).strip()
                model_arg.text = clean_text

                res = run_translate(model_arg, self.bodhan_client)
                cost_inr = res.metadata.cost_inr
                latency_ms = res.metadata.latency_ms
                output_str = res.model_dump_json()

                # Translation coverage verification
                self.tracer.add_verification(
                    artifact_name=f"translation_{model_arg.target_lang}",
                    check_type="content_coverage",
                    passed=len(res.translated_text) > 0,
                    details=f"Generated {res.token_count} tokens in {model_arg.target_lang}.",
                )

            elif tool_name == "synthesize_speech":
                # Clean text of XML quarantine tags if present
                clean_text = args.get("text", "")
                if "<untrusted_extracted_data" in clean_text:
                    import re
                    match = re.search(r"<untrusted_extracted_data[^>]*>(.*?)</untrusted_extracted_data>", clean_text, re.DOTALL)
                    if match:
                        clean_text = match.group(1).strip()
                args["text"] = clean_text

                # Ensure output path is inside artifacts directory if not set
                if not args.get("output_audio_path"):
                    lang = args.get("language", "hi")
                    args["output_audio_path"] = os.path.join(
                        self.tracer.artifacts_dir,
                        f"speech_{lang}_{int(os.times().elapsed * 1000)}.wav"
                    )

                model_arg = SpeakInput(**args)
                res = run_speak(model_arg, self.bodhan_client, default_output_dir=self.tracer.artifacts_dir)
                cost_inr = res.metadata.cost_inr
                latency_ms = res.metadata.latency_ms
                output_str = res.model_dump_json()
                self.tracer.register_artifact(res.audio_path)

            elif tool_name == "extract_text_ocr":
                model_arg = OCRInput(**args)
                res = run_ocr(model_arg, self.bodhan_client)
                cost_inr = res.metadata.cost_inr
                latency_ms = res.metadata.latency_ms
                
                # Quarantine extracted OCR text against prompt injection
                quarantined_text = f"<untrusted_extracted_data source=\"{model_arg.file_path}\">\n{res.full_text}\n</untrusted_extracted_data>"
                res_dict = res.model_dump()
                res_dict["full_text"] = quarantined_text
                output_str = json.dumps(res_dict, indent=2)

                # Save extracted text to artifacts
                doc_name = os.path.splitext(os.path.basename(model_arg.file_path))[0]
                md_path = os.path.join(self.tracer.artifacts_dir, f"{doc_name}_extracted.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(res.full_text)
                self.tracer.register_artifact(md_path)

            elif tool_name == "inspect_media":
                model_arg = InspectMediaInput(**args)
                res = run_inspect_media(model_arg)
                output_str = res.model_dump_json()

            elif tool_name == "detect_language":
                model_arg = DetectLanguageInput(**args)
                res = run_detect_language(model_arg, self.bodhan_client)
                output_str = res.model_dump_json()

            else:
                output_str = json.dumps({"error": f"Unknown tool: {tool_name}"})

        except BodhanAPIError as be:
            output_str = json.dumps({"error": True, "message": str(be), "details": be.details})
        except Exception as e:
            output_str = json.dumps({"error": True, "message": str(e)})

        # Cache the result
        self.cache.set(tool_name, args, {"output": output_str})
        return output_str, cost_inr, latency_ms

    def _discover_input_files(self) -> List[str]:
        if not self.files_path or not os.path.exists(self.files_path):
            return []

        if os.path.isfile(self.files_path):
            return [os.path.abspath(self.files_path)]

        found = []
        for root, _, files in os.walk(self.files_path):
            for file in files:
                found.append(os.path.abspath(os.path.join(root, file)))
        return found
