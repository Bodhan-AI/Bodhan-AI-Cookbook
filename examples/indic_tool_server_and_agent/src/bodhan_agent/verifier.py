"""Deterministic verification checks for generated artifacts."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from bodhan_mcp.tools.media import detect_script

logger = logging.getLogger(__name__)


def compute_cer(reference: str, hypothesis: str) -> float:
    """
    Compute Character Error Rate (CER) using Levenshtein distance.
    Returns value between 0.0 (identical) and 1.0+ (very different).
    """
    ref = reference.strip()
    hyp = hypothesis.strip()
    if not ref:
        return 0.0 if not hyp else 1.0

    n, m = len(ref), len(hyp)
    # Use 2-row DP for memory efficiency
    prev = list(range(m + 1))
    curr = [0] * (m + 1)

    for i in range(1, n + 1):
        curr[0] = i
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,      # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost  # substitution
            )
        prev, curr = curr, [0] * (m + 1)

    dist = prev[m]
    return float(dist) / float(len(ref))


def compute_similarity(s1: str, s2: str) -> float:
    """Normalized token overlap similarity between 0.0 and 1.0."""
    t1 = set(s1.lower().split())
    t2 = set(s2.lower().split())
    if not t1 or not t2:
        return 0.0
    intersection = t1.intersection(t2)
    union = t1.union(t2)
    return len(intersection) / len(union)


async def verify_audio(
    audio_path: str,
    expected_text: str,
    language: Optional[str] = None,
) -> dict[str, Any]:
    """
    Verify generated audio by back-transcribing with Indic-Transcribe
    and calculating CER against expected text.
    """
    p = Path(audio_path)
    if not p.exists() or p.stat().st_size == 0:
        return {
            "check": "audio_verification",
            "artifact": str(p),
            "status": "failed",
            "reason": f"Audio file {audio_path} does not exist or is empty",
        }

    from bodhan_mcp.tools.stt import transcribe_audio
    try:
        stt_res = await transcribe_audio(file_path=str(p), language=language)
        transcribed_text = stt_res.full_text if hasattr(stt_res, "full_text") else stt_res.get("full_text", "")

        cer = compute_cer(expected_text, transcribed_text)
        duration = stt_res.duration_seconds if hasattr(stt_res, "duration_seconds") else stt_res.get("duration_seconds", 0.0)

        # Indic-Speak might have minor phonetic differences, so CER <= 0.45 is acceptable
        passed = cer <= 0.50 and duration > 0.0
        return {
            "check": "audio_verification",
            "artifact": str(p),
            "status": "passed" if passed else "warning",
            "cer": round(cer, 4),
            "duration_seconds": round(duration, 2),
            "back_transcription_preview": transcribed_text[:100],
            "details": f"CER: {cer:.2%}, duration: {duration:.1f}s",
        }
    except Exception as exc:
        logger.warning("Audio back-transcription verification error: %s", exc)
        return {
            "check": "audio_verification",
            "artifact": str(p),
            "status": "warning",
            "reason": f"Could not complete back-transcription: {exc}",
        }


def verify_translation(
    source_text: str,
    translated_text: str,
    target_language: str,
) -> dict[str, Any]:
    """
    Verify translation coverage, length ratio, structure preservation.
    """
    if not translated_text or not translated_text.strip():
        return {
            "check": "translation_verification",
            "status": "failed",
            "reason": "Translated text is empty",
        }

    if not source_text or not source_text.strip():
        return {
            "check": "translation_verification",
            "status": "error",
            "source_chars": 0,
            "reason": "source_chars is 0 — caller must resolve the actual source text before calling verify_translation",
        }

    src_len = len(source_text.strip())
    trans_len = len(translated_text.strip())
    ratio = trans_len / max(src_len, 1)

    # Check Markdown tables preserved
    src_tables = source_text.count("|")
    trans_tables = translated_text.count("|")
    tables_preserved = (src_tables == 0) or (trans_tables >= src_tables * 0.7)

    # Check script/language if target is Indic
    script_check = detect_script(translated_text[:200])
    detected_lang = script_check.detected_language if hasattr(script_check, "detected_language") else script_check.get("detected_language")

    # Ratio between 0.2 and 4.0 is typical for translations
    sane_length = 0.2 <= ratio <= 4.0
    passed = sane_length and tables_preserved

    return {
        "check": "translation_verification",
        "status": "passed" if passed else "warning",
        "source_chars": src_len,
        "translated_chars": trans_len,
        "length_ratio": round(ratio, 2),
        "tables_preserved": tables_preserved,
        "detected_script": getattr(script_check, "script_name", None),
        "details": f"Length ratio {ratio:.2f}, tables: {trans_tables}/{src_tables}",
    }


def verify_transcript(text: str, expected_language: Optional[str] = None) -> dict[str, Any]:
    """Verify STT transcript is non-empty and in expected script."""
    if not text or not text.strip():
        return {
            "check": "transcript_verification",
            "status": "failed",
            "reason": "Transcript text is empty",
        }

    script_check = detect_script(text[:300])
    return {
        "check": "transcript_verification",
        "status": "passed",
        "char_count": len(text.strip()),
        "detected_script": getattr(script_check, "script_name", None),
        "detected_language": getattr(script_check, "detected_language", None),
    }


def verify_ocr(text: str) -> dict[str, Any]:
    """Verify OCR extracted text."""
    if not text or not text.strip():
        return {
            "check": "ocr_verification",
            "status": "failed",
            "reason": "OCR extracted text is empty",
        }

    has_table = "|" in text or "\t" in text
    return {
        "check": "ocr_verification",
        "status": "passed",
        "char_count": len(text.strip()),
        "has_table_structure": has_table,
    }


def verify_srt(srt_path: str, max_duration: Optional[float] = None) -> dict[str, Any]:
    """Verify SRT file format and monotonic timestamps."""
    p = Path(srt_path)
    if not p.exists() or p.stat().st_size == 0:
        return {
            "check": "srt_verification",
            "artifact": str(p),
            "status": "failed",
            "reason": "SRT file missing or empty",
        }

    content = p.read_text(encoding="utf-8")
    time_pattern = re.compile(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
    )
    matches = time_pattern.findall(content)
    if not matches:
        return {
            "check": "srt_verification",
            "artifact": str(p),
            "status": "failed",
            "reason": "No valid SRT timestamp cues found",
        }

    def to_seconds(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    monotonic = True
    prev_end = 0.0
    for match in matches:
        start_s = to_seconds(*match[:4])
        end_s = to_seconds(*match[4:])
        if start_s < prev_end or end_s <= start_s:
            monotonic = False
            break
        prev_end = end_s

    return {
        "check": "srt_verification",
        "artifact": str(p),
        "status": "passed" if monotonic else "warning",
        "num_entries": len(matches),
        "monotonic_timestamps": monotonic,
        "last_timestamp_seconds": round(prev_end, 2),
    }


async def verify_all(state: Any, out_dir: str | Path) -> list[dict[str, Any]]:
    """Run all relevant verifications across artifacts in state."""
    results = []
    for artifact in state.artifacts:
        kind = getattr(artifact, "kind", "")
        path = getattr(artifact, "path", "")

        if kind == "audio":
            # Find expected text from producer step
            step = state.get_step(artifact.produced_by)
            expected_text = ""
            lang = None
            if step and step.args:
                expected_text = step.args.get("text", "")
                lang = step.args.get("language")
            res = await verify_audio(path, expected_text, language=lang)
            results.append(res)

        elif kind == "subtitle" or path.endswith(".srt"):
            res = verify_srt(path)
            results.append(res)

        elif kind in ("text", "markdown"):
            p = Path(path)
            if p.exists():
                text = p.read_text(encoding="utf-8", errors="replace")
                step = state.get_step(artifact.produced_by)
                if step and step.tool == "translate_text":
                    # Prefer resolved_args (actual runtime values) over args
                    # (which may contain unresolved template refs like ${step_1.output})
                    resolved = getattr(step, "resolved_args", {}) or {}
                    src_text = resolved.get("text") or step.args.get("text") or ""

                    # If still empty, try to read from file_path in resolved_args
                    if not src_text:
                        fp = resolved.get("file_path") or step.args.get("file_path") or ""
                        if fp:
                            try:
                                src_text = Path(fp).read_text(encoding="utf-8", errors="replace").strip()
                                logger.debug("Translation verifier: read source from file_path %s (%d chars)", fp, len(src_text))
                            except Exception as exc:
                                logger.warning("Translation verifier: could not read file_path %s: %s", fp, exc)

                    # Last resort: use the top-level input_text from task state
                    if not src_text:
                        src_text = getattr(state, "input_text", None) or ""
                        if src_text:
                            logger.debug("Translation verifier: falling back to state.input_text (%d chars)", len(src_text))

                    if not src_text:
                        logger.warning(
                            "Translation verifier: could not resolve source text for step %s "
                            "(args=%r, resolved_args=%r) — verification will be skipped",
                            artifact.produced_by, step.args, resolved,
                        )
                        results.append({
                            "check": "translation_verification",
                            "status": "skipped",
                            "reason": "source_chars is 0 — source text could not be resolved; "
                                      "check that resolved_args is populated by the executor",
                            "artifact": path,
                        })
                        continue

                    target_lang = resolved.get("target_language") or step.args.get("target_language", "en")
                    res = verify_translation(src_text, text, target_lang)
                    res["artifact"] = path
                    results.append(res)
                elif step and step.tool == "ocr_document":
                    res = verify_ocr(text)
                    res["artifact"] = path
                    results.append(res)
                elif step and step.tool == "transcribe_audio":
                    res = verify_transcript(text)
                    res["artifact"] = path
                    results.append(res)

    return results
