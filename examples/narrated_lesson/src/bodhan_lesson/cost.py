"""Estimate run cost for Bodhan APIs and the companion LLM (Gemini).

Rates are snapshots of public list prices — not an invoice. Update constants
when providers change pricing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# --- Bodhan console list prices (INR) ---
# https://console.bodhan.ai — OCR per image; MT per 10K output tokens;
# Speak per 10K input characters; Transcribe per audio minute.
BODHAN_OCR_INR_PER_PAGE = 0.20
BODHAN_TRANSLATE_INR_PER_10K_COMPLETION = 0.20
BODHAN_SPEAK_INR_PER_10K_CHARS = 6.0
BODHAN_TRANSCRIBE_INR_PER_MINUTE = 0.10

# --- Gemini 3.5 Flash (Developer / Global standard) ---
# Google Cloud Model Garden / Gemini API pricing (USD per 1M tokens):
# Input $1.50 / 1M; Output (response + reasoning) $9.00 / 1M.
# https://cloud.google.com/model-garden/pricing
# https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash
GEMINI_35_FLASH_INPUT_USD_PER_1M = 1.50
GEMINI_35_FLASH_OUTPUT_USD_PER_1M = 9.00

RATES_NOTE = (
    "Estimates from published list prices; not a billing invoice. "
    "Bodhan Speak is character-metered; Transcribe is audio-minute-metered. "
    "Gemini output rate includes reasoning/thinking tokens when the API "
    "reports them under completion_tokens."
)


def get_llm_model_name() -> str:
    return os.environ.get("LLM_MODEL", "gemini-3.5-flash")


def _sum_usage(usages: list[dict[str, Any]]) -> dict[str, int]:
    out = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        for key in out:
            out[key] += int(usage.get(key) or 0)
    return out


def _load_script(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def speak_chars_and_audio_s(
    out_dir: Path,
    langs: list[str],
    narrations: list[dict[str, Any]],
) -> tuple[int, float]:
    """Sum TTS characters and section audio seconds from narration scripts."""
    by_path = {
        row.get("lang"): Path(row["path"])
        for row in narrations
        if row.get("lang") and row.get("path")
    }
    chars = 0
    audio_s = 0.0
    for lang in langs:
        path = by_path.get(lang) or (out_dir / lang / f"narration_script.{lang}.json")
        script = _load_script(path if isinstance(path, Path) else None)
        if not script:
            continue
        for section in script.get("sections") or []:
            text = section.get("text_for_tts") or ""
            if section.get("speak_status") == "ok" or section.get("status") == "ok":
                chars += len(str(text))
            dur = section.get("duration_s")
            if dur is not None:
                try:
                    audio_s += float(dur)
                except (TypeError, ValueError):
                    pass
            elif section.get("qa") and (section.get("qa") or {}).get("transcript"):
                # QA ran; prefer duration if present only — already handled.
                pass
    return chars, audio_s


def collect_meters(
    *,
    out_dir: Path,
    langs: list[str],
    ocr_pages: list[dict[str, Any]],
    layout_pages: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    narrations: list[dict[str, Any]],
    spoken_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gather usage meters from pipeline stage rows and scripts on disk."""
    spoken_plan = spoken_plan or {}
    ocr_pages_ok = sum(1 for row in ocr_pages if row.get("status") == "ok")

    translate_usage = _sum_usage(
        [row.get("usage") or {} for row in translations]
    )
    narrate_translate = _sum_usage(
        [row.get("translate_usage") or {} for row in narrations]
    )
    plan_pivot = spoken_plan.get("pivot_usage") or {}
    mt_completion = (
        translate_usage["completion_tokens"]
        + narrate_translate["completion_tokens"]
        + int(plan_pivot.get("completion_tokens") or 0)
    )
    mt_prompt = (
        translate_usage["prompt_tokens"]
        + narrate_translate["prompt_tokens"]
        + int(plan_pivot.get("prompt_tokens") or 0)
    )

    layout_llm = _sum_usage([row.get("llm_usage") or {} for row in layout_pages])
    plan_llm = spoken_plan.get("llm_usage") or {}
    narrate_llm = _sum_usage([row.get("llm_usage") or {} for row in narrations])
    llm_prompt = (
        layout_llm["prompt_tokens"]
        + int(plan_llm.get("prompt_tokens") or 0)
        + narrate_llm["prompt_tokens"]
    )
    llm_completion = (
        layout_llm["completion_tokens"]
        + int(plan_llm.get("completion_tokens") or 0)
        + narrate_llm["completion_tokens"]
    )

    speak_chars, audio_s = speak_chars_and_audio_s(out_dir, langs, narrations)

    return {
        "ocr_pages_ok": ocr_pages_ok,
        "translate_completion_tokens": mt_completion,
        "translate_prompt_tokens": mt_prompt,
        "speak_chars": speak_chars,
        "transcribe_audio_s": round(audio_s, 3),
        "llm_prompt_tokens": llm_prompt,
        "llm_completion_tokens": llm_completion,
        "llm_model": get_llm_model_name(),
    }


