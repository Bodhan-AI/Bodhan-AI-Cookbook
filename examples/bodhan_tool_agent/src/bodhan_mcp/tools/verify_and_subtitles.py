"""Verification and subtitle generation MCP tools (verify_speech, verify_translation, make_subtitles)."""

from __future__ import annotations

import contextlib
import json
import re
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..client.client import BodhanClient, TRANSLATE_LANGUAGES, normalize_language_code
from .transcribe import bodhan_transcribe, _format_srt_time
from .translate import bodhan_translate

# Expected Unicode script ranges for native-script validation
SCRIPT_RANGES: Dict[str, Tuple[int, int, str]] = {
    "hi": (0x0900, 0x097F, "Devanagari"),
    "mr": (0x0900, 0x097F, "Devanagari"),
    "sa": (0x0900, 0x097F, "Devanagari"),
    "ne": (0x0900, 0x097F, "Devanagari"),
    "kok": (0x0900, 0x097F, "Devanagari"),
    "mai": (0x0900, 0x097F, "Devanagari"),
    "doi": (0x0900, 0x097F, "Devanagari"),
    "brx": (0x0900, 0x097F, "Devanagari"),
    "ks-Deva": (0x0900, 0x097F, "Devanagari"),
    "sd": (0x0900, 0x097F, "Devanagari"),
    "bn": (0x0980, 0x09FF, "Bengali"),
    "as": (0x0980, 0x09FF, "Bengali/Assamese"),
    "mni-Beng": (0x0980, 0x09FF, "Bengali"),
    "pa": (0x0A00, 0x0A7F, "Gurmukhi"),
    "gu": (0x0A80, 0x0AFF, "Gujarati"),
    "or": (0x0B00, 0x0B7F, "Odia"),
    "ta": (0x0B80, 0x0BFF, "Tamil"),
    "te": (0x0C00, 0x0C7F, "Telugu"),
    "kn": (0x0C80, 0x0CFF, "Kannada"),
    "ml": (0x0D00, 0x0D7F, "Malayalam"),
    "sat": (0x1C50, 0x1C7F, "Ol Chiki"),
    "mni": (0xABC0, 0xABFF, "Meetei Mayek"),
    "ur": (0x0600, 0x06FF, "Perso-Arabic"),
    "ks": (0x0600, 0x06FF, "Perso-Arabic"),
    "sd-Arab": (0x0600, 0x06FF, "Perso-Arabic"),
    "en": (0x0041, 0x007A, "Latin"),
}

_INDIC_DIGIT_TRANS = str.maketrans(
    "०१२३४५६७८९"
    "০১২৩৪৫৬৭৮৯"
    "੦੧੨੩੪੫੬੭੮੯"
    "૦૧૨૩૪૫૬૭૮૯"
    "୦୧୨୩୪୫୬୭୮୯"
    "௦௧௨௩௪௫௬௭௮௯"
    "౦౧౨౩౪౫౬౭౮౯"
    "೦೧೨೩೪೫೬೭೮೯"
    "൦൧൨൩൪൫൬൭൮൯",
    "0123456789" * 9,
)


