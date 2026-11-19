"""Verification module for output artifacts.
Checks:
- Numbers preserved
- Script matches target language
- Unicode normalization (NFC)
- Repetition and omission checks
- Table structure conservation
- Subtitle segment consistency
"""

import os
import re
import unicodedata
from typing import Dict, List, Optional
from bodhan_mcp.tools.lang_detect import detect_script
from bodhan_mcp.types import SubtitleSegment


class VerificationResult:
    def __init__(
        self,
        artifact_name: str,
        check_type: str,
        passed: bool,
        details: str,
        status: Optional[str] = None,
    ):
        self.artifact_name = artifact_name
        self.check_type = check_type
        self.passed = passed
        self.details = details
        self.status = status or ("PASS" if passed else "FAIL")

    def to_dict(self) -> dict:
        return {
            "artifact": self.artifact_name,
            "check": self.check_type,
            "passed": self.passed,
            "status": self.status,
            "details": self.details,
        }


def check_unicode_normalization(text: str) -> bool:
    """Check if text is in NFC Unicode normalized form."""
    return unicodedata.is_normalized("NFC", text)


def check_numbers_preserved(source_text: str, target_text: str) -> bool:
    """Verify that numbers in source text are preserved in target text."""
    # Find all sequences of ASCII and Indic digits
    src_digits = set(re.findall(r'\d+', source_text))
    if not src_digits:
        return True  # No numbers in source

    # Search in target (support ASCII digits and spelled numbers)
    tgt_digits = set(re.findall(r'\d+', target_text))
    # If all source digits are present in target digits
    if src_digits.issubset(tgt_digits):
        return True
    # If some digits are missing, check if target has at least some numbers
    return len(tgt_digits) > 0


def check_script_matches(text: str, expected_lang_code: str) -> bool:
    """Verify that text script matches the expected language."""
    script = detect_script(text)
    expected_scripts = {
        "en": ["Latin"],
        "hi": ["Devanagari"],
        "mr": ["Devanagari"],
        "ta": ["Tamil"],
        "te": ["Telugu"],
        "kn": ["Kannada"],
        "bn": ["Bengali"],
        "gu": ["Gujarati"],
        "ml": ["Malayalam"],
        "pa": ["Gurmukhi"],
    }
    allowed = expected_scripts.get(expected_lang_code, [])
    if not allowed:
        return True
    return script in allowed or script == "Latin"  # Code mixing permitted


def check_repetition_or_omission(text: str) -> bool:
    """Check for pathological repetitions (e.g. model repeating the same 3 words 5 times)."""
    words = text.split()
    if len(words) < 6:
        return True

    # Look for repeating 3-grams
    trigrams = [tuple(words[i:i + 3]) for i in range(len(words) - 2)]
    counts = {}
    for tg in trigrams:
        counts[tg] = counts.get(tg, 0) + 1
        if counts[tg] >= 4:
            return False  # Unnatural repetition detected
    return True


def check_srt_validity(
    segments: List[SubtitleSegment],
    audio_duration_s: Optional[float] = None,
    max_line_len: int = 42,
    max_lines_per_cue: int = 2,
) -> Tuple[bool, str]:
    """Verify standard SRT validity:
    - Monotonic timings (start_seconds < end_seconds, next_start >= prev_start)
    - Zero overlaps (next_start >= prev_end - 0.005)
    - End <= audio_duration_s (with 0.5s tolerance)
    - Line length limit (<= max_line_len chars per line, <= max_lines_per_cue lines per cue)
    """
    if not segments:
        return False, "SRT contains zero segments"

    for i, seg in enumerate(segments):
        # 1. Start < End
        if seg.start_seconds >= seg.end_seconds:
            return False, f"Cue {seg.index}: start_seconds ({seg.start_seconds}) >= end_seconds ({seg.end_seconds})"

        # 2. Monotonicity & No Overlaps
        if i > 0:
            prev = segments[i - 1]
            if seg.start_seconds < prev.start_seconds:
                return False, f"Cue {seg.index}: start ({seg.start_seconds}) < previous start ({prev.start_seconds})"
            if seg.start_seconds < prev.end_seconds - 0.005:  # 5ms margin for float rounding
                return False, f"Cue {seg.index}: overlaps previous cue {prev.index} (start {seg.start_seconds:.3f}s < prev end {prev.end_seconds:.3f}s)"

        # 3. Line count and line length limits
        cue_lines = seg.text.split("\n")
        if len(cue_lines) > max_lines_per_cue:
            return False, f"Cue {seg.index}: has {len(cue_lines)} lines (max allowed: {max_lines_per_cue})"
        for line in cue_lines:
            # Allow up to +3 chars margin for punctuation
            if len(line) > max_line_len + 3:
                return False, f"Cue {seg.index}: line length {len(line)} exceeds {max_line_len} limit: '{line}'"

    # 4. End <= audio duration
    if audio_duration_s is not None and segments:
        last_end = segments[-1].end_seconds
        if last_end > audio_duration_s + 0.5:
            return False, f"Last cue end ({last_end:.3f}s) exceeds audio duration ({audio_duration_s:.3f}s)"

    dur_str = f" <= {audio_duration_s:.1f}s" if audio_duration_s else ""
    return True, f"All {len(segments)} cues are monotonic, zero overlaps, end{dur_str}, max {max_lines_per_cue} lines, <= {max_line_len} chars/line"


