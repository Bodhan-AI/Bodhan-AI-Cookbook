"""
LLM Planner and decision maker for Bodhan Task Agent.
Uses an OpenAI-compatible client configured from environment variables.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

try:
    from openai import AsyncOpenAI
    _HAVE_OPENAI = True
except ImportError:
    _HAVE_OPENAI = False
    AsyncOpenAI = None  # type: ignore

from bodhan_agent.guard import assert_safe
from bodhan_core.langs import TRANSLATE_LANGS, TRANSCRIBE_LANGS, SPEAK_LANGS

log = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.txt"


def _load_system_prompt() -> str:
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return "You are an agent planner for Bodhan AI Indic language tools."


class Planner:
    """Plans steps, validates feasibility, and generates English summaries."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        if _HAVE_OPENAI and self.api_key:
            self.client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
        else:
            self.client = None

    async def plan(self, task: str, file_metadata: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Produce an ordered execution plan based on the task description and file metadata.
        Falls back to deterministic recipes if LLM is unavailable or unconfigured.
        """
        # 1. Quick check for known unsupported languages / impossible tasks
        task_lower = task.lower()
        if "french" in task_lower or "german" in task_lower or "spanish" in task_lower or "chinese" in task_lower:
            return {
                "feasible": False,
                "refusal_reason": (
                    "Task requested an unsupported foreign language (e.g. French). "
                    "Bodhan models only support 25 official Indian languages and English."
                ),
                "steps": [],
                "assumptions": [],
            }

        if not file_metadata and ("file" in task_lower or "this" in task_lower or "clip" in task_lower or "pdf" in task_lower):
            return {
                "feasible": False,
                "refusal_reason": "No input files provided for a task requiring input files.",
                "steps": [],
                "assumptions": [],
            }

        # 2. Try LLM planning
        if self.client is not None:
            try:
                system_prompt = _load_system_prompt()
                user_msg = (
                    f"User Task: {task}\n\n"
                    f"Input Files Metadata:\n{json.dumps(file_metadata, indent=2)}\n\n"
                    "Respond with a JSON object with this exact structure:\n"
                    "{\n"
                    '  "feasible": true,\n'
                    '  "refusal_reason": null,\n'
                    '  "assumptions": ["assumption 1"],\n'
                    '  "steps": [\n'
                    '    {"action": "tool_call", "tool": "tool_name", "args": {...}, "description": "step description"}\n'
                    "  ]\n"
                    "}\n"
                    "If the task cannot be done, set feasible to false and give refusal_reason."
                )

                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                content = resp.choices[0].message.content or "{}"
                plan = json.loads(content)
                if "steps" in plan or not plan.get("feasible", True):
                    return plan
            except Exception as exc:
                log.warning("LLM planning call failed: %s; using deterministic recipe fallback", exc)

        # 3. Deterministic recipe fallback for common task shapes
        return self._deterministic_plan(task, file_metadata)

    def _deterministic_plan(self, task: str, file_metadata: list[dict[str, Any]]) -> dict[str, Any]:
        """Deterministic task-shape templates for standard multi-step patterns."""
        t_low = task.lower()
        steps: list[dict[str, Any]] = []
        assumptions: list[str] = []

        # Find target language if mentioned
        target_lang = "en"
        for code in sorted(TRANSLATE_LANGS, key=len, reverse=True):
            if code in t_low:
                target_lang = code
                break
        if "tamil" in t_low:
            target_lang = "ta"
        elif "hindi" in t_low:
            target_lang = "hi"
        elif "marathi" in t_low:
            target_lang = "mr"
        elif "telugu" in t_low:
            target_lang = "te"
        elif "bengali" in t_low:
            target_lang = "bn"
        elif "english" in t_low:
            target_lang = "en"

        # Select matching file from file_metadata based on task intent
        first_file = ""
        first_type = ""
        if any(w in t_low for w in ["audio", "voice", "clip", "wav", "listen", "subtitles", "srt"]):
            for fm in file_metadata:
                ft = fm.get("metadata", {}).get("file_type", "")
                fp = fm.get("path", "")
                if ft in {"audio", "video"} or fp.endswith((".wav", ".mp3", ".m4a", ".mp4")):
                    first_file = fp
                    first_type = ft
                    break
        elif any(w in t_low for w in ["pdf", "scan", "page", "image", "photo"]):
            for fm in file_metadata:
                ft = fm.get("metadata", {}).get("file_type", "")
                fp = fm.get("path", "")
                if ft in {"pdf", "image"} or fp.endswith((".pdf", ".png", ".jpg", ".jpeg")):
                    first_file = fp
                    first_type = ft
                    break
        elif any(w in t_low for w in ["note", "text", "paragraph", "worksheet"]):
            for fm in file_metadata:
                ft = fm.get("metadata", {}).get("file_type", "")
                fp = fm.get("path", "")
                if ft == "text" or fp.endswith((".txt", ".md")):
                    first_file = fp
                    first_type = ft
                    break

        if not first_file and file_metadata:
            first_file = file_metadata[0]["path"]
            first_type = file_metadata[0].get("metadata", {}).get("file_type", "")

        # Pattern 1: Scanned PDF / Document -> Summary or Translation
        if first_type == "pdf" or first_file.endswith(".pdf") or "pdf" in t_low or "worksheet" in t_low or first_type == "image":
            steps.append({
                "action": "tool_call",
                "tool": "ocr_document",
                "args": {"file_path": first_file, "table_format": "markdown"},
                "description": "Extract text from document/image using Indic-OCR",
            })
            if "summary" in t_low or "audio" in t_low or "mp3" in t_low or "read" in t_low:
                assumptions.append(f"Target language for summary/audio is {target_lang}")
                steps.append({
                    "action": "tool_call",
                    "tool": "translate_text",
                    "args": {"file_path": "{step_0_output}", "target_lang": target_lang},
                    "description": f"Translate extracted text into {target_lang}",
                })
                if "audio" in t_low or "summary" in t_low or "mp3" in t_low:
                    steps.append({
                        "action": "tool_call",
                        "tool": "synthesize_speech",
                        "args": {"text": "{step_1_output}", "language": target_lang},
                        "description": f"Generate spoken audio in {target_lang}",
                    })
            elif "translate" in t_low or target_lang != "en":
                steps.append({
                    "action": "tool_call",
                    "tool": "translate_text",
                    "args": {"file_path": "{step_0_output}", "target_lang": target_lang},
                    "description": f"Translate extracted text to {target_lang}",
                })
            return {"feasible": True, "steps": steps, "assumptions": assumptions}

        # Pattern 2: Audio -> Subtitles or Translation
        if first_type in {"audio", "video"} or any(first_file.endswith(ext) for ext in [".wav", ".mp3", ".m4a", ".mp4"]):
            fmt = "srt" if ("subtitle" in t_low or "srt" in t_low) else "text"
            steps.append({
                "action": "tool_call",
                "tool": "transcribe_audio",
                "args": {"file_path": first_file, "output_format": fmt},
                "description": f"Transcribe audio input to {fmt}",
            })
            if "translate" in t_low or target_lang != "en":
                steps.append({
                    "action": "tool_call",
                    "tool": "translate_text",
                    "args": {"file_path": "{step_0_output}", "target_lang": target_lang},
                    "description": f"Translate transcript to {target_lang}",
                })
            return {"feasible": True, "steps": steps, "assumptions": assumptions}

        # Pattern 3: Text file -> Translate or Read aloud
        if first_type == "text" or first_file.endswith((".txt", ".md")):
            if "translate" in t_low or (target_lang != "en" and not any(w in t_low for w in ["read", "aloud", "speak"])):
                steps.append({
                    "action": "tool_call",
                    "tool": "translate_text",
                    "args": {"file_path": first_file, "target_lang": target_lang},
                    "description": f"Translate text document into {target_lang}",
                })
                if any(w in t_low for w in ["audio", "read", "aloud", "speak"]):
                    steps.append({
                        "action": "tool_call",
                        "tool": "synthesize_speech",
                        "args": {"text": "{step_0_output}", "language": target_lang},
                        "description": f"Read translated text aloud in {target_lang}",
                    })
                return {"feasible": True, "steps": steps, "assumptions": assumptions}

            gender = "female" if "female" in t_low else ("male" if "male" in t_low else "female")
            steps.append({
                "action": "tool_call",
                "tool": "synthesize_speech",
                "args": {"text": "{file_content}", "file_path": first_file, "language": target_lang, "gender": gender},
                "description": f"Synthesize text into {target_lang} speech ({gender} voice)",
            })
            return {"feasible": True, "steps": steps, "assumptions": assumptions}

        # Generic 1-step inspection fallback
        if first_file:
            steps.append({
                "action": "tool_call",
                "tool": "inspect_media",
                "args": {"file_path": first_file},
                "description": "Inspect input file properties",
            })

        return {
            "feasible": True,
            "steps": steps,
            "assumptions": ["Executed generic inspection recipe"],
        }

    async def summarize(
        self,
        task: str,
        completed_steps: list[dict[str, Any]],
        artifacts: list[str],
    ) -> str:
        """
        Produce a concise English summary of the task execution and generated artifacts.
        Strictly guarded against emitting any Indic text.
        """
        if self.client is not None:
            try:
                user_msg = (
                    f"Original Task: {task}\n\n"
                    f"Completed Steps: {json.dumps(completed_steps)}\n\n"
                    f"Artifacts Generated: {artifacts}\n\n"
                    "Write a professional English summary in 2-4 sentences explaining what was accomplished. "
                    "Do NOT include or generate any Indian-language text."
                )
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a concise English reporter. Never write Indian language text."},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.3,
                )
                summary = (resp.choices[0].message.content or "").strip()
                assert_safe(summary, context="summarize")
                return summary
            except Exception as exc:
                log.warning("LLM summarize call failed: %s; using template fallback", exc)

        # Template-based safe English fallback
        artifact_list = ", ".join(f"`{Path(a).name}`" for a in artifacts) if artifacts else "None"
        return (
            f"Successfully executed task: '{task}'. "
            f"Completed {len(completed_steps)} step(s) using Bodhan tools. "
            f"Generated artifacts: {artifact_list}."
        )