def bodhan_verify_speech(
    audio_file: str,
    expected_text: str,
    language: Optional[str] = None,
    client: Optional[BodhanClient] = None,
) -> Dict[str, Any]:
    """Back-transcribe an audio file via Indic-Transcribe and compare with expected text."""
    path = Path(audio_file).resolve()
    if not path.exists():
        return {"passed": False, "reason": f"Audio file not found: {audio_file}", "cost_inr": 0.0}

    duration = 0.0
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as wf:
            rate = wf.getframerate()
            duration = round(wf.getnframes() / float(rate), 2) if rate > 0 else 0.0
    except Exception as exc:
        return {"passed": False, "reason": f"Invalid WAV file: {exc}", "cost_inr": 0.0}

    if duration < 0.2:
        return {
            "passed": False,
            "duration_seconds": duration,
            "reason": f"Audio is silent or nearly empty ({duration}s).",
            "cost_inr": 0.0,
        }

    b_client = client or BodhanClient()
    tr = bodhan_transcribe(str(path), language=language, client=b_client)
    back_text = tr.text.strip()
    if not back_text:
        return {
            "passed": False,
            "duration_seconds": duration,
            "back_transcript": "",
            "reason": "Back-transcription returned empty text.",
            "cost_inr": tr.cost_inr,
        }

    exp_words = [w for w in re.findall(r"\w+", expected_text.lower()) if len(w) > 1]
    got_words = [w for w in re.findall(r"\w+", back_text.lower()) if len(w) > 1]
    exp_set = set(exp_words)
    got_set = set(got_words)
    overlap_ratio = round(len(exp_set & got_set) / max(1, len(exp_set)), 3)

    # Check for severe repetition, omission, or completely unrelated words
    len_ratio = round(len(back_text) / max(1, len(expected_text.strip())), 2)
    issues: List[str] = []
    if not b_client.mock_mode:
        if len_ratio > 2.2:
            issues.append(f"Possible repetition detected (length ratio {len_ratio}x)")
        if len_ratio < 0.35:
            issues.append(f"Possible omission detected (length ratio {len_ratio}x)")
        if len(exp_set) >= 3 and overlap_ratio < 0.2:
            issues.append(f"Low word overlap with expected text (overlap={overlap_ratio})")

    passed = len(issues) == 0
    return {
        "passed": passed,
        "duration_seconds": duration,
        "back_transcript": back_text,
        "word_overlap_ratio": overlap_ratio,
        "length_ratio": len_ratio,
        "issues": issues,
        "cost_inr": tr.cost_inr,
    }


def bodhan_verify_translation(
    source_text: str,
    translated_text: str,
    target_language: str,
    script: str = "native",
    back_translate: bool = False,
    client: Optional[BodhanClient] = None,
) -> Dict[str, Any]:
    """Verify translation quality: script match, numbers, Markdown structure, length ratio, and optional back-translation."""
    target_lang = normalize_language_code(target_language)
    issues: List[str] = []
    cost_inr = 0.0

    if not translated_text or not translated_text.strip():
        return {"passed": False, "issues": ["Translation is empty."], "cost_inr": 0.0}

    if target_lang not in TRANSLATE_LANGUAGES:
        return {"passed": False, "issues": [f"Target language '{target_language}' is not supported by Indic-Translate."], "cost_inr": 0.0}

    # 1. Unicode script check (require meaningful target-script presence, not just 1 char)
    if script == "native" and target_lang in SCRIPT_RANGES:
        lo, hi, script_name = SCRIPT_RANGES[target_lang]
        alpha_chars = [ch for ch in translated_text if ch.isalpha()]
        matching = sum(1 for ch in alpha_chars if lo <= ord(ch) <= hi)
        if matching == 0:
            issues.append(f"Script mismatch: expected {script_name} characters for '{target_lang}', found 0.")
        elif len(alpha_chars) >= 10 and (matching / len(alpha_chars)) < 0.25:
            issues.append(
                f"Low target script ratio: only {matching}/{len(alpha_chars)} alphabetic chars "
                f"are {script_name} for target '{target_lang}'."
            )

    # 2. Length ratio check
    src_len = max(1, len(source_text.strip()))
    tgt_len = len(translated_text.strip())
    length_ratio = round(tgt_len / src_len, 2)
    if src_len > 100 and tgt_len < 10:
        issues.append(f"Severe truncation detected (source={src_len} chars, target={tgt_len} chars).")

    # 3. Numbers preservation check (normalize Indic digits on BOTH source and target)
    norm_src = source_text.translate(_INDIC_DIGIT_TRANS)
    norm_tgt = translated_text.translate(_INDIC_DIGIT_TRANS)
    src_nums = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", norm_src))
    if src_nums:
        missing_nums = [n for n in sorted(src_nums) if n not in norm_tgt]
        if missing_nums and len(missing_nums) > len(src_nums) // 2:
            issues.append(f"Missing numeric values in translation: {', '.join(missing_nums[:5])}")

    # 4. Markdown table structure preservation check
    src_table_rows = [l for l in source_text.splitlines() if l.strip().startswith("|") and l.strip().endswith("|")]
    if len(src_table_rows) >= 2:
        tgt_table_rows = [l for l in translated_text.splitlines() if l.strip().startswith("|") and l.strip().endswith("|")]
        if len(tgt_table_rows) < 2:
            issues.append(f"Markdown table structure lost (source had {len(src_table_rows)} table rows, target has {len(tgt_table_rows)}).")

    # 5. Optional paid back-translation to English
    back_translation = None
    if back_translate and target_lang != "en":
        b_client = client or BodhanClient()
        bt = bodhan_translate(text=translated_text, target_language="en", client=b_client)
        back_translation = bt.translation
        cost_inr = bt.cost_inr

    return {
        "passed": len(issues) == 0,
        "target_language": target_lang,
        "length_ratio": length_ratio,
        "issues": issues,
        "back_translation_en": back_translation,
        "cost_inr": cost_inr,
    }