def estimate_bodhan_inr(meters: dict[str, Any]) -> dict[str, Any]:
    pages = int(meters.get("ocr_pages_ok") or 0)
    completion = int(meters.get("translate_completion_tokens") or 0)
    chars = int(meters.get("speak_chars") or 0)
    audio_s = float(meters.get("transcribe_audio_s") or 0.0)
    audio_min = audio_s / 60.0

    ocr = round(pages * BODHAN_OCR_INR_PER_PAGE, 4)
    translate = round(
        (completion / 10_000.0) * BODHAN_TRANSLATE_INR_PER_10K_COMPLETION, 4
    )
    speak = round((chars / 10_000.0) * BODHAN_SPEAK_INR_PER_10K_CHARS, 4)
    transcribe = round(audio_min * BODHAN_TRANSCRIBE_INR_PER_MINUTE, 4)
    total = round(ocr + translate + speak + transcribe, 4)
    return {
        "currency": "INR",
        "ocr": ocr,
        "translate": translate,
        "speak": speak,
        "transcribe": transcribe,
        "total": total,
    }


def estimate_llm_usd(meters: dict[str, Any]) -> dict[str, Any]:
    prompt = int(meters.get("llm_prompt_tokens") or 0)
    completion = int(meters.get("llm_completion_tokens") or 0)
    model = str(meters.get("llm_model") or get_llm_model_name())
    # Default table is Gemini 3.5 Flash; other models still use these rates
    # with an explicit note so the estimate is never silent.
    in_rate = GEMINI_35_FLASH_INPUT_USD_PER_1M
    out_rate = GEMINI_35_FLASH_OUTPUT_USD_PER_1M
    rate_model = "gemini-3.5-flash"
    note = None
    if "3.5-flash" not in model and "gemini-3.5-flash" not in model:
        note = (
            f"LLM_MODEL={model}; priced with {rate_model} list rates "
            f"(${in_rate}/1M in, ${out_rate}/1M out). Adjust if your model differs."
        )

    input_usd = (prompt / 1_000_000.0) * in_rate
    output_usd = (completion / 1_000_000.0) * out_rate
    total = round(input_usd + output_usd, 6)
    return {
        "currency": "USD",
        "model": model,
        "priced_as": rate_model,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "input_usd": round(input_usd, 6),
        "output_usd": round(output_usd, 6),
        "total_usd": total,
        "note": note,
    }


def build_cost_report(
    *,
    out_dir: Path,
    langs: list[str],
    ocr_pages: list[dict[str, Any]],
    layout_pages: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    narrations: list[dict[str, Any]],
    spoken_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full cost block for report.json."""
    meters = collect_meters(
        out_dir=out_dir,
        langs=langs,
        ocr_pages=ocr_pages,
        layout_pages=layout_pages,
        translations=translations,
        narrations=narrations,
        spoken_plan=spoken_plan,
    )
    bodhan = estimate_bodhan_inr(meters)
    llm = estimate_llm_usd(meters)
    return {
        "note": RATES_NOTE,
        "rates": {
            "bodhan": {
                "ocr_inr_per_page": BODHAN_OCR_INR_PER_PAGE,
                "translate_inr_per_10k_completion_tokens": (
                    BODHAN_TRANSLATE_INR_PER_10K_COMPLETION
                ),
                "speak_inr_per_10k_chars": BODHAN_SPEAK_INR_PER_10K_CHARS,
                "transcribe_inr_per_audio_minute": BODHAN_TRANSCRIBE_INR_PER_MINUTE,
            },
            "llm": {
                "model_priced_as": "gemini-3.5-flash",
                "input_usd_per_1m_tokens": GEMINI_35_FLASH_INPUT_USD_PER_1M,
                "output_usd_per_1m_tokens": GEMINI_35_FLASH_OUTPUT_USD_PER_1M,
                "source": (
                    "Google Model Garden / Gemini API list price "
                    "(output includes reasoning tokens)"
                ),
            },
        },
        "meters": meters,
        "bodhan_inr": bodhan,
        "llm_usd": llm,
    }
