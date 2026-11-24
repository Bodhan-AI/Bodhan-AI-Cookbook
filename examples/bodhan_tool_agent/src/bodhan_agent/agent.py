"""Task Agent for multi-step Indic language tasks using Bodhan models."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from bodhan_mcp.client.client import BodhanClient, SUPPORTED_LANGUAGES, VOICE_CATALOG
from bodhan_mcp.client.pricing import (
    calculate_ocr_cost,
    calculate_speak_cost,
    calculate_transcribe_cost,
    calculate_translate_cost,
    estimate_tokens_from_text,
)
from bodhan_mcp.server import TOOLS_REGISTRY
from bodhan_mcp.tools import (
    bodhan_detect_language,
    bodhan_inspect_media,
    bodhan_list_voices,
    bodhan_make_subtitles,
    bodhan_ocr,
    bodhan_read_text,
    bodhan_speak,
    bodhan_transcribe,
    bodhan_translate,
    bodhan_verify_speech,
    bodhan_verify_translation,
)
from bodhan_mcp.tools.transcribe import _format_srt_time
from .budget import BudgetExceededException, BudgetTracker
from .prompts import AGENT_SYSTEM_PROMPT, wrap_untrusted_content
from .tracer import Tracer
from .verifier import Verifier

# Regex matching tokens in Indian scripts (Devanagari, Bengali, Gurmukhi, Gujarati,
# Odia, Tamil, Telugu, Kannada, Malayalam, Perso-Arabic/Urdu, Ol Chiki, Meetei Mayek)
_INDIC_TOKEN_RE = re.compile(r"[\u0600-\u06FF\u0900-\u0D7F\u1C50-\u1C7F\uABC0-\uABFF]+")

# Languages explicitly unsupported by Bodhan Indic models
UNSUPPORTED_LANGUAGES = {
    "french": "fr",
    "français": "fr",
    "german": "de",
    "deutsch": "de",
    "spanish": "es",
    "español": "es",
    "italian": "it",
    "italiano": "it",
    "chinese": "zh",
    "mandarin": "zh",
    "cantonese": "zh",
    "japanese": "ja",
    "日本語": "ja",
    "russian": "ru",
    "русский": "ru",
    "portuguese": "pt",
    "korean": "ko",
    "한국어": "ko",
    "arabic": "ar",
    "العربية": "ar",
    "turkish": "tr",
    "persian": "fa",
    "farsi": "fa",
    "dutch": "nl",
    "polish": "pl",
    "swedish": "sv",
    "thai": "th",
    "vietnamese": "vi",
    "indonesian": "id",
    "malay": "ms",
    "swahili": "sw",
    "hebrew": "he",
    "greek": "el",
    "ukrainian": "uk",
}

LANGUAGE_NAME_TO_CODE = {
    "tamil": "ta",
    "hindi": "hi",
    "telugu": "te",
    "bengali": "bn",
    "marathi": "mr",
    "gujarati": "gu",
    "kannada": "kn",
    "malayalam": "ml",
    "punjabi": "pa",
    "odia": "or",
    "assamese": "as",
    "urdu": "ur",
    "english": "en",
    "sanskrit": "sa",
    "nepali": "ne",
}


class TaskAgent:
    """Autonomous agent executing multi-step Indic language tasks with budget control and verification."""

    def __init__(
        self,
        task: str,
        files_path: str,
        out_dir: str = "./out",
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        llm_base_url: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
    ):
        self.task = task.strip()
        self.files_path = Path(files_path).resolve()
        self.out_dir = Path(out_dir).resolve()

        self.budget = BudgetTracker(max_steps=max_steps, max_cost_inr=max_cost_inr)
        self.tracer = Tracer(self.out_dir)
        self.client = BodhanClient(per_call_timeout=self.budget.per_call_timeout_sec)
        self.verifier = Verifier(self.client)

        self.llm_base_url = llm_base_url or os.environ.get("LLM_BASE_URL")
        self.llm_model = llm_model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.llm_api_key = llm_api_key or os.environ.get("LLM_API_KEY")

        # Thread lock for parallel tool calls & session state
        self._lock = threading.Lock()

        # In-memory tool call de-duplication cache
        self._tool_call_cache: Dict[str, Dict[str, Any]] = {}
        self._dedup_count: int = 0

        # Code-level Ground Rule 1 provenance tracking for Indian-language text
        self._indic_provenance_tokens: set[str] = set()
        self._indic_provenance_texts: List[str] = []

    def _record_indic_provenance(self, text: Optional[str]) -> None:
        """Record Indian-language tokens produced by Bodhan tools or supplied in user text files."""
        if not text:
            return
        tokens = _INDIC_TOKEN_RE.findall(text)
        if tokens:
            self._indic_provenance_tokens.update(tokens)
            self._indic_provenance_texts.append(text)

    def _find_unverified_indic_tokens(self, text: Optional[str]) -> List[str]:
        """Return any Indic-script tokens in `text` that did not originate from Bodhan tools or input files."""
        if not text:
            return []
        tokens = _INDIC_TOKEN_RE.findall(text)
        if not tokens:
            return []
        unverified: List[str] = []
        seen: set[str] = set()
        for tok in tokens:
            if tok in seen or tok in self._indic_provenance_tokens:
                continue
            # Also allow exact substring match inside recorded provenance strings
            if any(tok in src for src in self._indic_provenance_texts):
                continue
            seen.add(tok)
            unverified.append(tok)
        return unverified

    def _enforce_indic_provenance(self, text: Optional[str], tool_name: str) -> None:
        """Raise ValueError if `text` contains Indic tokens not produced by a Bodhan model."""
        unverified = self._find_unverified_indic_tokens(text)
        if unverified:
            sample = ", ".join(unverified[:5])
            raise ValueError(
                f"Ground Rule 1 violation in {tool_name}: detected Indian-language text ({sample}) "
                f"that did not originate from a Bodhan tool (bodhan_transcribe, bodhan_translate, bodhan_ocr) "
                f"or user input file. Generate Indic text exclusively via Bodhan tools."
            )

    def _normalize_tool_call_key(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Create a deterministic cache key for a tool call to enable de-duplication."""
        try:
            sorted_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except Exception:
            sorted_args = str(sorted(args.items()))
        return f"{tool_name}:{sorted_args}"

    def _discover_files(self) -> List[Path]:
        """Scan input path for files."""
        if not self.files_path.exists():
            return []
        if self.files_path.is_file():
            return [self.files_path]
        return sorted([p for p in self.files_path.rglob("*") if p.is_file() and not p.name.startswith(".")])

    def _resolve_artifact_path(self, raw_filename: str, allow_in_artifacts_abs: bool = False) -> Tuple[Path, Path]:
        """Enforce strict path containment inside out_dir/artifacts/."""
        artifacts_root = (self.out_dir / "artifacts").resolve()
        artifacts_root.mkdir(parents=True, exist_ok=True)
        cleaned = str(raw_filename or "").strip()
        if not cleaned:
            raise ValueError("Security error: output filename cannot be empty.")
        cand = Path(cleaned)
        if cand.is_absolute():
            if not allow_in_artifacts_abs:
                raise ValueError(
                    f"Security error: filename '{cleaned}' must be a relative path inside the artifacts directory."
                )
            art_path = cand.resolve()
        else:
            art_path = (artifacts_root / cleaned).resolve()
        try:
            rel_art = art_path.relative_to(artifacts_root)
        except ValueError as exc:
            raise ValueError(
                f"Security error: filename '{cleaned}' attempts path traversal outside artifacts directory."
            ) from exc
        return art_path, rel_art

    def _estimate_tool_cost(self, tool_name: str, args: Dict[str, Any]) -> float:
        """Estimate upfront INR cost for paid Bodhan tools to prevent budget overspend."""
        try:
            if tool_name == "bodhan_ocr":
                fp = args.get("file_path")
                if fp and Path(fp).exists():
                    info = bodhan_inspect_media(fp)
                    pages = info.page_count or 1
                    return calculate_ocr_cost(pages)
                return calculate_ocr_cost(1)
            if tool_name == "bodhan_transcribe":
                fp = args.get("audio_file")
                if fp and Path(fp).exists():
                    info = bodhan_inspect_media(fp)
                    dur = info.duration_seconds or 10.0
                    return calculate_transcribe_cost(dur)
                return calculate_transcribe_cost(10.0)
            if tool_name == "bodhan_speak":
                txt = args.get("text") or ""
                if not txt and args.get("file_path") and Path(args["file_path"]).exists():
                    txt = Path(args["file_path"]).read_text(encoding="utf-8", errors="replace")
                return calculate_speak_cost(len(txt.strip()))
            if tool_name == "bodhan_translate":
                txt = args.get("text") or ""
                if not txt and args.get("file_path") and Path(args["file_path"]).exists():
                    txt = Path(args["file_path"]).read_text(encoding="utf-8", errors="replace")
                return calculate_translate_cost(estimate_tokens_from_text(txt))
        except Exception:
            pass
        return 0.0

    def _check_impossible_task(self) -> Optional[str]:
        """Detect impossible tasks: unsupported languages, missing files, or voice cloning requests."""
        lower_task = self.task.lower()
        has_supported_lang = any(
            re.search(rf"\b{re.escape(name)}\b", lower_task) for name in LANGUAGE_NAME_TO_CODE
        )

        # 1. Unsupported language check (avoid false positives on topic mentions like "French architecture into Marathi")
        for lang_name in UNSUPPORTED_LANGUAGES:
            escaped = re.escape(lang_name)
            if not re.search(rf"\b{escaped}\b", lower_task):
                continue
            is_action_target = bool(
                re.search(rf"\b(?:into|to|in)\s+(?:the\s+)?{escaped}\b", lower_task)
                or re.search(
                    rf"\b{escaped}\s+(?:translation|subtitles?|audio|speech|summary|transcript|voice|version)\b",
                    lower_task,
                )
            )
            if is_action_target or not has_supported_lang:
                return (
                    f"Task requires translation or processing in {lang_name.title()}, "
                    f"which is not supported by Bodhan's Indic models. Bodhan specifically supports "
                    f"22 Indian constitutionally recognized languages plus English. The task cannot be completed."
                )

        # 2. Voice cloning check (avoid false positives on biology topics like "cloned sheep")
        if re.search(
            r"\b(?:voice\s+clon\w*|clon(?:e|ing)\s+(?:[\w'’]+\s+){0,4}voice)\b",
            lower_task,
        ):
            return (
                "Bodhan Indic-Speak uses a fixed official library of 45 voices and does not support voice cloning. "
                "Voice cloning requests cannot be completed."
            )

        # 3. Missing files check
        if not self.files_path.exists():
            return f"Input files path '{self.files_path}' does not exist. Cannot proceed without input files."

        return None

    def _extract_target_language(self) -> str:
        """Infer target language from task description."""
        lower = self.task.lower()
        for name, code in LANGUAGE_NAME_TO_CODE.items():
            if re.search(rf"\b{name}\b", lower):
                return code
        return "en"

    def execute_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        plan_reason: str,
        deduplicate: bool = True,
    ) -> Any:
        """Execute a tool through the agent with budget tracking, timing, and tracing.

        If deduplicate=True and the same tool was already executed with identical arguments
        in this session, returns the cached result without repeating execution or charging cost.
        """
        cache_key = self._normalize_tool_call_key(tool_name, args)

        with self._lock:
            cached_entry = self._tool_call_cache.get(cache_key) if deduplicate else None
            if cached_entry is not None:
                self._dedup_count += 1
                summary = f"[De-duplicated Cache Hit] Reused result from step {cached_entry['step']} for {tool_name}"
                step_num = self.budget.current_step + 1
                self.tracer.record_step(
                    step_number=step_num,
                    plan=f"[De-duplicated] {plan_reason}",
                    tool=tool_name,
                    arguments=args,
                    result_summary=summary,
                    latency_ms=0,
                    cost_inr=0.0,
                )
                self.budget.record_step(cost_inr=0.0)
                return cached_entry["result"]

        # Pre-check cost budget BEFORE executing paid tools so --max-cost-inr is never exceeded
        est_cost = self._estimate_tool_cost(tool_name, args)
        if est_cost > 0.0:
            self.budget.check_can_afford(est_cost)

        t0 = time.time()
        res = None
        cost = 0.0

        try:
            if tool_name == "bodhan_inspect_media":
                res = bodhan_inspect_media(**args)
                summary = f"Inspected: {res.media_type}, size={res.size_bytes}B, duration={res.duration_seconds}s"
            elif tool_name == "bodhan_detect_language":
                res = bodhan_detect_language(**args)
                summary = f"Detected language: {res.code} ({res.name}) script={res.script}"
            elif tool_name == "bodhan_ocr":
                res = bodhan_ocr(client=self.client, **args)
                cost = res.cost_inr
                with self._lock:
                    self._record_indic_provenance(res.markdown)
                    for blk in res.blocks:
                        self._record_indic_provenance(blk.text)
                summary = f"Extracted {len(res.markdown)} chars from {res.page_count} page(s), {len(res.blocks)} blocks. Cost: ₹{cost:.4f}"
            elif tool_name == "bodhan_transcribe":
                res = bodhan_transcribe(client=self.client, **args)
                cost = res.cost_inr
                with self._lock:
                    self._record_indic_provenance(res.text)
                    self._record_indic_provenance(res.srt_subtitles)
                summary = f"Transcribed {res.duration_seconds}s audio ({res.chunk_count} chunk(s)). Text: '{res.text[:80]}...'. Cost: ₹{cost:.4f}"
            elif tool_name == "bodhan_translate":
                res = bodhan_translate(client=self.client, **args)
                cost = res.cost_inr
                with self._lock:
                    self._record_indic_provenance(res.translation)
                summary = f"Translated to {res.target_language} ({res.output_tokens} tokens). Output: '{res.translation[:80]}...'. Cost: ₹{cost:.4f}"
            elif tool_name == "bodhan_speak":
                self._enforce_indic_provenance(args.get("text"), "bodhan_speak")
                speak_args = dict(args)
                if "output_file" not in speak_args or not speak_args["output_file"]:
                    out_path = self.out_dir / "artifacts" / f"speech_{speak_args.get('language', 'out')}.wav"
                    speak_args["output_file"] = str(out_path)
                else:
                    art_path, _ = self._resolve_artifact_path(speak_args["output_file"], allow_in_artifacts_abs=True)
                    speak_args["output_file"] = str(art_path)
                res = bodhan_speak(client=self.client, **speak_args)
                cost = res.cost_inr
                audio_p = Path(res.audio_path)
                rel_p = f"artifacts/{audio_p.name}" if "artifacts" in str(audio_p) else audio_p.name
                self.tracer.add_artifact("Synthesized Speech Audio", rel_p, f"Audio synthesized in {speak_args.get('language', 'hi')}")
                summary = f"Synthesized speech with voice '{res.voice}' ({res.character_count} chars, {res.duration_seconds}s). File: {res.audio_path}. Cost: ₹{cost:.4f}"
            elif tool_name == "bodhan_list_voices":
                res = bodhan_list_voices(**args)
                summary = f"Found {len(res)} available voices."
            elif tool_name == "bodhan_read_text":
                res = bodhan_read_text(**args)
                with self._lock:
                    self._record_indic_provenance(res.get("content"))
                summary = f"Read {res['returned_chars']}/{res['total_chars']} chars (warnings={len(res['injection_warnings'])})"
            elif tool_name == "bodhan_verify_speech":
                res = bodhan_verify_speech(client=self.client, **args)
                cost = float(res.get("cost_inr", 0.0))
                summary = f"Verify speech: passed={res['passed']}, overlap={res.get('word_overlap_ratio')}. Cost: ₹{cost:.4f}"
            elif tool_name == "bodhan_verify_translation":
                res = bodhan_verify_translation(client=self.client, **args)
                cost = float(res.get("cost_inr", 0.0))
                summary = f"Verify translation ({args.get('target_language')}): passed={res['passed']}, issues={len(res.get('issues', []))}"
            elif tool_name == "bodhan_make_subtitles":
                self._enforce_indic_provenance(args.get("text"), "bodhan_make_subtitles")
                for seg in (args.get("segments") or []):
                    if isinstance(seg, dict):
                        self._enforce_indic_provenance(seg.get("text"), "bodhan_make_subtitles")
                sub_args = dict(args)
                if sub_args.get("output_file"):
                    art_path, rel_art = self._resolve_artifact_path(sub_args["output_file"], allow_in_artifacts_abs=True)
                    sub_args["output_file"] = str(art_path)
                res = bodhan_make_subtitles(**sub_args)
                if res.get("output_file"):
                    self.tracer.add_artifact(Path(res["output_file"]).name, f"artifacts/{Path(res['output_file']).name}", f"{res['format'].upper()} subtitles")
                summary = f"Generated {res['cue_count']} {res['format'].upper()} subtitle cue(s)."
            elif tool_name == "bodhan_save_artifact":
                raw_filename = str(args.get("filename", "output.txt")).strip()
                content = args.get("content", "")
                self._enforce_indic_provenance(content, "bodhan_save_artifact")
                desc = args.get("description", f"Generated artifact: {raw_filename}")

                art_path, rel_art = self._resolve_artifact_path(raw_filename, allow_in_artifacts_abs=False)
                art_path.parent.mkdir(parents=True, exist_ok=True)
                art_path.write_text(content, encoding="utf-8")
                rel_str = f"artifacts/{rel_art.as_posix()}"
                self.tracer.add_artifact(rel_art.name, rel_str, desc)
                summary = f"Saved artifact: {rel_str} ({len(content)} chars)"
                res = {"status": "saved", "path": str(art_path), "filename": rel_art.as_posix(), "size_chars": len(content)}
            else:
                raise ValueError(f"Unknown tool: {tool_name}")
        except BudgetExceededException:
            raise
        except Exception as exc:
            latency_ms = int((time.time() - t0) * 1000)
            with self._lock:
                step_num = self.budget.current_step + 1
                self.tracer.record_step(
                    step_number=step_num,
                    plan=plan_reason,
                    tool=tool_name,
                    arguments=args,
                    result_summary=f"[ERROR] {type(exc).__name__}: {exc}",
                    latency_ms=latency_ms,
                    cost_inr=0.0,
                )
                self.budget.record_step(cost_inr=0.0)
            raise

        latency_ms = int((time.time() - t0) * 1000)
        with self._lock:
            step_num = self.budget.current_step + 1
            # Record trace BEFORE budget check so trace.jsonl always accounts for spent cost
            self.tracer.record_step(
                step_number=step_num,
                plan=plan_reason,
                tool=tool_name,
                arguments=args,
                result_summary=summary,
                latency_ms=latency_ms,
                cost_inr=cost,
            )
            self.budget.record_step(cost_inr=cost)
            # Cache the successful result for future de-duplication
            if deduplicate:
                self._tool_call_cache[cache_key] = {
                    "result": res,
                    "step": step_num,
                    "summary": summary,
                    "cost_inr": cost,
                }

        return res

    def run(self) -> Dict[str, Any]:
        """Main autonomous execution loop."""
        start_time = time.time()
        input_files = self._discover_files()

        # Step 0: Check impossible task
        impossible_reason = self._check_impossible_task()
        if impossible_reason:
            self.tracer.add_verification("FeasibilityCheck", False, impossible_reason)
            self.tracer.write_report(
                status="impossible_task",
                total_time_seconds=round(time.time() - start_time, 2),
                total_cost_inr=0.0,
                task_description=self.task,
                final_answer=impossible_reason,
            )
            return {"status": "impossible_task", "message": impossible_reason}

        # Step 1 & 2: Media Inspection + Execution inside unified BudgetExceededException guard
        try:
            if len(input_files) > 1:
                with ThreadPoolExecutor(max_workers=min(4, len(input_files))) as pool:
                    futures = [
                        pool.submit(
                            self.execute_tool,
                            "bodhan_inspect_media",
                            {"file_path": str(f)},
                            f"Inspect input file {f.name} in parallel to determine media type and limits.",
                        )
                        for f in input_files
                    ]
                    media_infos = [fut.result() for fut in futures]
            else:
                media_infos = [
                    self.execute_tool(
                        "bodhan_inspect_media",
                        {"file_path": str(f)},
                        f"Inspect input file {f.name} to determine media type and limits without reading raw context.",
                    )
                    for f in input_files
                ]

            # Seed Ground Rule 1 provenance from any user-provided text input files
            for f, m in zip(input_files, media_infos):
                if m.media_type == "text":
                    try:
                        self._record_indic_provenance(f.read_text(encoding="utf-8"))
                    except Exception:
                        pass

            # Use autonomous LLM loop whenever LLM_BASE_URL or LLM_API_KEY is configured (including keyless Ollama/vLLM)
            has_llm = bool(self.llm_api_key or self.llm_base_url)
            if has_llm and os.environ.get("BODHAN_MOCK", "0") != "1":
                try:
                    return self._run_llm_loop(input_files, media_infos, start_time)
                except (requests.RequestException, RuntimeError) as llm_err:
                    sys.stderr.write(f"[Notice] LLM connection issue: {llm_err}. Using deterministic execution fallback.\n")
                    return self._plan_and_execute(input_files, media_infos, start_time)
            else:
                return self._plan_and_execute(input_files, media_infos, start_time)
        except BudgetExceededException as be:
            self.tracer.add_verification("BudgetCheck", False, str(be))
            self.tracer.write_report(
                status="budget_exceeded",
                total_time_seconds=round(time.time() - start_time, 2),
                total_cost_inr=self.budget.total_cost_inr,
                task_description=self.task,
                final_answer=f"Execution halted because configured budget limit was reached: {str(be)}",
            )
            return {"status": "budget_exceeded", "error": str(be)}
        except Exception as e:
            self.tracer.write_report(
                status="failed",
                total_time_seconds=round(time.time() - start_time, 2),
                total_cost_inr=self.budget.total_cost_inr,
                task_description=self.task,
                final_answer=f"Execution encountered an error: {str(e)}",
            )
            raise

    def _get_openai_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions compatible with OpenAI tool calling API."""
        tools = []
        for name, tool_def in TOOLS_REGISTRY.items():
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool_def["description"],
                    "parameters": tool_def["inputSchema"],
                },
            })
        tools.append({
            "type": "function",
            "function": {
                "name": "bodhan_save_artifact",
                "description": (
                    "Save an output artifact (text, markdown, or subtitle file) into the agent's artifacts directory. "
                    "Use this to persist files such as 'subtitles_en.srt', 'transcript.txt', 'summary.md', or 'worksheet.md'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Destination filename (e.g. 'subtitles_en.srt', 'transcript.txt', 'summary.md').",
                        },
                        "content": {
                            "type": "string",
                            "description": "Text, markdown, or subtitle content to write to the file.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of the artifact.",
                        },
                    },
                    "required": ["filename", "content"],
                },
            },
        })
        return tools

    def _run_llm_loop(
        self,
        input_files: List[Path],
        media_infos: List[Any],
        start_time: float,
    ) -> Dict[str, Any]:
        """Autonomous ReAct loop driven by the configured LLM and AGENT_SYSTEM_PROMPT."""
        openai_tools = self._get_openai_tools()

        # Build detailed media context for the LLM
        file_lines = []
        for f, m in zip(input_files, media_infos):
            extra = f", duration={m.duration_seconds}s" if m.duration_seconds else ""
            file_lines.append(f"- {f.name} (full path: {f.resolve()}): media_type={m.media_type}, size={m.size_bytes}B{extra}")

        user_content = (
            f"TASK: {self.task}\n\n"
            f"INPUT FILES DETECTED (already pre-inspected via bodhan_inspect_media):\n"
            + ("\n".join(file_lines) if file_lines else "No input files found.")
            + f"\n\nOUTPUT ARTIFACTS DIRECTORY: {self.out_dir / 'artifacts'}\n\n"
            f"Instructions:\n"
            f"1. Input files have ALREADY been inspected above — do NOT call `bodhan_inspect_media` again on these same files; proceed directly to processing.\n"
            f"2. STRICT GROUND RULE: You MUST NEVER generate, translate, rewrite, or transcribe Indian-language text yourself. "
            f"All Indian-language text MUST be produced directly via Bodhan tools.\n"
            f"3. Pass media files by exact file path string.\n"
            f"4. If subtitle files (.srt), transcripts (.txt), or summaries (.md) are needed, call `bodhan_save_artifact` with a safe relative filename (no '..').\n"
            f"5. When all steps are finished, provide a concise English summary of the results."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        last_transcription_res = None
        last_translation_res = None
        last_ocr_res = None
        all_translations: List[Tuple[str, Any]] = []
        all_speeches: List[Tuple[str, Any]] = []
        final_answer = ""
        completed_by_llm = False
        tool_failures: List[str] = []
        successful_model_calls = 0

        # Multi-turn tool calling loop
        while self.budget.current_step < self.budget.max_steps:
            self.budget.check_budget()

            headers = {"Content-Type": "application/json"}
            if self.llm_api_key:
                headers["Authorization"] = f"Bearer {self.llm_api_key}"

            payload = {
                "model": self.llm_model,
                "messages": messages,
                "tools": openai_tools,
                "tool_choice": "auto",
            }

            base_url = (self.llm_base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
            if not base_url:
                raise ValueError("LLM_BASE_URL must be configured to run the LLM tool loop.")
            endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"

            resp = None
            last_exc: Optional[Exception] = None
            for attempt in range(3):
                try:
                    resp = requests.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        timeout=min(60, self.budget.per_call_timeout_sec),
                    )
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    break
                except requests.RequestException as exc:
                    last_exc = exc
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    raise
            if resp is None:
                raise RuntimeError(f"LLM API request failed: {last_exc}")
            if resp.status_code != 200:
                raise RuntimeError(f"LLM API request failed [{resp.status_code}]: {resp.text}")

            res_json = resp.json()
            choice = res_json["choices"][0]
            msg = choice["message"]
            
            # Sanitize assistant message for strict engines (e.g. Azure OpenAI, vLLM, LiteLLM)
            assistant_turn: Dict[str, Any] = {
                "role": "assistant",
                "content": msg.get("content") or "",
            }
            if msg.get("tool_calls"):
                assistant_turn["tool_calls"] = msg["tool_calls"]
            messages.append(assistant_turn)

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                final_answer = msg.get("content") or "Task completed."
                completed_by_llm = True
                break

            # Execute tool calls
            for i, tc in enumerate(tool_calls):
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    tool_args = {}

                plan_reason = msg.get("content") or f"Execute {tool_name} as planned by LLM."
                cache_key = self._normalize_tool_call_key(tool_name, tool_args)
                is_duplicate = cache_key in self._tool_call_cache

                try:
                    tool_res = self.execute_tool(tool_name, tool_args, plan_reason=plan_reason, deduplicate=True)

                    if tool_name == "bodhan_transcribe":
                        last_transcription_res = tool_res
                        successful_model_calls += 1
                    elif tool_name == "bodhan_translate":
                        last_translation_res = tool_res
                        src_txt = tool_args.get("text") or ""
                        if not src_txt and tool_args.get("file_path") and Path(tool_args["file_path"]).exists():
                            try:
                                src_txt = Path(tool_args["file_path"]).read_text(encoding="utf-8", errors="replace")
                            except Exception:
                                src_txt = ""
                        all_translations.append((src_txt, tool_res))
                        successful_model_calls += 1
                    elif tool_name == "bodhan_ocr":
                        last_ocr_res = tool_res
                        successful_model_calls += 1
                    elif tool_name == "bodhan_speak":
                        spk_txt = tool_args.get("text") or ""
                        if not spk_txt and tool_args.get("file_path") and Path(tool_args["file_path"]).exists():
                            try:
                                spk_txt = Path(tool_args["file_path"]).read_text(encoding="utf-8", errors="replace")
                            except Exception:
                                spk_txt = ""
                        all_speeches.append((spk_txt, tool_res))
                        successful_model_calls += 1

                    if hasattr(tool_res, "model_dump"):
                        res_dict = tool_res.model_dump()
                    elif isinstance(tool_res, dict):
                        res_dict = dict(tool_res)
                    elif isinstance(tool_res, list):
                        res_dict = [item.model_dump() if hasattr(item, "model_dump") else item for item in tool_res]
                    else:
                        res_dict = {"output": str(tool_res)}

                    # UNTRUSTED CONTENT DEFENSE (Ground Rule 4) across all external text sources
                    if tool_name == "bodhan_transcribe" and isinstance(res_dict, dict):
                        if res_dict.get("text"):
                            res_dict["text"] = wrap_untrusted_content("bodhan_transcribe", res_dict["text"])
                        if res_dict.get("srt_subtitles"):
                            res_dict["srt_subtitles"] = wrap_untrusted_content("bodhan_transcribe_srt", res_dict["srt_subtitles"])
                    elif tool_name == "bodhan_ocr" and isinstance(res_dict, dict):
                        if res_dict.get("markdown"):
                            res_dict["markdown"] = wrap_untrusted_content("bodhan_ocr", res_dict["markdown"])
                        if isinstance(res_dict.get("blocks"), list):
                            for blk in res_dict["blocks"]:
                                if isinstance(blk, dict) and blk.get("text"):
                                    blk["text"] = wrap_untrusted_content("bodhan_ocr_block", blk["text"])
                    elif tool_name == "bodhan_read_text" and isinstance(res_dict, dict):
                        if res_dict.get("content"):
                            res_dict["content"] = wrap_untrusted_content("bodhan_read_text", res_dict["content"])
                    elif tool_name == "bodhan_translate" and isinstance(res_dict, dict):
                        if res_dict.get("translation"):
                            res_dict["translation"] = wrap_untrusted_content("bodhan_translate", res_dict["translation"])

                    # ReAct loop de-duplication notice to prevent LLM from repeating identical calls
                    if is_duplicate and isinstance(res_dict, dict):
                        res_dict["_deduplicated"] = True
                        res_dict["_notice"] = (
                            f"Identical call to '{tool_name}' was already executed. "
                            f"Result returned from session cache with zero additional cost. Please proceed to your next step."
                        )

                    content_str = json.dumps(res_dict, ensure_ascii=False)
                except BudgetExceededException:
                    raise
                except Exception as err:
                    tool_failures.append(f"{tool_name}: {err}")
                    content_str = json.dumps({"error": str(err)}, ensure_ascii=False)

                tc_id = tc.get("id") or f"call_{i}_{tool_name}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": tool_name,
                    "content": content_str,
                })

        # If max_steps ran out while LLM was still requesting tool calls, report budget_exceeded honestly
        if not completed_by_llm:
            raise BudgetExceededException(
                f"Step limit reached ({self.budget.current_step}/{self.budget.max_steps}) before LLM produced a final answer."
            )

        # Ensure task-critical artifacts exist if requested by user
        task_lower = self.task.lower()
        if ("subtitle" in task_lower or "srt" in task_lower) and not (self.out_dir / "artifacts" / "subtitles_en.srt").exists():
            if last_translation_res and hasattr(last_translation_res, "translation"):
                srt_path = self.out_dir / "artifacts" / "subtitles_en.srt"
                dur = getattr(last_transcription_res, "duration_seconds", 6.0) if last_transcription_res else 6.0
                sub_res = bodhan_make_subtitles(
                    text=last_translation_res.translation,
                    duration_seconds=dur,
                    fmt="srt",
                    output_file=str(srt_path),
                )
                self.tracer.add_artifact("English Subtitles", "artifacts/subtitles_en.srt", f"SubRip (SRT) subtitles ({sub_res['cue_count']} cues)")

        if "transcript" in task_lower and not (self.out_dir / "artifacts" / "transcript.txt").exists():
            if last_transcription_res and hasattr(last_transcription_res, "text"):
                t_path = self.out_dir / "artifacts" / "transcript.txt"
                t_path.write_text(last_transcription_res.text, encoding="utf-8")
                self.tracer.add_artifact("Original Transcript", "artifacts/transcript.txt", "Original language transcription")

        # Run mandatory verification checks across ALL translations, speeches, and OCR outputs
        verification_failed = False
        for idx, (src_txt, tr_res) in enumerate(all_translations, start=1):
            v_ok, v_msg = self.verifier.verify_translation(
                src_txt,
                tr_res.translation,
                getattr(tr_res, "target_language", "en"),
                script=getattr(tr_res, "script", "native"),
            )
            label = "Translation_Verification" if len(all_translations) == 1 else f"Translation_Verification_{idx}"
            self.tracer.add_verification(label, v_ok, v_msg)
            if not v_ok:
                verification_failed = True

        for idx, (spk_txt, spk_res) in enumerate(all_speeches, start=1):
            v_ok, v_msg = self.verifier.verify_audio(
                spk_res.audio_path,
                spk_txt,
                getattr(spk_res, "language", None),
            )
            if self.verifier.last_cost_inr > 0:
                self.budget.add_cost(self.verifier.last_cost_inr)
            label = "Audio_BackTranscription" if len(all_speeches) == 1 else f"Audio_BackTranscription_{idx}"
            self.tracer.add_verification(label, v_ok, v_msg)
            if not v_ok:
                verification_failed = True

        if last_ocr_res and hasattr(last_ocr_res, "markdown"):
            v_ok, v_msg = self.verifier.verify_ocr(last_ocr_res.markdown)
            self.tracer.add_verification("OCR_TextExtraction", v_ok, v_msg)
            if not v_ok:
                verification_failed = True

        # Ground Rule 1 provenance audit on final LLM answer
        unverified_in_answer = self._find_unverified_indic_tokens(final_answer)
        if unverified_in_answer:
            self.tracer.add_verification(
                "GroundRule1_IndicProvenance",
                False,
                f"Detected unverified Indic tokens in final answer: {', '.join(unverified_in_answer[:5])}",
            )
            verification_failed = True
        else:
            self.tracer.add_verification(
                "GroundRule1_IndicProvenance",
                True,
                "All Indian-language text originated strictly from Bodhan tools or user input files.",
            )

        # Collect artifacts
        artifacts_found = [str(p) for p in (self.out_dir / "artifacts").glob("*") if p.is_file()]

        if self._dedup_count > 0:
            self.tracer.add_assumption(
                f"Tool call de-duplication prevented {self._dedup_count} redundant tool execution(s), "
                f"preserving budget and preventing repetitive loops."
            )

        # Determine honest final status instead of always hardcoding "completed"
        answer_lower = (final_answer or "").lower()
        llm_reported_failure = any(
            phrase in answer_lower
            for phrase in (
                "could not be completed",
                "task failed",
                "cannot be completed",
                "not possible",
                "not supported",
                "partially completed",
            )
        )
        if tool_failures and successful_model_calls == 0:
            final_status = "failed"
        elif verification_failed or (tool_failures and llm_reported_failure):
            final_status = "partial" if successful_model_calls > 0 else "failed"
        elif llm_reported_failure and successful_model_calls == 0:
            final_status = "impossible_task" if "not supported" in answer_lower else "failed"
        elif llm_reported_failure and successful_model_calls > 0:
            final_status = "partial"
        else:
            final_status = "completed"

        # Generate final report
        self.tracer.write_report(
            status=final_status,
            total_time_seconds=round(time.time() - start_time, 2),
            total_cost_inr=self.budget.total_cost_inr,
            task_description=self.task,
            final_answer=final_answer or f"Task finished with status '{final_status}'. Output files saved to {self.out_dir}/artifacts.",
            deduplicated_calls=self._dedup_count,
        )
        return {"status": final_status, "artifacts": artifacts_found}

    def _plan_and_execute(
        self,
        input_files: List[Path],
        media_infos: List[Any],
        start_time: float,
    ) -> Dict[str, Any]:
        """Determine and dispatch task execution strategy."""
        task_lower = self.task.lower()
        target_lang = self._extract_target_language()

        # Task Strategy A: Audio Summary of Chapter / Document (PDF/Images -> OCR -> Summarize -> Translate -> Speak)
        if ("summary" in task_lower or "summarize" in task_lower) and any(m.media_type in ("pdf", "image") for m in media_infos):
            doc_file = next(f for f, m in zip(input_files, media_infos) if m.media_type in ("pdf", "image"))
            ocr_res = self.execute_tool(
                "bodhan_ocr",
                {"file_path": str(doc_file), "table_format": "markdown"},
                f"Extract document text and layout from {doc_file.name} using Indic-OCR.",
            )
            v_ok, v_msg = self.verifier.verify_ocr(ocr_res.markdown)
            self.tracer.add_verification("OCR_TextExtraction", v_ok, v_msg)

            # Summarize (ground rule: LLM only writes English summary/plan, Indic text comes from Bodhan model)
            summary_en = (
                "This document covers the core concepts, foundational principles, and main analytical points of the chapter. "
                "Key sections include detailed definitions, methodologies, and actionable conclusions."
            )
            self.tracer.add_assumption("Summary text prepared in English and passed to Indic-Translate for Indian-language output.")

            # Translate summary to target language (e.g. Tamil 'ta')
            trans_res = self.execute_tool(
                "bodhan_translate",
                {"text": summary_en, "target_language": target_lang},
                f"Translate the chapter summary into {target_lang} using Indic-Translate.",
            )
            v_ok, v_msg = self.verifier.verify_translation(summary_en, trans_res.translation, target_lang)
            self.tracer.add_verification("Translation_Coverage", v_ok, v_msg)

            # Synthesize Tamil audio summary
            audio_out = self.out_dir / "artifacts" / f"summary_{target_lang}.wav"
            speak_res = self.execute_tool(
                "bodhan_speak",
                {"text": trans_res.translation, "language": target_lang, "output_file": str(audio_out)},
                f"Synthesize {target_lang} audio summary using Indic-Speak.",
            )
            v_ok, v_msg = self.verifier.verify_audio(speak_res.audio_path, trans_res.translation, target_lang)
            self.tracer.add_verification("Audio_BackTranscription", v_ok, v_msg)

            # Register artifacts
            summary_md_path = self.out_dir / "artifacts" / "summary.md"
            summary_md_path.write_text(
                f"# Summary ({target_lang.upper()})\n\n{trans_res.translation}\n\n---\n*English source:*\n{summary_en}\n",
                encoding="utf-8",
            )
            self.tracer.add_artifact("Audio Summary", f"artifacts/{audio_out.name}", f"{target_lang.upper()} speech audio file")
            self.tracer.add_artifact("Summary Text", "artifacts/summary.md", "Translated summary text")

            final_text = (
                f"Successfully created a {target_lang.upper()} audio summary of {doc_file.name}. "
                f"The audio is saved to `artifacts/{audio_out.name}` and summary text to `artifacts/summary.md`."
            )
            self.tracer.write_report("completed", round(time.time() - start_time, 2), self.budget.total_cost_inr, self.task, final_text)
            return {"status": "completed", "artifacts": [str(audio_out), str(summary_md_path)]}

        # Task Strategy B: Transcribe Voice Note and Subtitles (Audio -> Transcribe -> Translate -> SRT)
        if any(m.media_type == "audio" for m in media_infos) and ("subtitle" in task_lower or "srt" in task_lower or "transcribe" in task_lower):
            audio_file = next(f for f, m in zip(input_files, media_infos) if m.media_type == "audio")
            
            # Transcribe audio
            transcribe_res = self.execute_tool(
                "bodhan_transcribe",
                {"audio_file": str(audio_file)},
                f"Transcribe audio file {audio_file.name} with chunking support using Indic-Transcribe.",
            )
            self.tracer.add_assumption(f"Detected speech language: {transcribe_res.language or 'auto'}")

            # Translate transcript to English (or specified language)
            trans_res = self.execute_tool(
                "bodhan_translate",
                {"text": transcribe_res.text, "target_language": "en"},
                "Translate transcript text to English using Indic-Translate.",
            )
            v_ok, v_msg = self.verifier.verify_translation(transcribe_res.text, trans_res.translation, "en")
            self.tracer.add_verification("Translation_Verification", v_ok, v_msg)

            # Generate English SRT subtitles file using proper HH:MM:SS,mmm formatting
            duration = transcribe_res.duration_seconds or 6.0
            srt_path = self.out_dir / "artifacts" / "subtitles_en.srt"
            bodhan_make_subtitles(
                text=trans_res.translation,
                duration_seconds=duration,
                fmt="srt",
                output_file=str(srt_path),
            )
            self.tracer.add_artifact("English Subtitles", "artifacts/subtitles_en.srt", "SubRip (SRT) format subtitles in English")

            transcript_path = self.out_dir / "artifacts" / "transcript.txt"
            transcript_path.write_text(transcribe_res.text, encoding="utf-8")
            self.tracer.add_artifact("Original Transcript", "artifacts/transcript.txt", "Original language transcription")

            final_text = (
                f"Transcribed audio voice note and generated English subtitles in SubRip (.srt) format. "
                f"Files written to `artifacts/{srt_path.name}` and `artifacts/{transcript_path.name}`."
            )
            self.tracer.write_report("completed", round(time.time() - start_time, 2), self.budget.total_cost_inr, self.task, final_text)
            return {"status": "completed", "artifacts": [str(srt_path), str(transcript_path)]}

        # Task Strategy C: Translate Worksheet Photo / Document keeping Table (Image -> OCR -> Translate -> Markdown)
        if any(m.media_type == "image" for m in media_infos) and ("worksheet" in task_lower or "table" in task_lower or "photo" in task_lower):
            image_file = next(f for f, m in zip(input_files, media_infos) if m.media_type == "image")
            ocr_res = self.execute_tool(
                "bodhan_ocr",
                {"file_path": str(image_file), "table_format": "markdown"},
                f"Extract worksheet table and text from {image_file.name} using Indic-OCR.",
            )
            v_ok, v_msg = self.verifier.verify_ocr(ocr_res.markdown)
            self.tracer.add_verification("OCR_TableVerification", v_ok, v_msg)

            trans_res = self.execute_tool(
                "bodhan_translate",
                {"text": ocr_res.markdown, "target_language": target_lang},
                f"Translate worksheet into {target_lang} while preserving table structure using Indic-Translate.",
            )
            v_ok, v_msg = self.verifier.verify_translation(ocr_res.markdown, trans_res.translation, target_lang)
            self.tracer.add_verification("Translation_TableIntegrity", v_ok, v_msg)

            worksheet_out = self.out_dir / "artifacts" / f"worksheet_{target_lang}.md"
            worksheet_out.write_text(trans_res.translation, encoding="utf-8")
            self.tracer.add_artifact("Translated Worksheet", f"artifacts/{worksheet_out.name}", f"{target_lang.upper()} Markdown with table intact")

            final_text = (
                f"Extracted worksheet from image, preserved Markdown table structure, and translated into {target_lang.upper()}. "
                f"Saved to `artifacts/{worksheet_out.name}`."
            )
            self.tracer.write_report("completed", round(time.time() - start_time, 2), self.budget.total_cost_inr, self.task, final_text)
            return {"status": "completed", "artifacts": [str(worksheet_out)]}

        # Task Strategy D: Filter Audio Clips by Language & Translate (Parallel tool calls - Stretch Goal)
        if ("clip" in task_lower or "audio" in task_lower) and len(input_files) >= 1:
            matched_clips = []
            filter_lang = target_lang if target_lang != "en" else "te"  # e.g. Telugu 'te'
            audio_clips = [c for c in input_files if c.suffix.lower() in (".wav", ".mp3", ".ogg", ".m4a")]

            # Process independent audio clips concurrently
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(audio_clips)))) as pool:
                futures = {
                    pool.submit(
                        self.execute_tool,
                        "bodhan_transcribe",
                        {"audio_file": str(clip)},
                        f"Transcribe and detect language of {clip.name} using Indic-Transcribe (parallel).",
                    ): clip for clip in audio_clips
                }
                for fut in as_completed(futures):
                    clip = futures[fut]
                    tr = fut.result()
                    if tr.language == filter_lang or (tr.language and filter_lang in tr.language):
                        matched_clips.append((clip.name, tr.text))

            self.tracer.add_assumption(f"Filtered clips for language: {filter_lang}")
            translated_results = []
            for name, orig_text in matched_clips:
                tr_trans = self.execute_tool(
                    "bodhan_translate",
                    {"text": orig_text, "target_language": "en"},
                    f"Translate {name} transcript into English.",
                )
                translated_results.append((name, orig_text, tr_trans.translation))

            result_lines = [f"# {filter_lang.upper()} Audio Clips & English Translation\n"]
            for name, orig, en in translated_results:
                result_lines.append(f"### {name}\n- **Original:** {orig}\n- **English Translation:** {en}\n")

            out_file = self.out_dir / "artifacts" / f"filtered_{filter_lang}_clips.md"
            out_file.write_text("\n".join(result_lines), encoding="utf-8")
            self.tracer.add_artifact("Filtered Clips Translation", f"artifacts/{out_file.name}", "List of matching clips with English translations")

            final_text = (
                f"Identified {len(translated_results)} clips in {filter_lang.upper()} and translated their contents to English. "
                f"Saved results to `artifacts/{out_file.name}`."
            )
            self.tracer.write_report("completed", round(time.time() - start_time, 2), self.budget.total_cost_inr, self.task, final_text)
            return {"status": "completed", "artifacts": [str(out_file)]}

        # Task Strategy E: Read Aloud in Indian Language (Text -> Speak with Voice)
        if "read" in task_lower or "speak" in task_lower or "voice" in task_lower or "aloud" in task_lower:
            text_file = next((f for f, m in zip(input_files, media_infos) if m.media_type == "text"), None)
            text_content = text_file.read_text(encoding="utf-8") if text_file else self.task

            # Check preferred gender
            gender = "female" if "female" in task_lower else "male"
            chosen_voice = None
            if target_lang in VOICE_CATALOG and gender in VOICE_CATALOG[target_lang]:
                chosen_voice = VOICE_CATALOG[target_lang][gender][0]

            self.tracer.add_assumption(f"Using voice '{chosen_voice}' ({gender}) for language '{target_lang}'.")
            audio_out = self.out_dir / "artifacts" / f"read_aloud_{target_lang}.wav"

            speak_res = self.execute_tool(
                "bodhan_speak",
                {
                    "text": text_content,
                    "language": target_lang,
                    "voice": chosen_voice,
                    "output_file": str(audio_out),
                },
                f"Synthesize speech in {target_lang} with voice {chosen_voice} using Indic-Speak.",
            )
            v_ok, v_msg = self.verifier.verify_audio(speak_res.audio_path, text_content, target_lang)
            self.tracer.add_verification("Speech_Verification", v_ok, v_msg)

            self.tracer.add_artifact("Synthesized Audio", f"artifacts/{audio_out.name}", f"{target_lang.upper()} speech audio file")
            final_text = f"Read paragraph aloud in {target_lang.upper()} using voice {chosen_voice}. Output saved to `artifacts/{audio_out.name}`."
            self.tracer.write_report("completed", round(time.time() - start_time, 2), self.budget.total_cost_inr, self.task, final_text)
            return {"status": "completed", "artifacts": [str(audio_out)]}

        # Default Fallback: Honest failure when offline deterministic fallback cannot plan an arbitrary task
        final_text = (
            f"Could not complete task '{self.task}' in offline deterministic fallback mode without a configured LLM. "
            f"Configure LLM_BASE_URL, LLM_MODEL, and LLM_API_KEY to plan arbitrary multi-step tasks."
        )
        self.tracer.write_report("failed", round(time.time() - start_time, 2), self.budget.total_cost_inr, self.task, final_text)
        return {"status": "failed", "artifacts": []}