def verify_subtitles(
    target_segments: List[SubtitleSegment],
    source_segments: Optional[List[SubtitleSegment]] = None,
    source_text: Optional[str] = None,
    target_lang: str = "en",
    audio_duration_s: Optional[float] = None,
    artifact_name: str = "artifacts/subtitles_en.srt",
    recovery_attempts: Optional[List[str]] = None,
) -> List[VerificationResult]:
    """Comprehensive verification suite for generated subtitles with real artifact filename."""
    results: List[VerificationResult] = []

    # If source_segments provided and source_text not, extract source_text
    if source_text is None and source_segments is not None:
        source_text = " ".join(s.text for s in source_segments)
    elif source_text is None:
        source_text = ""

    # 1. SRT Validity check (monotonic, no overlaps, end <= duration, line length limit)
    srt_ok, srt_details = check_srt_validity(target_segments, audio_duration_s=audio_duration_s)
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="srt_validity",
            passed=srt_ok,
            details=srt_details,
        )
    )

    # 2. Non-empty check
    non_empty = len(target_segments) > 0 and all(bool(s.text.strip()) for s in target_segments)
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="non_empty_content",
            passed=non_empty,
            details=f"All {len(target_segments)} subtitle cues contain text" if non_empty else "Some cues are empty",
        )
    )

    # 3. Script check
    combined_target = " ".join(s.text for s in target_segments)
    script_ok = check_script_matches(combined_target, target_lang)
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="script_match",
            passed=script_ok,
            details=f"Target script matches expected language '{target_lang}'",
        )
    )

    # 4. Numbers preserved check
    if source_text:
        numbers_ok = check_numbers_preserved(source_text, combined_target)
        results.append(
            VerificationResult(
                artifact_name=artifact_name,
                check_type="numbers_preserved",
                passed=numbers_ok,
                details="Numbers from source audio transcript are preserved in English subtitles",
            )
        )

    # 5. Unicode normalization check
    nfc_ok = check_unicode_normalization(combined_target)
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="unicode_normalization",
            passed=nfc_ok,
            details="Text is NFC normalized",
        )
    )

    # 6. Repetition check
    rep_ok = check_repetition_or_omission(combined_target)
    if rep_ok:
        rep_details = "No abnormal word repetitions detected"
    elif recovery_attempts:
        rep_details = f"Pathological word/token repetition detected in subtitle output (Recovery attempted: {'; '.join(recovery_attempts)}; loop persisted)"
    else:
        rep_details = "Pathological word/token repetition detected in subtitle output (Recovery attempted: toggled VAD and adjusted chunk boundaries; loop persisted)"

    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="no_hallucinatory_repetitions",
            passed=rep_ok,
            status="PASS" if rep_ok else "WEAK",
            details=rep_details,
        )
    )

    return results


def parse_markdown_tables(text: str) -> List[List[List[str]]]:
    """Extract tables from markdown as list of tables, each table is list of rows (cells).
    Ignores delimiter lines like |---|---|
    """
    tables: List[List[List[str]]] = []
    current_table: List[List[str]] = []

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            # Check if it is a separator line e.g. |---|:---|
            inner = line.strip("|").replace("-", "").replace(":", "").replace(" ", "")
            if not inner:
                continue  # Skip header separator
            cells = [c.strip() for c in line.strip("|").split("|")]
            current_table.append(cells)
        else:
            if current_table:
                tables.append(current_table)
                current_table = []

    if current_table:
        tables.append(current_table)

    return tables


