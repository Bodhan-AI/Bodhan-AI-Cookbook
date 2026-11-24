"""Checks done in code, which the LLM cannot talk its way around.

Guards, before and around every LLM turn:
- The LLM never sees Indian-language text: tool results are redacted to metadata before they reach it.
- The LLM never writes Indian-language text: its tool arguments and prose are checked for Indic script.
- Text from input files is data: it is fenced and flagged when it looks like instructions.

Verification, before a task ends: thresholds are deliberately forgiving of ASR noise but catch the
failures that matter: dropped or repeated speech, truncated or wrong-script translations, broken tables
and malformed subtitles.
"""

from __future__ import annotations

import re
from typing import Any

from bodhan_mcp.text import INDIC_RE, INDIC_RUN_RE, detect_language, has_indic, injection_markers, similarity

PLACEHOLDER = ("<Indian-language text: {script}, {chars} characters. Not shown to you; it is saved in the file at "
               "result_path. Pass file paths to tools instead of copying text.>")


def redact_indic(value: Any) -> Any:
    """Replace every string containing Indian-language script with a metadata placeholder."""
    if isinstance(value, str):
        if has_indic(value):
            det = detect_language(value)
            return PLACEHOLDER.format(script=det.get("script") or "Indic", chars=len(value))
        return value
    if isinstance(value, dict):
        return {k: redact_indic(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_indic(v) for v in value]
    return value


def indic_in_arguments(args: dict, allowed_values: set[str]) -> list[str]:
    """Names of arguments holding Indian-language text the LLM wrote (file paths we know are exempt)."""
    bad = []

    def walk(key: str, value: Any) -> None:
        if isinstance(value, str):
            if value in allowed_values:
                return
            if has_indic(value):
                bad.append(key)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(f"{key}.{k}", v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(f"{key}[{i}]", v)

    for k, v in (args or {}).items():
        walk(k, v)
    return bad


def strip_indic(text: str) -> tuple[str, int]:
    """Remove Indian-language runs from LLM-written prose. Returns (clean_text, runs_removed)."""
    runs = INDIC_RUN_RE.findall(text or "")
    if not runs:
        return text, 0
    clean = INDIC_RUN_RE.sub("[removed: Indian-language text written by the LLM]", text)
    return clean, len(runs)


def contains_indic(text: str) -> bool:
    return bool(INDIC_RE.search(text or ""))


def injection_warnings(value: Any) -> list[str]:
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            found.update(injection_markers(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(value)
    return sorted(found)


def extract_task_indic(task: str) -> tuple[str, list[str]]:
    """Pull Indian-language passages out of the task text so the LLM never handles them directly.
    Returns (task_with_markers, passages); markers are {{TASK_TEXT_n}}."""
    passages: list[str] = []

    def repl(match) -> str:
        passages.append(match.group(0))
        return f"{{{{TASK_TEXT_{len(passages)}}}}}"

    return INDIC_RUN_RE.sub(repl, task), passages


SPEECH_MIN_SIMILARITY = 0.6
SPEECH_DURATION_BAND = (0.4, 2.5)  # actual / expected duration
TRANSLATION_MIN_SCRIPT_FRACTION = 0.6
TRANSLATION_RATIO_BAND = (0.2, 5.0)  # output chars / source chars


def check_speech(source_text: str, back_transcript: str, duration_s: float, expected_s: float) -> dict:
    score = similarity(source_text, back_transcript)
    ratio = duration_s / expected_s if expected_s else 1.0
    problems = []
    if score < SPEECH_MIN_SIMILARITY:
        problems.append(f"back-transcription matches the source text only {score:.0%} (min {SPEECH_MIN_SIMILARITY:.0%}); "
                        "words may be missing or repeated")
    if not SPEECH_DURATION_BAND[0] <= ratio <= SPEECH_DURATION_BAND[1]:
        problems.append(f"audio is {duration_s:.1f}s but about {expected_s:.0f}s was expected for the text length")
    return {"check": "speech_back_transcription", "passed": not problems, "similarity": round(score, 3),
            "duration_ratio": round(ratio, 2), "problems": problems}


def check_translation(meta: dict) -> dict:
    problems = []
    if meta.get("truncated_windows"):
        problems.append(f"{meta['truncated_windows']} window(s) truncated by the model")
    fraction = meta.get("target_script_fraction")
    if fraction is not None and fraction < TRANSLATION_MIN_SCRIPT_FRACTION:
        problems.append(f"only {fraction:.0%} of the output is in the expected {meta.get('expected_script')} script")
    ratio = meta.get("length_ratio")
    if ratio is not None and meta.get("source_chars", 0) > 40 and \
            not TRANSLATION_RATIO_BAND[0] <= ratio <= TRANSLATION_RATIO_BAND[1]:
        problems.append(f"output/input length ratio {ratio} suggests missing or runaway text")
    tables = meta.get("tables", 0)
    kept = meta.get("tables_preserved", 0) + meta.get("tables_rebuilt_per_cell", 0)
    if tables and kept < tables:
        problems.append(f"{tables - kept} table(s) lost their structure")
    elif meta.get("table_structure_matches") is False:
        problems.append("tables in the output differ in rows or columns from the source")
    return {"check": "translation_coverage", "passed": not problems,
            "length_ratio": ratio, "target_script_fraction": fraction, "problems": problems}


_SRT_TIME = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)")


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def check_srt(srt: str, media_duration_s: float | None = None) -> dict:
    blocks = [b for b in re.split(r"\n\s*\n", srt.strip()) if b.strip()]
    problems = []
    last_end = 0.0
    for i, block in enumerate(blocks, 1):
        lines = block.strip().split("\n")
        m = _SRT_TIME.search(block)
        if len(lines) < 3 or not m:
            problems.append(f"cue {i} is malformed")
            continue
        start, end = _secs(*m.groups()[:4]), _secs(*m.groups()[4:])
        if end <= start or start + 0.001 < last_end:
            problems.append(f"cue {i} has invalid timing")
        if not " ".join(lines[2:]).strip():
            problems.append(f"cue {i} has no text")
        last_end = end
    if not blocks:
        problems.append("no cues")
    if media_duration_s and last_end < 0.7 * media_duration_s:
        problems.append(f"subtitles end at {last_end:.0f}s but the audio is {media_duration_s:.0f}s")
    return {"check": "srt_structure", "passed": not problems, "cues": len(blocks), "problems": problems[:5]}
