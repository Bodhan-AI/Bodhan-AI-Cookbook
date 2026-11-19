from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bodhan_agent.llm import AgentLLMClient
from bodhan_agent.tracer import RunTracer
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import BodhanConfig
from bodhan_mcp.cost import BudgetExceededError, CostTracker
from bodhan_mcp.languages import (
    LANGUAGE_ALIASES,
    SUPPORTED_LANGUAGES,
    detect_unsupported_language_in_task,
    normalize_language_code,
)
from bodhan_mcp.security import ArtifactProvenanceRegistry
from bodhan_mcp.server import BodhanMCPServer
from bodhan_mcp.tools import BodhanToolSuite


WORD_NUMBERS: Dict[str, float] = {
    "half": 0.5,
    "one": 1.0,
    "a": 1.0,
    "an": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "ten": 10.0,
}


@dataclass
class TaskPlan:
    raw_task: str
    impossible_reason: Optional[str] = None
    filter_language: Optional[str] = None
    target_language: Optional[str] = None
    source_language_hint: Optional[str] = None
    needs_ocr: bool = False
    needs_transcription: bool = False
    needs_summary: bool = False
    needs_translation: bool = False
    needs_audio: bool = False
    needs_subtitles: bool = False
    needs_language_identification: bool = False
    requested_gender: str = "female"
    requested_voice: Optional[str] = None
    requested_audio_ext: str = "mp3"
    requested_subtitle_ext: str = "srt"
    target_duration_sec: Optional[float] = None
    assumptions: List[str] = field(default_factory=list)