def _wrap_subtitle_lines(text: str, max_chars_per_line: int = 42, max_lines: int = 2) -> List[str]:
    """Split text into subtitle cues of at most `max_lines` lines x `max_chars_per_line` chars."""
    words = text.strip().split()
    if not words:
        return []

    cues: List[str] = []
    current_lines: List[str] = []
    current_line = ""

    for word in words:
        candidate = f"{current_line} {word}".strip() if current_line else word
        if len(candidate) <= max_chars_per_line:
            current_line = candidate
        else:
            if current_line:
                current_lines.append(current_line)
            if len(current_lines) >= max_lines:
                cues.append("\n".join(current_lines))
                current_lines = []
            current_line = word

    if current_line:
        current_lines.append(current_line)
    if current_lines:
        cues.append("\n".join(current_lines))

    return cues


def _format_vtt_time(seconds: float) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def bodhan_make_subtitles(
    text: Optional[str] = None,
    segments: Optional[List[Dict[str, Any]]] = None,
    duration_seconds: float = 10.0,
    fmt: str = "srt",
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    output_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Format transcript or translated text/segments into readable SRT or VTT subtitles (42 chars x 2 lines)."""
    fmt_clean = fmt.strip().lower()
    if fmt_clean not in ("srt", "vtt"):
        raise ValueError(f"Unsupported subtitle format '{fmt}'. Use 'srt' or 'vtt'.")

    cues_timed: List[Tuple[float, float, str]] = []

    if segments:
        for seg in segments:
            s_start = max(0.0, float(seg.get("start", 0.0)))
            s_end = max(s_start + 0.5, float(seg.get("end", s_start + 5.0)))
            s_text = str(seg.get("text", "")).strip()
            wrapped = _wrap_subtitle_lines(s_text, max_chars_per_line=max_chars_per_line, max_lines=max_lines)
            if not wrapped:
                continue
            step = (s_end - s_start) / len(wrapped)
            for idx, cue_text in enumerate(wrapped):
                cues_timed.append((s_start + idx * step, s_start + (idx + 1) * step, cue_text))
    elif text:
        wrapped = _wrap_subtitle_lines(text, max_chars_per_line=max_chars_per_line, max_lines=max_lines)
        if wrapped:
            total_dur = max(1.0, float(duration_seconds))
            step = total_dur / len(wrapped)
            for idx, cue_text in enumerate(wrapped):
                cues_timed.append((idx * step, (idx + 1) * step, cue_text))
    else:
        raise ValueError("Provide either 'text' or 'segments' to bodhan_make_subtitles.")

    lines: List[str] = []
    if fmt_clean == "vtt":
        lines.append("WEBVTT\n")
        for idx, (st, en, body) in enumerate(cues_timed, start=1):
            lines.append(f"{idx}\n{_format_vtt_time(st)} --> {_format_vtt_time(en)}\n{body}\n")
    else:
        for idx, (st, en, body) in enumerate(cues_timed, start=1):
            lines.append(f"{idx}\n{_format_srt_time(st)} --> {_format_srt_time(en)}\n{body}\n")

    subtitle_text = "\n".join(lines).strip() + "\n"

    saved_path = None
    if output_file:
        out_p = Path(output_file).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(subtitle_text, encoding="utf-8")
        saved_path = str(out_p)

    return {
        "format": fmt_clean,
        "cue_count": len(cues_timed),
        "subtitles": subtitle_text,
        "output_file": saved_path,
        "cost_inr": 0.0,
    }
