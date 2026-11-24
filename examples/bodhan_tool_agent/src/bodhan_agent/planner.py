"""Task understanding, language detection, and DAG execution plan generator."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from bodhan_agent.file_inspector import inspect_files_in_path
from bodhan_agent.llm import LLMClient
from bodhan_agent.models import ExecutionPlan, InputFileInfo, PlanStep, TaskSpec
from bodhan_mcp.config import (
    LANGUAGE_NAMES,
    PRICING,
)

logger = logging.getLogger("bodhan_agent.planner")

# Map common language names to ISO codes
NAME_TO_CODE = {name.lower(): code for code, name in LANGUAGE_NAMES.items()}
NAME_TO_CODE.update({
    "english": "en",
    "hindi": "hi",
    "tamil": "ta",
    "telugu": "te",
    "marathi": "mr",
    "bengali": "bn",
    "gujarati": "gu",
    "kannada": "kn",
    "malayalam": "ml",
    "punjabi": "pa",
    "odia": "or",
    "urdu": "ur",
    "french": "fr",
    "german": "de",
    "spanish": "es",
})


class TaskPlanner:
    """Parses natural-language user tasks, inspects input files, and constructs structured execution plans."""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client or LLMClient()

    def parse_task(self, prompt: str, files_path: str | Path | None = None) -> TaskSpec:
        """Inspect prompt and input files to construct a structured TaskSpec."""
        p_lower = prompt.lower()
        input_files: list[InputFileInfo] = []

        if files_path:
            input_files = inspect_files_in_path(files_path)

        # Detect target languages mentioned in prompt
        targets: list[str] = []
        for name, code in NAME_TO_CODE.items():
            pattern = rf"\b{re.escape(name)}\b"
            if re.search(pattern, p_lower):
                if code not in targets:
                    targets.append(code)

        # Determine capabilities requested
        req_ocr = any(w in p_lower for w in ["ocr", "scan", "worksheet", "document", "extract text", "pdf"]) or any(f.file_type in {"document", "image"} for f in input_files)
        req_translation = any(w in p_lower for w in ["translate", "translation", "in tamil", "in hindi", "in marathi", "in telugu", "in bengali", "into french"])
        req_summary = any(w in p_lower for w in ["summary", "summarize", "summarise", "brief", "two-minute", "chapter summary"])
        req_speech = any(w in p_lower for w in ["audio summary", "speech", "read aloud", "speak", "voice", "podcast", "say", "talk"])
        req_subtitles = any(w in p_lower for w in ["subtitle", "subtitles", "srt", "captions"])

        return TaskSpec(
            user_prompt=prompt,
            detected_target_languages=targets,
            requires_summary=req_summary,
            requires_speech=req_speech,
            requires_ocr=req_ocr,
            requires_translation=req_translation,
            requires_subtitles=req_subtitles,
            input_files=input_files,
        )

    def create_plan(
        self,
        task_spec: TaskSpec,
        out_dir: Path,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
    ) -> ExecutionPlan:
        """Generate a sequential/parallel dependency DAG of PlanSteps."""
        steps: list[PlanStep] = []
        total_estimated_cost = 0.0
        step_counter = 1

        artifacts_dir = out_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Group input files by type
        audio_files = [f for f in task_spec.input_files if f.file_type == "audio"]
        doc_files = [f for f in task_spec.input_files if f.file_type in {"document", "image"}]

        last_text_step_id: str | None = None

        # -------------------------------------------------------------------
        # 1. OCR steps for documents/images
        # -------------------------------------------------------------------
        if doc_files:
            for df in doc_files:
                sid = f"step_{step_counter:02d}"
                step_counter += 1
                page_count = df.page_count or 1
                cost = page_count * PRICING.ocr_per_page_inr
                total_estimated_cost += cost

                out_art = str(artifacts_dir / f"ocr_{Path(df.filename).stem}.md")
                step = PlanStep(
                    step_id=sid,
                    description=f"Extract text and layout from document '{df.filename}' using Bodhan indic-ocr",
                    tool_name="extract_text_from_document",
                    tool_arguments={"file_path": df.path, "table_format": "markdown"},
                    dependencies=[],
                    expected_output_artifact=out_art,
                    estimated_cost_inr=round(cost, 4),
                )
                steps.append(step)
                last_text_step_id = sid

        # -------------------------------------------------------------------
        # 2. Transcription steps for audio files
        # -------------------------------------------------------------------
        if audio_files:
            for af in audio_files:
                sid = f"step_{step_counter:02d}"
                step_counter += 1
                dur = af.duration_seconds or 10.0
                cost = dur * PRICING.transcribe_per_second_inr
                total_estimated_cost += cost

                out_art = str(artifacts_dir / f"transcript_{Path(af.filename).stem}.txt")
                step = PlanStep(
                    step_id=sid,
                    description=f"Transcribe audio '{af.filename}' using Bodhan indic-transcribe",
                    tool_name="transcribe_audio",
                    tool_arguments={"file_path": af.path},
                    dependencies=[],
                    expected_output_artifact=out_art,
                    estimated_cost_inr=round(cost, 4),
                )
                steps.append(step)
                last_text_step_id = sid

        # -------------------------------------------------------------------
        # 3. Summarization step (English summary via LLM)
        # -------------------------------------------------------------------
        if task_spec.requires_summary and last_text_step_id:
            sid = f"step_{step_counter:02d}"
            step_counter += 1
            out_art = str(artifacts_dir / "summary.md")
            step = PlanStep(
                step_id=sid,
                description="Synthesize key points into a structured English summary using LLM",
                tool_name="llm_summarize",
                tool_arguments={"source_step_id": last_text_step_id, "focus": task_spec.user_prompt},
                dependencies=[last_text_step_id],
                expected_output_artifact=out_art,
                estimated_cost_inr=0.0,  # Companion LLM cost
            )
            steps.append(step)
            last_text_step_id = sid

        # -------------------------------------------------------------------
        # 4. Subtitle formatting step
        # -------------------------------------------------------------------
        if task_spec.requires_subtitles and last_text_step_id:
            sid = f"step_{step_counter:02d}"
            step_counter += 1
            out_art = str(artifacts_dir / "subtitles.srt")
            step = PlanStep(
                step_id=sid,
                description="Format timestamped segments into standard SRT subtitles",
                tool_name="format_subtitles",
                tool_arguments={"source_step_id": last_text_step_id},
                dependencies=[last_text_step_id],
                expected_output_artifact=out_art,
                estimated_cost_inr=0.0,
            )
            steps.append(step)

        # -------------------------------------------------------------------
        # 5. Translation steps
        # -------------------------------------------------------------------
        target_lang = task_spec.detected_target_languages[0] if task_spec.detected_target_languages else None
        # If user asked for translation or speech in a target language
        if target_lang and (task_spec.requires_translation or (task_spec.requires_speech and target_lang != "en")):
            source_id = last_text_step_id or "inline_prompt"
            sid = f"step_{step_counter:02d}"
            step_counter += 1
            est_cost = 0.05
            total_estimated_cost += est_cost

            out_art = str(artifacts_dir / f"translated_{target_lang}.md")
            step = PlanStep(
                step_id=sid,
                description=f"Translate text into '{target_lang}' using Bodhan indic-translate",
                tool_name="translate_text",
                tool_arguments={
                    "source_step_id": source_id,
                    "text": task_spec.user_prompt if not last_text_step_id else "",
                    "target_language": target_lang,
                    "script": "native",
                },
                dependencies=[last_text_step_id] if last_text_step_id else [],
                expected_output_artifact=out_art,
                estimated_cost_inr=est_cost,
            )
            steps.append(step)
            last_text_step_id = sid

        # -------------------------------------------------------------------
        # 6. Speech synthesis step
        # -------------------------------------------------------------------
        if task_spec.requires_speech and last_text_step_id:
            speech_lang = target_lang or "hi"
            sid = f"step_{step_counter:02d}"
            step_counter += 1

            # Detect female/male preference from prompt
            gender_pref = "male" if "male voice" in task_spec.user_prompt.lower() else "female"
            out_art = str(artifacts_dir / f"generated_speech_{speech_lang}.wav")

            # Approx 30s audio speech estimate
            est_cost = 30.0 * PRICING.speak_per_second_inr
            total_estimated_cost += est_cost

            step = PlanStep(
                step_id=sid,
                description=f"Synthesize speech in '{speech_lang}' ({gender_pref} voice) using Bodhan indic-speak",
                tool_name="synthesize_speech",
                tool_arguments={
                    "source_step_id": last_text_step_id,
                    "language": speech_lang,
                    "gender_preference": gender_pref,
                    "output_file_path": out_art,
                },
                dependencies=[last_text_step_id],
                expected_output_artifact=out_art,
                estimated_cost_inr=round(est_cost, 4),
            )
            steps.append(step)

        return ExecutionPlan(
            task_id=task_spec.task_id,
            steps=steps,
            estimated_total_cost_inr=round(total_estimated_cost, 4),
            max_steps=max_steps,
            max_cost_inr=max_cost_inr,
        )