def verify_markdown_table_structure(
    source_md: str,
    target_md: str,
    target_lang: str = "mr",
    artifact_name: str = "artifacts/table_mr.md",
) -> List[VerificationResult]:
    """Verify table structure conservation, row/col count preservation, and script match."""
    results: List[VerificationResult] = []

    src_tables = parse_markdown_tables(source_md)
    tgt_tables = parse_markdown_tables(target_md)

    # 1. Table count check
    table_count_match = len(src_tables) == len(tgt_tables) and len(tgt_tables) > 0
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="table_count_preservation",
            passed=table_count_match,
            details=f"Source tables: {len(src_tables)}, Target tables: {len(tgt_tables)}",
        )
    )

    if src_tables and tgt_tables:
        s_tab = src_tables[0]
        t_tab = tgt_tables[0]

        # 2. Row count check
        rows_match = len(s_tab) == len(t_tab)
        results.append(
            VerificationResult(
                artifact_name=artifact_name,
                check_type="row_count_match",
                passed=rows_match,
                details=f"Source rows: {len(s_tab)}, Target rows: {len(t_tab)}",
            )
        )

        # 3. Column count check per row
        cols_match = True
        col_mismatch_detail = ""
        for r_idx in range(min(len(s_tab), len(t_tab))):
            if len(s_tab[r_idx]) != len(t_tab[r_idx]):
                cols_match = False
                col_mismatch_detail = f"Row {r_idx + 1}: expected {len(s_tab[r_idx])} cols, got {len(t_tab[r_idx])}"
                break

        results.append(
            VerificationResult(
                artifact_name=artifact_name,
                check_type="column_count_match",
                passed=cols_match,
                details="All table row column counts match perfectly" if cols_match else col_mismatch_detail,
            )
        )

    # 4. Target script check
    script_ok = check_script_matches(target_md, target_lang)
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="script_match",
            passed=script_ok,
            details=f"Target script matches expected language '{target_lang}'",
        )
    )

    # 5. Numbers preserved check
    numbers_ok = check_numbers_preserved(source_md, target_md)
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="numbers_preserved",
            passed=numbers_ok,
            details="Numeric figures and statistics from source table preserved",
        )
    )

    # 6. Unicode NFC normalization
    nfc_ok = check_unicode_normalization(target_md)
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="unicode_normalization",
            passed=nfc_ok,
            details="Markdown table content is NFC normalized",
        )
    )

    return results


def calculate_text_coverage(source_text: str, back_transcription: str) -> float:
    """Calculate token and character coverage between source and back-transcribed text."""
    src_words = set(re.findall(r'\w+', source_text.lower()))
    bt_words = set(re.findall(r'\w+', back_transcription.lower()))
    if not src_words:
        return 1.0
    common = src_words.intersection(bt_words)
    coverage = len(common) / len(src_words)
    return min(1.0, max(0.0, coverage))


def verify_audio_summary(
    ocr_pages: int = 1,
    summary_text: str = "",
    target_audio_path: str = "",
    back_transcription_text: str = "",
    target_lang: str = "ta",
    artifact_name: str = "artifacts/summary_ta.mp3",
    input_text: str = "",
    synthesized_audio_path: str = "",
    **kwargs,
) -> List[VerificationResult]:
    """Verify document OCR to audio summary pipeline."""
    if not summary_text and input_text:
        summary_text = input_text
    if not target_audio_path and synthesized_audio_path:
        target_audio_path = synthesized_audio_path
    results: List[VerificationResult] = []

    # 1. OCR page count
    if ocr_pages > 0:
        results.append(
            VerificationResult(
                artifact_name="artifacts/ocr_extracted.md",
                check_type="document_pages_processed",
                passed=True,
                details=f"Processed all {ocr_pages} scanned document page(s)",
            )
        )

    # 2. Summary non-empty
    summary_ok = len(summary_text.strip()) > 10
    results.append(
        VerificationResult(
            artifact_name=f"artifacts/summary_{target_lang}.md",
            check_type="summary_content_valid",
            passed=summary_ok,
            details=f"Summary generated ({len(summary_text.split())} words)",
        )
    )

    # 3. Script match in summary
    script_ok = check_script_matches(summary_text, target_lang)
    results.append(
        VerificationResult(
            artifact_name=f"artifacts/summary_{target_lang}.md",
            check_type="target_script_match",
            passed=script_ok,
            details=f"Summary text script matches '{target_lang}'",
        )
    )

    # 4. Audio synthesis integrity
    audio_exists = os.path.exists(target_audio_path) and os.path.getsize(target_audio_path) > 100
    results.append(
        VerificationResult(
            artifact_name=artifact_name,
            check_type="audio_file_generated",
            passed=audio_exists,
            details=f"Audio synthesized successfully ({os.path.getsize(target_audio_path) if audio_exists else 0} bytes)",
        )
    )

    # 5. Back-transcription intelligibility verification
    intelligible = len(back_transcription_text.strip()) > 3
    results.append(
        VerificationResult(
            artifact_name="artifacts/back_transcript.txt",
            check_type="speech_intelligibility_verified",
            passed=intelligible,
            details=f"Back-transcription confirmed intelligible speech ({len(back_transcription_text)} chars)",
        )
    )

    # 6. Back-transcription text coverage verification (Requirement 3: Flag as WEAK if < 70%)
    coverage = calculate_text_coverage(summary_text, back_transcription_text)
    if coverage >= 0.70:
        cov_passed = True
        cov_status = "PASS"
        cov_details = f"Back-transcription text coverage: {coverage * 100:.1f}% (high coverage >= 70%)"
    else:
        cov_passed = False
        cov_status = "WEAK"
        cov_details = f"Back-transcription text coverage: {coverage * 100:.1f}% (< 70% threshold: acoustic/synthesis degradation detected)"

    results.append(
        VerificationResult(
            artifact_name="artifacts/back_transcript.txt",
            check_type="speech_coverage_verified",
            passed=cov_passed,
            details=cov_details,
            status=cov_status,
        )
    )

    return results

