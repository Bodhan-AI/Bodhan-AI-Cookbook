"""
LLM-based task planner.

Given the task description and a list of files (with their types), the planner
asks the LLM to produce a JSON execution plan: an ordered list of tool calls
with their arguments, purpose, and estimated cost. If the task is impossible
(unsupported language, missing capability, etc.) the plan flags it and the
agent stops without producing any output.
"""
from __future__ import annotations

from pathlib import Path

from openai import AsyncOpenAI

from bodhan_mcp.clients.bodhan_client import (
    COST_OCR_PER_IMAGE,
    COST_SPEAK_PER_10K_CHARS,
    COST_TRANSCRIBE_PER_MINUTE,
    COST_TRANSLATE_PER_10K_TOKENS,
)
from bodhan_mcp.tools.constants import SUPPORTED_LANGUAGES

from .llm_client import llm_json
from .sanitizer import SYSTEM_GUARD

_TOOL_DESCRIPTIONS = """
Available tools (call them by name in the plan):

transcribe(audio_path, language=None)
  → {text, language, segments, cost_inr}
  Transcribes any-length audio. Handles 30s windowing internally.
  Use for: STT, language detection from audio, back-transcription of TTS.

translate(text, source_lang, target_lang)
  → {translated_text, source_lang, target_lang, cost_inr}
  Translates text. Supported target languages: {langs}.
  text: The text to translate, or the file reference to a text file (e.g. "{files[0]}").
  Returns an error dict (no translated_text) for unsupported languages.

speak(text, language, out_dir, voice=None)
  → {audio_path, duration_seconds, cost_inr, warning}
  Synthesizes speech. Same supported languages as translate.
  text: The text to read aloud, or a previous translation (e.g. "{s1.translated_text}"),
        or a text file ("{files[0]}").
  voice: 'female' (default), 'male', or a specific voice ID.

ocr(file_path)
  → {pages, full_text, flagged_pages, cost_inr}
  Extracts text from an image or scanned PDF page-by-page.

language_detect(text)
  → {language, script, confidence}
  Offline script-based language detection for text. Free.

audio_chunk(audio_path, max_seconds=28.0)
  → {chunks, count}
  Splits audio at silence. transcribe() calls this internally.

media_info(file_path)
  → {type, format, duration_seconds, size_bytes, path}
  Inspect a file without loading it into memory.

summarize(text, target_duration_seconds=120)
  → {summary_text, word_count}
  Summarizes English text into a concise summary suitable for speech synthesis.
  Use for: creating summaries of long documents/chapters before translating and narrating.

subtitles(text, out_path, segments=None)
  → {srt_path, subtitle_count}
  Formats text or segments into an SRT subtitle file.
  Use for: generating subtitle files (.srt).

NOTE: Plain text files (.txt, .md) passed as input files can be passed directly
as '{files[0]}' to translate or speak; their content is loaded automatically.
""".strip()

_PRICING = f"""
Pricing (INR):
  Indic-Transcribe: ₹{COST_TRANSCRIBE_PER_MINUTE}/audio minute
  Indic-Translate:  ₹{COST_TRANSLATE_PER_10K_TOKENS}/10K output tokens
  Indic-Speak:      ₹{COST_SPEAK_PER_10K_CHARS}/10K input characters
  Indic-OCR:        ₹{COST_OCR_PER_IMAGE}/image (₹{COST_OCR_PER_IMAGE * 10}/10 pages)
""".strip()

_SYSTEM = f"""{SYSTEM_GUARD}

You are a planning agent for Bodhan's Indic-language AI tools.
Given a task and a list of input files, produce a JSON execution plan.

{_TOOL_DESCRIPTIONS}

{_PRICING}

RULES:
1. Indian-language text must come from Indic-Translate. Never generate it yourself.
2. Never transcribe audio yourself. Always use the transcribe tool.
3. Never read text from images yourself. Always use the ocr tool.
4. If the task requires a language not in the supported list, set impossible=true.
5. If a required file is missing, set impossible=true.
6. Use media_info first if file types are unclear.
7. Each step's args may reference previous step outputs as "{{step_id.field}}"
   and input files as "{{files[0]}}", "{{files[1]}}", etc.
8. Estimate cost_inr for each step using the pricing above.

OUTPUT FORMAT (JSON only, no other text):
{{
  "impossible": false,
  "impossible_reason": null,
  "assumptions": ["list of assumptions you are making"],
  "steps": [
    {{
      "id": "s1",
      "tool": "tool_name",
      "args": {{"arg": "value or {{ref}}"}},
      "purpose": "one sentence explaining why this step is needed",
      "estimated_cost_inr": 0.1
    }}
  ]
}}
"""


async def plan_task(
    task: str,
    files: list[Path],
    llm_client: AsyncOpenAI,
    llm_model: str,
    budget_inr: float,
) -> dict:
    """Ask the LLM to produce an execution plan for the given task.

    Returns the parsed plan dict. Never raises - if the LLM call fails,
    returns an impossible plan with the error as the reason.
    """
    file_list = "\n".join(f"  [{i}] {p} (exists: {p.exists()})" for i, p in enumerate(files))
    system = _SYSTEM.replace("{langs}", ", ".join(SUPPORTED_LANGUAGES))
    user = (
        f"Task: {task}\n\n"
        f"Input files:\n{file_list}\n\n"
        f"Budget: ₹{budget_inr} total. Keep estimated costs within this budget."
    )

    try:
        plan = await llm_json(llm_client, llm_model, system, user)
    except Exception as exc:
        return {
            "impossible": True,
            "impossible_reason": f"Planning failed: {exc}",
            "assumptions": [],
            "steps": [],
        }

    # Ensure required keys exist
    plan.setdefault("impossible", False)
    plan.setdefault("impossible_reason", None)
    plan.setdefault("assumptions", [])
    plan.setdefault("steps", [])
    return plan
