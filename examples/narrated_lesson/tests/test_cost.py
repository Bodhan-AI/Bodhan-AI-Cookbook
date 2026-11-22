"""Unit tests for Bodhan + Gemini cost estimates."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_lesson.cost import (
    BODHAN_OCR_INR_PER_PAGE,
    BODHAN_SPEAK_INR_PER_10K_CHARS,
    BODHAN_TRANSCRIBE_INR_PER_MINUTE,
    BODHAN_TRANSLATE_INR_PER_10K_COMPLETION,
    GEMINI_35_FLASH_INPUT_USD_PER_1M,
    GEMINI_35_FLASH_OUTPUT_USD_PER_1M,
    build_cost_report,
    estimate_bodhan_inr,
    estimate_llm_usd,
)


def test_estimate_bodhan_inr_math() -> None:
    meters = {
        "ocr_pages_ok": 2,
        "translate_completion_tokens": 10_000,
        "speak_chars": 10_000,
        "transcribe_audio_s": 60.0,
    }
    out = estimate_bodhan_inr(meters)
    assert out["ocr"] == round(2 * BODHAN_OCR_INR_PER_PAGE, 4)
    assert out["translate"] == round(BODHAN_TRANSLATE_INR_PER_10K_COMPLETION, 4)
    assert out["speak"] == round(BODHAN_SPEAK_INR_PER_10K_CHARS, 4)
    assert out["transcribe"] == round(BODHAN_TRANSCRIBE_INR_PER_MINUTE, 4)
    assert out["total"] == round(
        out["ocr"] + out["translate"] + out["speak"] + out["transcribe"], 4
    )


def test_estimate_gemini_35_flash_usd() -> None:
    meters = {
        "llm_prompt_tokens": 1_000_000,
        "llm_completion_tokens": 1_000_000,
        "llm_model": "gemini-3.5-flash",
    }
    out = estimate_llm_usd(meters)
    assert out["input_usd"] == GEMINI_35_FLASH_INPUT_USD_PER_1M
    assert out["output_usd"] == GEMINI_35_FLASH_OUTPUT_USD_PER_1M
    assert out["total_usd"] == round(
        GEMINI_35_FLASH_INPUT_USD_PER_1M + GEMINI_35_FLASH_OUTPUT_USD_PER_1M, 6
    )
    assert out["note"] is None


def test_build_cost_report_reads_speak_chars(tmp_path: Path) -> None:
    lang_dir = tmp_path / "hi"
    narr = lang_dir / "narration"
    narr.mkdir(parents=True)
    script = {
        "lang": "hi",
        "sections": [
            {
                "status": "ok",
                "speak_status": "ok",
                "text_for_tts": "abcd",  # 4 chars
                "duration_s": 30.0,
            }
        ],
    }
    script_path = lang_dir / "narration_script.hi.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")

    report = build_cost_report(
        out_dir=tmp_path,
        langs=["hi"],
        ocr_pages=[{"status": "ok"}, {"status": "ok"}],
        layout_pages=[
            {
                "status": "ok",
                "llm_usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            }
        ],
        translations=[
            {
                "lang": "hi",
                "status": "ok",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            }
        ],
        narrations=[
            {
                "lang": "hi",
                "status": "ok",
                "path": str(script_path),
                "llm_usage": {},
                "translate_usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 5,
                    "total_tokens": 5,
                },
            }
        ],
        spoken_plan={
            "llm_usage": {
                "prompt_tokens": 40,
                "completion_tokens": 10,
                "total_tokens": 50,
            },
            "pivot_usage": {},
        },
    )
    assert report["meters"]["ocr_pages_ok"] == 2
    assert report["meters"]["speak_chars"] == 4
    assert report["meters"]["transcribe_audio_s"] == 30.0
    assert report["meters"]["translate_completion_tokens"] == 25
    assert report["meters"]["llm_prompt_tokens"] == 140
    assert report["meters"]["llm_completion_tokens"] == 60
    assert report["bodhan_inr"]["currency"] == "INR"
    assert report["llm_usd"]["currency"] == "USD"
    assert "rates" in report
    assert report["bodhan_inr"]["ocr"] == round(2 * BODHAN_OCR_INR_PER_PAGE, 4)


def test_write_report_includes_cost(tmp_path: Path) -> None:
    from bodhan_lesson.cli import parse_args, write_report

    args = parse_args(["--input", "sample", "--langs", "hi", "--out", str(tmp_path)])
    path = write_report(
        tmp_path,
        args,
        ["hi"],
        page_count=1,
        ocr_pages=[{"page": "page_001.png", "status": "ok", "error": None}],
        layout_pages=[],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "cost" in data
    assert "bodhan_inr" in data["cost"]
    assert "llm_usd" in data["cost"]
    assert data["cost"]["meters"]["ocr_pages_ok"] == 1