class TaskAnalyzer:
    @staticmethod
    def parse_duration_seconds(task_text: str) -> Optional[float]:
        lower = task_text.lower()
        m_min = re.search(
            r"\b(\d+(?:\.\d+)?|half|one|two|three|four|five|ten)[\s\-]*(?:minute|min)s?\b",
            lower,
        )
        if m_min:
            val_str = m_min.group(1)
            mins = float(WORD_NUMBERS.get(val_str, val_str)) if val_str in WORD_NUMBERS else float(val_str)
            return round(mins * 60.0, 1)

        m_sec = re.search(r"\b(\d+(?:\.\d+)?)[\s\-]*(?:second|sec)s?\b", lower)
        if m_sec:
            return float(m_sec.group(1))
        return None

    @staticmethod
    def find_mentioned_languages(task_text: str) -> List[Tuple[int, str]]:
        lower = task_text.lower()
        found: List[Tuple[int, str]] = []
        seen_spans: List[Tuple[int, int]] = []

        candidates: List[Tuple[str, str]] = []
        for alias, code in LANGUAGE_ALIASES.items():
            if len(alias) >= 4:
                candidates.append((alias, code))
        for code, info in SUPPORTED_LANGUAGES.items():
            candidates.append((info.name.lower(), code))

        candidates.sort(key=lambda item: len(item[0]), reverse=True)

        for name_token, code in candidates:
            for match in re.finditer(rf"\b{re.escape(name_token)}\b", lower):
                s, e = match.span()
                if any(not (e <= es or s >= ee) for es, ee in seen_spans):
                    continue
                seen_spans.append((s, e))
                found.append((s, code))

        found.sort(key=lambda x: x[0])
        deduped: List[Tuple[int, str]] = []
        for pos, code in found:
            if not deduped or deduped[-1][1] != code:
                deduped.append((pos, code))
        return deduped

    @classmethod
    def analyze(cls, task: str, inspected_files: List[Dict[str, Any]]) -> TaskPlan:
        plan = TaskPlan(raw_task=task)
        lower = task.strip().lower()

        if not lower:
            plan.impossible_reason = "Task description is empty. Please provide a valid instruction in --task."
            return plan

        unsupported = detect_unsupported_language_in_task(task)
        if unsupported:
            _, lang_name = unsupported
            plan.impossible_reason = (
                f"Unsupported language '{lang_name}' requested in task. "
                "Bodhan AI models only support English (en) and the 22 scheduled Indian languages "
                "(such as Hindi, Tamil, Bengali, Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Odia, Assamese, Urdu). "
                "No fabricated output was produced."
            )
            return plan

        if re.search(r"\b(clone|cloning|imitate\s+.*?\s*voice|exact\s+voice\s+of)\b", lower):
            plan.impossible_reason = (
                "Voice cloning is not supported. Bodhan's Indic-Speak model synthesizes speech using "
                "a fixed library of pre-trained speakers and cannot clone or imitate custom speaker voices."
            )
            return plan

        if re.search(
            r"\b(generate\s+a?\s*video|draw\s+an?\s*image|create\s+a?\s*3d\s+animation|compile\s+c\+\+)\b", lower
        ):
            plan.impossible_reason = (
                "Task requests a capability outside Bodhan AI's four language models "
                "(Speech-to-Text, Translation, Speech Synthesis, and Document OCR)."
            )
            return plan

        non_empty_files = [f for f in inspected_files if f.get("media_type") != "empty" and f.get("size_bytes", 0) > 0]
        if not non_empty_files:
            plan.impossible_reason = "All provided input files are missing or empty (0 bytes). Cannot complete task without valid input media."
            return plan

        mentioned = cls.find_mentioned_languages(task)
        media_types = {f.get("media_type") for f in non_empty_files}

        filter_match = re.search(
            r"(?:which|find|filter|identify)[^?.!]*?\b(?:are\s+in|is\s+in|in)\s+([a-zA-Z]+)",
            lower,
        )
        if filter_match:
            cand_word = filter_match.group(1).lower()
            norm_f, err_f = normalize_language_code(cand_word)
            if not err_f and norm_f and norm_f != "auto":
                plan.filter_language = norm_f
                plan.needs_language_identification = True

        target_match = re.search(
            r"\b(?:into|to)\s+([a-zA-Z]+)\b|\b(?:in)\s+([a-zA-Z]+)\s+(?:with|using|audio|voice|subtitles|markdown)\b|\b([a-zA-Z]+)\s+(?:audio|summary|subtitles|captions|translation|mp3|wav)\b",
            lower,
        )
        if target_match:
            for grp in target_match.groups():
                if grp:
                    norm_t, err_t = normalize_language_code(grp)
                    if not err_t and norm_t and norm_t != "auto":
                        plan.target_language = norm_t
                        break

        if not plan.target_language and mentioned:
            if plan.filter_language and len(mentioned) >= 2:
                for _pos, c in mentioned:
                    if c != plan.filter_language:
                        plan.target_language = c
                        break
            elif not plan.filter_language:
                plan.target_language = mentioned[-1][1]
                if len(mentioned) >= 2:
                    plan.source_language_hint = mentioned[0][1]

        if (
            "pdf" in media_types
            or "image" in media_types
            or re.search(r"\b(ocr|scan|scanned|worksheet|page|chapter|document|image|photo)\b", lower)
        ):
            if "pdf" in media_types or "image" in media_types:
                plan.needs_ocr = True

        if (
            "audio" in media_types
            or "video" in media_types
            or re.search(r"\b(transcribe|voice\s*note|audio\s*clip|clips|speech\s*to\s*text|listen)\b", lower)
        ):
            if "audio" in media_types or "video" in media_types:
                plan.needs_transcription = True

        if re.search(r"\b(summary|summarize|summarise|synopsis|overview|brief)\b", lower):
            plan.needs_summary = True

        if re.search(r"\b(subtitle|subtitles|srt|vtt|captions)\b", lower):
            plan.needs_subtitles = True
            plan.requested_subtitle_ext = "vtt" if "vtt" in lower else "srt"

        if re.search(r"\b(audio|read\s+.*?aloud|speak|narrate|narration|voice|mp3|wav|tts|aloud)\b", lower):
            if re.search(r"\b(audio\s+summary|audio\s+file|read\s+.*?aloud|aloud|narrate|speak|voice|mp3)\b", lower):
                plan.needs_audio = True

        if re.search(r"\b(translate|translation|into\s+[a-z]+)\b", lower) or (
            plan.target_language is not None
            and (plan.needs_ocr or plan.needs_transcription or plan.needs_summary or plan.needs_subtitles)
        ):
            plan.needs_translation = True

        if re.search(r"\b(which\s+of|identify\s+the\s+language|what\s+language|detect\s+language)\b", lower):
            plan.needs_language_identification = True

        if re.search(r"\b(male|man|boy)\s+voice\b|\bwith\s+a\s+male\b", lower):
            plan.requested_gender = "male"
        elif re.search(r"\b(female|woman|girl)\s+voice\b|\bwith\s+a\s+female\b", lower):
            plan.requested_gender = "female"
        else:
            if plan.needs_audio:
                plan.assumptions.append(
                    "Voice gender was not explicitly specified; defaulted to 'female' voice from the Indic-Speak library."
                )

        if ".wav" in lower or " wav " in lower:
            plan.requested_audio_ext = "wav"
        else:
            plan.requested_audio_ext = "mp3"

        plan.target_duration_sec = cls.parse_duration_seconds(task)

        if not plan.target_language:
            if plan.needs_translation or plan.needs_subtitles:
                plan.target_language = "en"
                plan.assumptions.append(
                    "Target language was not explicitly specified in the task; assumed English ('en') as the default target language."
                )
            elif plan.needs_audio:
                first_lang = next(
                    (
                        str(f.get("detected_language"))
                        for f in non_empty_files
                        if f.get("detected_language") not in (None, "unknown")
                    ),
                    "hi",
                )
                plan.target_language = first_lang
                plan.assumptions.append(
                    f"Target spoken language was not explicitly specified; assumed '{plan.target_language}' based on input content."
                )

        return plan


