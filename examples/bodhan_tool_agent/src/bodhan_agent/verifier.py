"""
Output verification: checks translations and generated audio before the task ends.

Verification uses only Bodhan models (no other systems).
  - Translation: back-translate to source and ask the LLM to judge coverage.
  - Audio (TTS): back-transcribe with Indic-Transcribe and compute character match rate.
"""
from __future__ import annotations

from pathlib import Path

from openai import AsyncOpenAI
from rapidfuzz.distance import Levenshtein

from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text

from .llm_client import llm_json
from .sanitizer import SYSTEM_GUARD, wrap_external

_VERIFY_SYSTEM = f"""{SYSTEM_GUARD}

You are a translation quality verifier. You will be given:
1. The original source text.
2. A back-translation (the target-language translation translated back to the source language).

Decide whether the translation covers the source content faithfully.
Respond with JSON only:
{{"ok": true/false, "issues": ["list of specific problems, or empty if ok"]}}
"""

# Character match threshold: flag if back-transcription differs more than this
_AUDIO_CHAR_MATCH_MIN = 0.70


async def verify_translation(
    source_text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str,
    llm_client: AsyncOpenAI,
    llm_model: str,
) -> dict:
    """Back-translate and ask the LLM to judge coverage.

    Returns:
        {"ok": bool, "issues": list[str], "cost_inr": float}
    """
    back = await translate_text(translated_text, target_lang, source_lang)
    back_text = back["translated_text"]

    user = (
        f"Original ({source_lang}):\n"
        + wrap_external(source_text, "original")
        + f"\n\nBack-translation ({source_lang} from {target_lang}):\n"
        + wrap_external(back_text, "back-translation")
    )

    try:
        verdict = await llm_json(llm_client, llm_model, _VERIFY_SYSTEM, user)
    except Exception as exc:
        verdict = {"ok": False, "issues": [f"Verifier LLM call failed: {exc}"]}

    return {
        "ok": verdict.get("ok", False),
        "issues": verdict.get("issues", []),
        "cost_inr": round(back["cost_inr"], 4),
    }


async def verify_audio(audio_path: Path, expected_text: str, language: str) -> dict:
    """Back-transcribe TTS audio and check character match against the input text.

    Returns:
        {"ok": bool, "char_match_rate": float, "warning": str | None, "cost_inr": float}
    """
    result = await transcribe_audio(audio_path, language=language)
    got = result["text"].strip()
    expected = expected_text.strip()

    # Normalised edit distance → character match rate
    max_len = max(len(got), len(expected), 1)
    edit_dist = Levenshtein.distance(got, expected)
    match_rate = 1.0 - edit_dist / max_len

    ok = match_rate >= _AUDIO_CHAR_MATCH_MIN
    warning = None if ok else (
        f"Audio match rate {match_rate:.0%} is below threshold {_AUDIO_CHAR_MATCH_MIN:.0%}. "
        "Possible omission or repetition."
    )

    return {
        "ok": ok,
        "char_match_rate": round(match_rate, 3),
        "warning": warning,
        "cost_inr": round(result["cost_inr"], 4),
    }
