"""Artifact verification: audio back-transcription, SRT validation, translation coverage, and table parity (SPEC §8.6)."""

import difflib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bodhan_mcp.tools.transcribe import transcribe_audio


class VerificationResult(BaseModel):
    """Result of a verification audit on generated outputs."""
    model_config = ConfigDict(extra="forbid")

    check_name: str
    passed: bool
    metric: float | dict[str, Any]
    details: dict[str, Any] = Field(default_factory=dict)


def compute_char_similarity(text_a: str, text_b: str) -> float:
    """Compute character-level similarity ratio between two strings."""
    norm_a = re.sub(r"\s+", "", text_a)
    norm_b = re.sub(r"\s+", "", text_b)
    if not norm_a and not norm_b:
        return 1.0
    matcher = difflib.SequenceMatcher(None, norm_a, norm_b)
    return round(matcher.ratio(), 4)


async def verify_audio_back_transcription(
    audio_path: str,
    expected_text: str,
    target_duration_s: float | None = None,
    client: Any = None,
) -> VerificationResult:
    """Verify generated audio by back-transcribing and asserting character similarity >= 0.85."""
    trans_res = await transcribe_audio(input_path=audio_path, client=client)
    if hasattr(trans_res, "transcript"):
        transcribed_text = trans_res.transcript
        similarity = compute_char_similarity(expected_text, transcribed_text)
        duration_ok = True
        duration_details = {}

        if target_duration_s and target_duration_s > 0:
            actual_dur = trans_res.duration_s
            diff_ratio = abs(actual_dur - target_duration_s) / target_duration_s
            duration_ok = diff_ratio <= 0.20  # within ±20%
            duration_details = {
                "target_dur": target_duration_s,
                "actual_dur": actual_dur,
                "diff_ratio": round(diff_ratio, 3),
            }

        passed = (similarity >= 0.85) and duration_ok
        return VerificationResult(
            check_name="audio_back_transcribe",
            passed=passed,
            metric={"char_similarity": similarity, **duration_details},
            details={
                "expected": expected_text[:50],
                "back_transcribed": transcribed_text[:50],
            },
        )
    return VerificationResult(
        check_name="audio_back_transcribe",
        passed=False,
        metric={"char_similarity": 0.0},
        details={"error": "Failed to back-transcribe audio file"},
    )


def verify_translation_coverage(
    source_segments: list[str],
    target_segments: list[str],
) -> VerificationResult:
    """Verify translation preserves segment parity and length ratio within [0.4, 3.0]."""
    src_count = len(source_segments)
    tgt_count = len(target_segments)

    count_parity = (src_count == tgt_count) and src_count > 0
    no_empties = all(bool(s.strip()) for s in target_segments)

    src_len = sum(len(s) for s in source_segments)
    tgt_len = sum(len(s) for s in target_segments)
    ratio = round(tgt_len / max(1, src_len), 3)
    ratio_ok = 0.4 <= ratio <= 3.0

    passed = count_parity and no_empties and ratio_ok
    return VerificationResult(
        check_name="translation_coverage",
        passed=passed,
        metric={"ratio": ratio, "source_segments": src_count, "target_segments": tgt_count},
        details={"no_empties": no_empties, "count_parity": count_parity},
    )


def verify_srt_structure(srt_content: str, source_duration_s: float | None = None) -> VerificationResult:
    """Verify SRT parses, timestamps are monotonically increasing, and cues are within duration."""
    # Pattern matching standard SRT blocks: index, timestamp line, text
    srt_block_re = re.compile(
        r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n(.*?)(?=\n\s*\d+\s*\n|\Z)",
        re.DOTALL,
    )

    def srt_time_to_s(ts: str) -> float:
        ts = ts.replace(",", ".")
        h, m, s = ts.split(":")
        return int(h) * 3600.0 + int(m) * 60.0 + float(s)

    blocks = srt_block_re.findall(srt_content)
    if not blocks:
        return VerificationResult(
            check_name="srt_structure",
            passed=False,
            metric={"cue_count": 0},
            details={"error": "Failed to parse any valid SRT cues."},
        )

    last_time = -1.0
    monotonic = True
    cues_valid = True

    for _, start_str, end_str, text in blocks:
        st = srt_time_to_s(start_str)
        et = srt_time_to_s(end_str)
        if st < last_time or et <= st:
            monotonic = False
        last_time = st
        if not text.strip():
            cues_valid = False

    duration_ok = True
    if source_duration_s and blocks:
        last_cue_end = srt_time_to_s(blocks[-1][2])
        if last_cue_end > source_duration_s + 0.5:
            duration_ok = False

    passed = monotonic and cues_valid and duration_ok
    return VerificationResult(
        check_name="srt_structure",
        passed=passed,
        metric={"cues": len(blocks), "monotonic": monotonic, "duration_ok": duration_ok},
        details={"cues_valid": cues_valid},
    )


def verify_table_parity(source_table_md: str, target_table_md: str) -> VerificationResult:
    """Verify translated table preserves exact row and column dimensions."""
    src_rows = [line.strip() for line in source_table_md.strip().splitlines() if line.strip().startswith("|")]
    tgt_rows = [line.strip() for line in target_table_md.strip().splitlines() if line.strip().startswith("|")]

    src_row_count = len(src_rows)
    tgt_row_count = len(tgt_rows)

    src_cols = [len(r.split("|")) - 2 for r in src_rows]
    tgt_cols = [len(r.split("|")) - 2 for r in tgt_rows]

    row_match = (src_row_count == tgt_row_count) and src_row_count > 0
    col_match = (src_cols == tgt_cols) and len(src_cols) > 0

    passed = row_match and col_match
    return VerificationResult(
        check_name="table_parity",
        passed=passed,
        metric={"src_rows": src_row_count, "tgt_rows": tgt_row_count, "col_match": col_match},
        details={"row_match": row_match, "col_match": col_match},
    )