class BodhanTaskAgent:
    def __init__(
        self,
        config: Optional[BodhanConfig] = None,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        out_dir: Path = Path("./out"),
        resume: bool = False,
        bodhan_client: Optional[BodhanClient] = None,
        llm_client: Optional[AgentLLMClient] = None,
    ) -> None:
        self.config = config or BodhanConfig.from_env()
        self.out_dir = Path(out_dir)
        self.artifacts_dir = self.out_dir / "artifacts"
        self.resume = resume

        self.cost_tracker = CostTracker(max_cost_inr=max_cost_inr, max_steps=max_steps)
        self.provenance = ArtifactProvenanceRegistry()
        self.client = bodhan_client or BodhanClient(config=self.config, cost_tracker=self.cost_tracker)
        self.client.cost_tracker = self.cost_tracker

        self.tool_suite = BodhanToolSuite(
            client=self.client,
            artifacts_dir=self.artifacts_dir,
            provenance=self.provenance,
        )
        self.mcp_server = BodhanMCPServer(tool_suite=self.tool_suite)
        self.llm = llm_client or AgentLLMClient(config=self.config)
        self.tracer = RunTracer(out_dir=self.out_dir, resume=resume)

        self.artifacts_produced: List[str] = []
        self.verifications: List[Dict[str, Any]] = []
        self.security_alerts: List[str] = []
        self.assumptions: List[str] = []

    def _call_tool_traced(
        self,
        plan_reason: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        step_num = self.cost_tracker.increment_step()
        cost_before = self.cost_tracker.total_cost_inr
        t0 = time.perf_counter()
        sys.stderr.write(f"[Step {step_num}] {tool_name}: {plan_reason}\n")
        sys.stderr.flush()

        call_args = dict(arguments)
        res = self.mcp_server.call_tool(tool_name, call_args)

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        cost_after = self.cost_tracker.total_cost_inr
        step_cost = round(max(0.0, cost_after - cost_before), 6)
        if step_cost == 0.0 and "cost_inr" in res:
            step_cost = float(res.get("cost_inr", 0.0))

        sys.stderr.write(
            f"[Step {step_num}] -> status={res.get('status', 'ok')} ({latency_ms:.1f}ms, cost=₹{step_cost:.4f})\n"
        )
        sys.stderr.flush()

        self.tracer.record_step(
            step=step_num,
            plan=plan_reason,
            tool=tool_name,
            arguments=arguments,
            result=res,
            latency_ms=latency_ms,
            cost_inr=step_cost,
            cumulative_cost_inr=self.cost_tracker.total_cost_inr,
        )

        if res.get("security_warning"):
            self.security_alerts.append(str(res["security_warning"]))
        if isinstance(res.get("files"), list):
            for f_info in res["files"]:
                if f_info.get("security_warning"):
                    self.security_alerts.append(str(f_info["security_warning"]))

        if res.get("status") == "ok" and res.get("output_path"):
            out_p = str(res["output_path"])
            if out_p not in self.artifacts_produced:
                self.artifacts_produced.append(out_p)

        if res.get("error_code") == "BUDGET_EXCEEDED":
            raise BudgetExceededError(
                message=str(res.get("message", "Budget exceeded")),
                current_cost_inr=self.cost_tracker.total_cost_inr,
                max_cost_inr=self.cost_tracker.max_cost_inr or 0.0,
                budget_type="cost_inr",
            )

        return res

    def run(self, task: str, files_path: str) -> Dict[str, Any]:
        run_t0 = time.perf_counter()

        if self.cost_tracker.max_cost_inr is not None and self.cost_tracker.max_cost_inr <= 0:
            total_ms = round((time.perf_counter() - run_t0) * 1000.0, 2)
            return self.tracer.write_final_outputs(
                task=task,
                input_files_path=files_path,
                status="budget_exceeded",
                final_answer="Run stopped immediately because `--max-cost-inr` was set to 0 or less.",
                assumptions=self.assumptions,
                artifacts=[],
                verifications=[],
                cost_summary=self.cost_tracker.summary(),
                total_latency_ms=total_ms,
                failure_reason="Cost budget (--max-cost-inr) is <= 0 INR.",
            )

        try:
            inspect_res = self._call_tool_traced(
                plan_reason="Inspect input files to determine media types, durations, page counts, scripts, and security status.",
                tool_name="inspect_media_and_files",
                arguments={"path": files_path},
            )

            if inspect_res.get("status") == "error":
                total_ms = round((time.perf_counter() - run_t0) * 1000.0, 2)
                err_msg = str(inspect_res.get("message", "Input files could not be inspected."))
                return self.tracer.write_final_outputs(
                    task=task,
                    input_files_path=files_path,
                    status="impossible_task",
                    final_answer=f"Task cannot be completed because the input files are missing or invalid: {err_msg}",
                    assumptions=self.assumptions,
                    artifacts=[],
                    verifications=[],
                    cost_summary=self.cost_tracker.summary(),
                    total_latency_ms=total_ms,
                    failure_reason=err_msg,
                    security_alerts=self.security_alerts,
                )

            inspected_files: List[Dict[str, Any]] = inspect_res.get("files", [])
            plan = TaskAnalyzer.analyze(task, inspected_files)
            if not plan.impossible_reason and self.llm.is_configured():
                llm_plan = self.llm.plan_task_with_llm(task, inspected_files)
                if isinstance(llm_plan, dict):
                    if not plan.target_language and llm_plan.get("target_language"):
                        norm_l, err_l = normalize_language_code(str(llm_plan["target_language"]))
                        if not err_l and norm_l and norm_l != "auto":
                            plan.target_language = norm_l
                    if not plan.filter_language and llm_plan.get("filter_language"):
                        norm_f, err_f = normalize_language_code(str(llm_plan["filter_language"]))
                        if not err_f and norm_f and norm_f != "auto":
                            plan.filter_language = norm_f
                    if isinstance(llm_plan.get("needs_summary"), bool):
                        plan.needs_summary = plan.needs_summary or bool(llm_plan["needs_summary"])
                    if isinstance(llm_plan.get("needs_translation"), bool):
                        plan.needs_translation = plan.needs_translation or bool(llm_plan["needs_translation"])
                    if isinstance(llm_plan.get("needs_audio"), bool):
                        plan.needs_audio = plan.needs_audio or bool(llm_plan["needs_audio"])
                    if isinstance(llm_plan.get("needs_subtitles"), bool):
                        plan.needs_subtitles = plan.needs_subtitles or bool(llm_plan["needs_subtitles"])
                    if llm_plan.get("requested_gender") in ("female", "male"):
                        plan.requested_gender = str(llm_plan["requested_gender"])
                    if not plan.target_duration_sec and isinstance(llm_plan.get("target_duration_sec"), (int, float)):
                        plan.target_duration_sec = float(llm_plan["target_duration_sec"])
                    if llm_plan.get("assumption"):
                        plan.assumptions.append(str(llm_plan["assumption"]))
            self.assumptions.extend(plan.assumptions)

            if plan.impossible_reason:
                unsupported = detect_unsupported_language_in_task(task)
                if unsupported:
                    bad_code, _ = unsupported
                    self._call_tool_traced(
                        plan_reason=f"Validate language support for requested language '{bad_code}' against Bodhan models.",
                        tool_name="translate_text",
                        arguments={"target_language": bad_code, "text": "Language support check"},
                    )
                total_ms = round((time.perf_counter() - run_t0) * 1000.0, 2)
                return self.tracer.write_final_outputs(
                    task=task,
                    input_files_path=files_path,
                    status="impossible_task",
                    final_answer=plan.impossible_reason,
                    assumptions=self.assumptions,
                    artifacts=[],
                    verifications=[],
                    cost_summary=self.cost_tracker.summary(),
                    total_latency_ms=total_ms,
                    failure_reason=plan.impossible_reason,
                    security_alerts=self.security_alerts,
                )

            final_answer = self._execute_planned_workflow(plan, inspected_files)

            self._run_verification_gate(plan)

            total_ms = round((time.perf_counter() - run_t0) * 1000.0, 2)
            return self.tracer.write_final_outputs(
                task=task,
                input_files_path=files_path,
                status="completed",
                final_answer=final_answer,
                assumptions=self.assumptions,
                artifacts=self.artifacts_produced,
                verifications=self.verifications,
                cost_summary=self.cost_tracker.summary(),
                total_latency_ms=total_ms,
                security_alerts=self.security_alerts,
            )

        except BudgetExceededError as exc:
            total_ms = round((time.perf_counter() - run_t0) * 1000.0, 2)
            partial_msg = (
                f"Stopped cleanly due to budget limit: {exc} "
                f"Completed {len(self.artifacts_produced)} artifact(s) before budget was reached."
            )
            return self.tracer.write_final_outputs(
                task=task,
                input_files_path=files_path,
                status="budget_exceeded",
                final_answer=partial_msg,
                assumptions=self.assumptions,
                artifacts=self.artifacts_produced,
                verifications=self.verifications,
                cost_summary=self.cost_tracker.summary(),
                total_latency_ms=total_ms,
                failure_reason=str(exc),
                security_alerts=self.security_alerts,
            )
        except Exception as exc:
            total_ms = round((time.perf_counter() - run_t0) * 1000.0, 2)
            return self.tracer.write_final_outputs(
                task=task,
                input_files_path=files_path,
                status="failed",
                final_answer=f"Task execution encountered an unrecoverable error: {exc}",
                assumptions=self.assumptions,
                artifacts=self.artifacts_produced,
                verifications=self.verifications,
                cost_summary=self.cost_tracker.summary(),
                total_latency_ms=total_ms,
                failure_reason=str(exc),
                security_alerts=self.security_alerts,
            )

    def _execute_planned_workflow(
        self,
        plan: TaskPlan,
        inspected_files: List[Dict[str, Any]],
    ) -> str:
        audio_video_files = [f for f in inspected_files if f.get("media_type") in ("audio", "video")]
        doc_image_files = [f for f in inspected_files if f.get("media_type") in ("pdf", "image")]
        text_files = [f for f in inspected_files if f.get("media_type") == "text"]

        extracted_texts: List[Dict[str, Any]] = []
        answer_sections: List[str] = []

        if doc_image_files:
            all_images_same_dir = (
                len(doc_image_files) > 1
                and all(f.get("media_type") == "image" for f in doc_image_files)
                and len({str(Path(str(f["file_path"])).parent) for f in doc_image_files}) == 1
            )
            if all_images_same_dir:
                parent_dir = str(Path(str(doc_image_files[0]["file_path"])).parent)
                stem = Path(parent_dir).name or "document"
                out_md = str((self.artifacts_dir / f"{stem}_ocr.md").resolve())
                ocr_res = self._call_tool_traced(
                    plan_reason=f"Extract text and layout structure across {len(doc_image_files)} scanned page images in '{stem}' using Indic-OCR.",
                    tool_name="extract_document_text",
                    arguments={"file_path": parent_dir, "output_path": out_md},
                )
                if ocr_res.get("status") != "ok":
                    raise RuntimeError(ocr_res.get("message", "OCR extraction failed"))
                extracted_texts.append(
                    {
                        "path": str(ocr_res["output_path"]),
                        "lang": str(ocr_res.get("detected_language", "en")),
                        "lang_name": str(ocr_res.get("detected_language_name", "English")),
                        "source_file": stem,
                        "segments_path": None,
                    }
                )
            else:
                for doc in doc_image_files:
                    f_path = str(doc["file_path"])
                    stem = Path(f_path).stem
                    out_md = str((self.artifacts_dir / f"{stem}_ocr.md").resolve())
                    ocr_res = self._call_tool_traced(
                        plan_reason=f"Extract text and layout structure from '{Path(f_path).name}' using Indic-OCR.",
                        tool_name="extract_document_text",
                        arguments={"file_path": f_path, "output_path": out_md},
                    )
                    if ocr_res.get("status") != "ok":
                        raise RuntimeError(ocr_res.get("message", "OCR extraction failed"))
                    extracted_texts.append(
                        {
                            "path": str(ocr_res["output_path"]),
                            "lang": str(ocr_res.get("detected_language", "en")),
                            "lang_name": str(ocr_res.get("detected_language_name", "English")),
                            "source_file": Path(f_path).name,
                            "segments_path": None,
                        }
                    )

        if audio_video_files:
            asr_lang_hint = plan.source_language_hint or ("auto" if plan.filter_language else "auto")
            if len(audio_video_files) > 1:
                if (
                    self.cost_tracker.max_steps is not None
                    and self.cost_tracker.steps_taken + len(audio_video_files) > self.cost_tracker.max_steps
                ):
                    clip_results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
                    for clip in audio_video_files:
                        c_path = str(clip["file_path"])
                        c_stem = Path(c_path).stem
                        out_txt = str((self.artifacts_dir / f"{c_stem}_transcript.txt").resolve())
                        t_res = self._call_tool_traced(
                            plan_reason=f"Transcribe audio clip '{Path(c_path).name}' and detect its spoken language using Indic-Transcribe.",
                            tool_name="transcribe_audio",
                            arguments={
                                "file_path": c_path,
                                "output_path": out_txt,
                                "language": "auto",
                                "output_format": "txt",
                            },
                        )
                        if t_res.get("status") != "ok":
                            raise RuntimeError(t_res.get("message", f"Transcription failed on {c_path}"))
                        clip_results.append((clip, t_res))
                else:

                    def _transcribe_single(clip_item: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], float]:
                        c_p = str(clip_item["file_path"])
                        c_st = Path(c_p).stem
                        out_t = str((self.artifacts_dir / f"{c_st}_transcript.txt").resolve())
                        t_start = time.perf_counter()
                        res_d = self.mcp_server.call_tool(
                            "transcribe_audio",
                            {
                                "file_path": c_p,
                                "output_path": out_t,
                                "language": "auto",
                                "output_format": "txt",
                            },
                        )
                        el_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
                        return clip_item, res_d, el_ms

                    max_workers = min(len(audio_video_files), self.config.max_concurrency)
                    ordered_futures = []
                    with ThreadPoolExecutor(max_workers=max_workers) as pool:
                        for clip in audio_video_files:
                            ordered_futures.append(pool.submit(_transcribe_single, clip))

                    clip_results = []
                    for fut in ordered_futures:
                        clip, t_res, el_ms = fut.result()
                        c_path = str(clip["file_path"])
                        c_stem = Path(c_path).stem
                        out_txt = str((self.artifacts_dir / f"{c_stem}_transcript.txt").resolve())
                        step_num = self.cost_tracker.increment_step()
                        step_cost = float(t_res.get("cost_inr", 0.0))
                        self.tracer.record_step(
                            step=step_num,
                            plan=f"[Parallel] Transcribe audio clip '{Path(c_path).name}' and detect its spoken language using Indic-Transcribe.",
                            tool="transcribe_audio",
                            arguments={
                                "file_path": c_path,
                                "output_path": out_txt,
                                "language": "auto",
                                "output_format": "txt",
                            },
                            result=t_res,
                            latency_ms=el_ms,
                            cost_inr=step_cost,
                            cumulative_cost_inr=self.cost_tracker.total_cost_inr,
                        )
                        if t_res.get("error_code") == "BUDGET_EXCEEDED":
                            raise BudgetExceededError(
                                message=str(t_res.get("message", "Budget exceeded")),
                                current_cost_inr=self.cost_tracker.total_cost_inr,
                                max_cost_inr=self.cost_tracker.max_cost_inr or 0.0,
                            )
                        if t_res.get("status") != "ok":
                            raise RuntimeError(t_res.get("message", f"Transcription failed on {c_path}"))
                        if t_res.get("output_path") and str(t_res["output_path"]) not in self.artifacts_produced:
                            self.artifacts_produced.append(str(t_res["output_path"]))
                        clip_results.append((clip, t_res))

                if plan.filter_language:
                    filter_lang_name = SUPPORTED_LANGUAGES.get(plan.filter_language, SUPPORTED_LANGUAGES["en"]).name
                    matching_clips: List[Dict[str, Any]] = []
                    non_matching_clips: List[str] = []

                    for clip, t_res in clip_results:
                        det_lang = str(t_res.get("detected_language", "unknown"))
                        c_name = Path(str(clip["file_path"])).name
                        if det_lang == plan.filter_language:
                            matching_clips.append(
                                {
                                    "path": str(t_res["output_path"]),
                                    "lang": det_lang,
                                    "lang_name": str(t_res.get("detected_language_name", filter_lang_name)),
                                    "source_file": c_name,
                                    "segments_path": t_res.get("segments_path"),
                                }
                            )
                        else:
                            det_name = str(t_res.get("detected_language_name", det_lang))
                            non_matching_clips.append(f"`{c_name}` (detected: {det_name} / `{det_lang}`)")

                    extracted_texts.extend(matching_clips)
                    match_names = [f"`{m['source_file']}`" for m in matching_clips]
                    answer_sections.append(
                        f"### Language Identification Results ({filter_lang_name})\n"
                        f"- **Clips in {filter_lang_name} (`{plan.filter_language}`)**: {', '.join(match_names) if match_names else 'None'}\n"
                        f"- **Other Clips**: {', '.join(non_matching_clips) if non_matching_clips else 'None'}"
                    )
                else:
                    for clip, t_res in clip_results:
                        extracted_texts.append(
                            {
                                "path": str(t_res["output_path"]),
                                "lang": str(t_res.get("detected_language", "en")),
                                "lang_name": str(t_res.get("detected_language_name", "English")),
                                "source_file": Path(str(clip["file_path"])).name,
                                "segments_path": t_res.get("segments_path"),
                            }
                        )
            else:
                clip = audio_video_files[0]
                c_path = str(clip["file_path"])
                c_stem = Path(c_path).stem
                fmt = plan.requested_subtitle_ext if (plan.needs_subtitles and not plan.needs_translation) else "txt"
                out_f = str((self.artifacts_dir / f"{c_stem}_transcript.{fmt}").resolve())
                t_res = self._call_tool_traced(
                    plan_reason=f"Transcribe '{Path(c_path).name}' in <=28s chunks using Indic-Transcribe.",
                    tool_name="transcribe_audio",
                    arguments={
                        "file_path": c_path,
                        "output_path": out_f,
                        "language": asr_lang_hint,
                        "output_format": fmt,
                    },
                )
                if t_res.get("status") != "ok":
                    raise RuntimeError(t_res.get("message", "Audio transcription failed"))
                extracted_texts.append(
                    {
                        "path": str(t_res["output_path"]),
                        "lang": str(t_res.get("detected_language", "en")),
                        "lang_name": str(t_res.get("detected_language_name", "English")),
                        "source_file": Path(c_path).name,
                        "segments_path": t_res.get("segments_path"),
                    }
                )

        for tf in text_files:
            extracted_texts.append(
                {
                    "path": str(tf["file_path"]),
                    "lang": str(tf.get("detected_language", "en")),
                    "lang_name": str(tf.get("detected_language_name", "English")),
                    "source_file": str(tf["file_name"]),
                    "segments_path": None,
                }
            )

        if plan.needs_summary and extracted_texts:
            combined_en_parts: List[str] = []
            for item in extracted_texts:
                item_text = Path(item["path"]).read_text(encoding="utf-8", errors="replace").strip()
                if item["lang"] != "en":
                    en_pivot_path = str((self.artifacts_dir / f"{Path(item['path']).stem}_en.md").resolve())
                    tr_en = self._call_tool_traced(
                        plan_reason=f"Translate '{item['source_file']}' from {item['lang']} to English via Indic-Translate before English summarization.",
                        tool_name="translate_text",
                        arguments={
                            "input_path": item["path"],
                            "source_language": item["lang"],
                            "target_language": "en",
                            "output_path": en_pivot_path,
                        },
                    )
                    if tr_en.get("status") == "ok":
                        item_text = Path(tr_en["output_path"]).read_text(encoding="utf-8", errors="replace").strip()
                combined_en_parts.append(item_text)

            full_en_source = "\n\n".join(combined_en_parts)
            target_dur = plan.target_duration_sec or 120.0
            en_summary = self.llm.summarize_in_english(
                english_source_text=full_en_source,
                target_duration_sec=target_dur,
                task_instruction=plan.raw_task,
            )

            summary_en_path = self.artifacts_dir / "summary_en.md"
            summary_en_path.write_text(en_summary + "\n", encoding="utf-8")
            self.provenance.register_input_file(summary_en_path)
            if str(summary_en_path.resolve()) not in self.artifacts_produced:
                self.artifacts_produced.append(str(summary_en_path.resolve()))

            answer_sections.append(f"### English Summary\n\n{en_summary}")
            extracted_texts = [
                {
                    "path": str(summary_en_path.resolve()),
                    "lang": "en",
                    "lang_name": "English",
                    "source_file": "summary_en.md",
                    "segments_path": None,
                }
            ]

        translated_items: List[Dict[str, Any]] = []
        if plan.target_language and extracted_texts:
            tgt = plan.target_language
            for item in extracted_texts:
                src_lang = item["lang"]
                stem = Path(item["path"]).stem
                out_fmt = plan.requested_subtitle_ext if plan.needs_subtitles else "md"

                if src_lang == tgt and not (plan.needs_subtitles and not item["path"].endswith(f".{out_fmt}")):
                    translated_items.append({**item, "source_path_for_verify": None})
                    continue

                input_for_translate = (
                    item["segments_path"] if (plan.needs_subtitles and item.get("segments_path")) else item["path"]
                )
                out_trans_path = str((self.artifacts_dir / f"{stem}_{tgt}.{out_fmt}").resolve())
                tr_res = self._call_tool_traced(
                    plan_reason=(
                        f"Translate '{item['source_file']}' from '{src_lang}' into '{tgt}' "
                        f"({'subtitles' if plan.needs_subtitles else 'Markdown/text'}) using Indic-Translate."
                    ),
                    tool_name="translate_text",
                    arguments={
                        "input_path": input_for_translate,
                        "source_language": src_lang,
                        "target_language": tgt,
                        "output_path": out_trans_path,
                        "output_format": out_fmt,
                    },
                )
                if tr_res.get("status") != "ok":
                    raise RuntimeError(tr_res.get("message", "Translation failed"))

                trans_out_file = str(tr_res["output_path"])
                trans_text = Path(trans_out_file).read_text(encoding="utf-8", errors="replace").strip()
                lang_label = SUPPORTED_LANGUAGES.get(tgt, SUPPORTED_LANGUAGES["en"]).name
                answer_sections.append(
                    f"### {lang_label} (`{tgt}`) Output (`{Path(trans_out_file).name}`)\n\n"
                    f"{trans_text[:1500]}" + ("..." if len(trans_text) > 1500 else "")
                )
                translated_items.append(
                    {
                        "path": trans_out_file,
                        "lang": tgt,
                        "lang_name": lang_label,
                        "source_file": item["source_file"],
                        "source_path_for_verify": item["path"],
                    }
                )
        else:
            for item in extracted_texts:
                txt_body = Path(item["path"]).read_text(encoding="utf-8", errors="replace").strip()
                answer_sections.append(
                    f"### Extracted / Transcribed Text (`{item['source_file']}` — {item['lang_name']})\n\n"
                    f"{txt_body[:1500]}" + ("..." if len(txt_body) > 1500 else "")
                )
                translated_items.append({**item, "source_path_for_verify": None})

        if plan.needs_audio and translated_items:
            for t_item in translated_items:
                spk_lang = plan.target_language or t_item["lang"]
                stem = Path(t_item["path"]).stem
                out_audio = str((self.artifacts_dir / f"{stem}.{plan.requested_audio_ext}").resolve())
                tts_res = self._call_tool_traced(
                    plan_reason=(
                        f"Synthesize {spk_lang} speech from '{Path(t_item['path']).name}' "
                        f"with a {plan.requested_gender} voice using Indic-Speak."
                    ),
                    tool_name="synthesize_speech",
                    arguments={
                        "language": spk_lang,
                        "input_path": t_item["path"],
                        "output_path": out_audio,
                        "gender": plan.requested_gender,
                        "style": "narration",
                    },
                )
                if tts_res.get("status") != "ok":
                    raise RuntimeError(tts_res.get("message", "Speech synthesis failed"))

                t_item["audio_output_path"] = str(tts_res["output_path"])
                answer_sections.append(
                    f"### Synthesized Audio (`{Path(tts_res['output_path']).name}`)\n"
                    f"- **Model**: `indic-speak` *(AI-Generated Audio)*\n"
                    f"- **Language**: {tts_res.get('language_name')} (`{spk_lang}`)\n"
                    f"- **Voice**: {tts_res.get('voice')} ({tts_res.get('gender')})\n"
                    f"- **Duration**: {tts_res.get('duration_sec')} seconds"
                )

        self._items_to_verify = translated_items
        return "\n\n".join(answer_sections).strip() or "Task completed successfully."

    def _run_verification_gate(self, plan: TaskPlan) -> None:
        items: List[Dict[str, Any]] = getattr(self, "_items_to_verify", [])
        for item in items:
            src_verify = item.get("source_path_for_verify")
            if src_verify and item.get("path") and src_verify != item.get("path"):
                try:
                    v_tr = self._call_tool_traced(
                        plan_reason=f"Verify translation completeness, script, numbers, and formatting for '{Path(item['path']).name}'.",
                        tool_name="verify_translation_quality",
                        arguments={
                            "source_path": src_verify,
                            "translated_path": item["path"],
                            "target_language": item["lang"],
                            "run_back_translation": False,
                        },
                    )
                    self.verifications.append({**v_tr, "translated_path": item["path"]})
                except BudgetExceededError:
                    raise
                except Exception:
                    pass

            aud_path = item.get("audio_output_path")
            if aud_path:
                try:
                    v_aud = self._call_tool_traced(
                        plan_reason=f"Back-transcribe and verify audio quality for '{Path(aud_path).name}' using Indic-Transcribe.",
                        tool_name="verify_audio_quality",
                        arguments={
                            "audio_path": aud_path,
                            "expected_text_path": item.get("path"),
                            "language": item.get("lang", "auto"),
                            "target_duration_sec": plan.target_duration_sec,
                            "run_back_transcription": True,
                        },
                    )
                    if not v_aud.get("passed", True) and v_aud.get("checks", {}).get("repetition_detected"):
                        retry_tts = self._call_tool_traced(
                            plan_reason=f"Retry speech synthesis for '{Path(aud_path).name}' with neutral style after repetition detected.",
                            tool_name="synthesize_speech",
                            arguments={
                                "language": item.get("lang", "hi"),
                                "input_path": item.get("path"),
                                "output_path": aud_path,
                                "gender": plan.requested_gender,
                                "style": "neutral",
                            },
                        )
                        if retry_tts.get("status") == "ok":
                            v_aud = self._call_tool_traced(
                                plan_reason=f"Re-verify audio quality after retry for '{Path(aud_path).name}'.",
                                tool_name="verify_audio_quality",
                                arguments={
                                    "audio_path": aud_path,
                                    "expected_text_path": item.get("path"),
                                    "language": item.get("lang", "auto"),
                                    "target_duration_sec": plan.target_duration_sec,
                                    "run_back_transcription": True,
                                },
                            )
                    self.verifications.append(v_aud)
                except BudgetExceededError:
                    raise
                except Exception:
                    pass
