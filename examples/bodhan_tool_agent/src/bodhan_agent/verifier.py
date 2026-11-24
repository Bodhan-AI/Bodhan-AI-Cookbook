#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bodhan_mcp.media import script_ratio, verify_table_parity


@dataclass
class TranslationVerifier:
    """Verifies translation quality: non-empty text, script ratio >= 0.85,
    table parity."""

    target_lang: str = ""
    min_script_ratio: float = 0.85

    def verify(
        self, source_text: str, translated_text: str,
        has_tables: bool = False,
    ) -> dict[str, Any]:
        """Run verification checks on translated text."""
        results = {
            "non_empty": bool(translated_text and translated_text.strip()),
            "script_ratio": 0.0,
            "table_parity": True,
            "passed": False,
            "details": {},
        }

        if not results["non_empty"]:
            results["passed"] = False
            results["details"]["error"] = "Translated text is empty"
            return results

        lang_script_map = {
            "hi": "devanagari", "mr": "devanagari", "ne": "devanagari",
            "sa": "devanagari", "bn": "bengali", "as": "bengali",
            "gu": "gujarati", "pa": "gurmukhi", "or": "oriya",
            "ta": "tamil", "te": "telugu", "kn": "kannada",
            "ml": "malayalam", "ur": "arabic", "ks": "arabic",
            "sd": "devanagari", "sd-Arab": "arabic", "mni": "latin",
            "kok": "devanagari", "mai": "devanagari", "brx": "devanagari",
            "doi": "devanagari", "sat": "latin", "bhb": "devanagari",
            "bho": "devanagari",
        }

        expected_script = lang_script_map.get(self.target_lang, "latin")
        ratio = script_ratio(translated_text, expected_script)
        results["script_ratio"] = round(ratio, 4)

        if has_tables:
            results["table_parity"] = verify_table_parity(
                source_text, translated_text
            )

        checks = [results["non_empty"]]
        details = {
            "script_ratio_sufficient": ratio >= self.min_script_ratio,
            "table_parity_ok": results["table_parity"],
        }

        if expected_script != "latin":
            checks.append(ratio >= self.min_script_ratio)
        if has_tables:
            checks.append(results["table_parity"])

        results["passed"] = all(checks)
        results["details"] = {
            "expected_script": expected_script,
            **details,
            "script_ratio_threshold": self.min_script_ratio,
        }
        return results


@dataclass
class SpeechVerifier:
    """Verifies synthesized audio: duration > 0.5s, back-transcription
    similarity >= 0.75, no content omission/repetition."""

    min_duration_sec: float = 0.5
    min_similarity: float = 0.75
    chars_per_sec: float = 12.0  # Expected speaking rate for Indic languages
    omission_duration_factor: float = 0.5  # Flag if duration < 0.5 * expected
    omission_min_expected_sec: float = 3.0  # duration heuristic only for longer text
    omission_coverage_threshold: float = 0.7  # Flag if coverage < 70%
    repetition_ngram_size: int = 2
    repetition_max_count: int = 2
    repetition_coverage_threshold: float = 0.3  # Flag if repeated n-grams cover > 30%
    duration_inflation_factor: float = 2.0  # Flag if duration > 2x expected

    async def verify(
        self,
        audio_path: str,
        source_script: str,
        transcribe_func=None,
    ) -> dict[str, Any]:
        """Run verification checks on synthesized audio."""
        results = {
            "file_exists": False,
            "duration_ok": False,
            "similarity": 0.0,
            "back_transcribed": "",
            "passed": False,
            "details": {},
        }

        if not transcribe_func:
            results["details"]["error"] = (
                "No transcribe function provided"
            )
            return results

        try:
            audio_info = await self._check_audio_duration(audio_path)
            results["file_exists"] = True
            results["duration_sec"] = audio_info.get("duration_sec", 0)
            dur = audio_info.get("duration_sec", 0)
            results["duration_ok"] = dur > self.min_duration_sec
        except Exception as e:
            results["details"]["audio_check_error"] = str(e)
            return results

        if not results["duration_ok"]:
            dur = audio_info.get("duration_sec", 0)
            results["details"]["error"] = (
                f"Audio duration too short: {dur:.2f}s "
                f"< {self.min_duration_sec}s"
            )
            return results

        # Expected duration based on source text length and speaking rate
        expected_duration = (
            len(source_script) / self.chars_per_sec if source_script else 0
        )
        results["details"]["expected_duration_sec"] = round(expected_duration, 2)
        results["details"]["chars_per_sec"] = self.chars_per_sec

        try:
            transcription = await transcribe_func(audio_path)
            if "error" in transcription:
                error_msg = transcription.get("message", "Unknown error")
                results["details"]["error"] = (
                    "Speech verification failed: back-transcription via "
                    f"Indic-Transcribe failed ({error_msg})"
                )
                results["details"]["transcription_error"] = error_msg
                return results
            results["back_transcribed"] = transcription.get("text", "")
            src = source_script
            tgt = transcription.get("text", "")
            sim = self._compute_similarity(src, tgt)
            results["similarity"] = round(sim, 4)

            # --- Omission Detection ---
            # 1. Duration omission: actual duration much shorter than expected
            if expected_duration >= self.omission_min_expected_sec:
                duration_ratio = dur / expected_duration
                results["details"]["duration_ratio"] = round(duration_ratio, 3)
                if duration_ratio < self.omission_duration_factor:
                    results["details"]["error"] = (
                        f"Content omission detected: audio duration {dur:.2f}s is "
                        f"{duration_ratio:.1%} of expected {expected_duration:.2f}s "
                        f"(< {self.omission_duration_factor:.0%} threshold)"
                    )
                    results["details"]["omission_type"] = "duration"
                    results["passed"] = False
                    return results

            # 2. Coverage-based omission: back-transcription covers too little of source
            if src and tgt:
                src_chars = set(re.sub(r"\s+", "", src))
                tgt_chars = set(re.sub(r"\s+", "", tgt))
                if src_chars:
                    coverage = len(src_chars & tgt_chars) / len(src_chars)
                    results["details"]["back_transcription_coverage"] = round(
                        coverage, 3
                    )
                    if coverage < self.omission_coverage_threshold:
                        results["details"]["error"] = (
                            f"Content omission detected: back-transcription coverage "
                            f"{coverage:.1%} < {self.omission_coverage_threshold:.0%}"
                        )
                        results["details"]["omission_type"] = "coverage"
                        results["passed"] = False
                        return results

            # --- Repetition Detection ---
            # 1. N-gram repetition in back-transcription
            repetition_result = self._detect_repetition(tgt)
            if repetition_result:
                results["details"]["repetition"] = repetition_result
                results["details"]["error"] = (
                    f"Content repetition detected: {repetition_result['description']}"
                )
                results["details"]["omission_type"] = "repetition"
                results["passed"] = False
                return results

            # 2. Duration inflation: actual duration significantly longer than expected
            if expected_duration > 0:
                duration_ratio = dur / expected_duration
                if duration_ratio > self.duration_inflation_factor:
                    results["details"]["error"] = (
                        f"Abnormal duration inflation: audio duration {dur:.2f}s is "
                        f"{duration_ratio:.1f}x expected {expected_duration:.2f}s "
                        f"(> {self.duration_inflation_factor:.1f}x threshold)"
                    )
                    results["details"]["omission_type"] = "duration_inflation"
                    results["passed"] = False
                    return results

        except Exception as e:
            results["details"]["error"] = (
                f"Speech verification failed: back-transcription via Indic-Transcribe "
                f"failed ({e})"
            )
            results["details"]["transcription_error"] = str(e)

        fe = results["file_exists"]
        do = results["duration_ok"]
        sm = results["similarity"] >= self.min_similarity
        results["passed"] = fe and do and sm
        results["details"]["min_duration_threshold"] = self.min_duration_sec
        results["details"]["similarity_threshold"] = self.min_similarity
        return results

    def _detect_repetition(self, text: str) -> dict | None:
        """Detect excessive n-gram repetition in text."""
        if not text:
            return None

        # Tokenize by whitespace
        tokens = text.split()
        if len(tokens) < self.repetition_ngram_size * 2:
            return None

        n = self.repetition_ngram_size
        ngrams = {}
        for i in range(len(tokens) - n + 1):
            ngram = tuple(tokens[i:i + n])
            ngrams[ngram] = ngrams.get(ngram, 0) + 1

        # Check for n-grams that appear too many times
        total_ngrams = len(tokens) - n + 1
        for ngram, count in ngrams.items():
            if count > self.repetition_max_count:
                coverage = (count * n) / total_ngrams
                if coverage > self.repetition_coverage_threshold:
                    return {
                        "ngram": " ".join(ngram),
                        "count": count,
                        "coverage": round(coverage, 3),
                        "description": (
                            f"'{ ' '.join(ngram) }' repeated {count} times "
                            f"covering {coverage:.1%} of tokens"
                        ),
                    }
        return None

    async def _check_audio_duration(self, path: str) -> dict:
        """Check audio file duration."""
        from bodhan_mcp.media import inspect_audio_video
        return inspect_audio_video(path)

    def _compute_similarity(self, source: str, target: str) -> float:
        """Compute normalized character-level similarity."""
        if not source or not target:
            return 0.0
        src_chars = set(re.sub(r"\s+", "", source))
        tgt_chars = set(re.sub(r"\s+", "", target))
        if not src_chars:
            return 1.0 if not tgt_chars else 0.0
        intersection = src_chars & tgt_chars
        union = src_chars | tgt_chars
        return len(intersection) / len(union) if union else 0.0


def format_srt(segments: list[dict[str, Any]], output_path: str) -> str:
    """Format segments into valid SRT subtitle file."""
    if not segments:
        Path(output_path).write_text("", encoding="utf-8")
        return output_path

    # Validate monotonic, non-overlapping timestamps
    prev_end = 0.0
    for seg in segments:
        start = seg.get("start_sec", 0.0)
        end = seg.get("end_sec", 0.0)
        if start < prev_end:
            raise ValueError(
                f"SRT timestamp overlap: segment starts at {start}s "
                f"but previous ended at {prev_end}s"
            )
        if end <= start:
            raise ValueError(
                f"SRT invalid duration: segment end ({end}s) <= start ({start}s)"
            )
        text = seg.get("text", "").strip()
        if not text:
            raise ValueError("SRT segment has empty text")
        prev_end = end

    lines = []
    for i, seg in enumerate(segments, 1):
        start = _format_timestamp(seg.get("start_sec", 0.0))
        end = _format_timestamp(seg.get("end_sec", 0.0))
        text = seg.get("text", "").replace("\n", "<br>")
        lines.append(f"{i}\n{start} --> {end}\n{text}")

    srt_content = "\n\n".join(lines)
    Path(output_path).write_text(srt_content, encoding="utf-8")
    return output_path


def _format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds % 1) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_artifact(output_path: str, content: str) -> str:
    """Write text/Markdown artifact to output path."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return str(out)


def parse_page_range(page_range_str: str, total_pages: int) -> tuple[int, int]:
    """Parse page range string like '1-3' or '5' into (start, end) 0-indexed."""
    start = 0
    end = total_pages
    if not page_range_str:
        return start, end
    parts = page_range_str.split("-")
    try:
        start = max(0, int(parts[0]) - 1)
        if len(parts) > 1:
            end = min(total_pages, int(parts[-1]))
        else:
            end = min(total_pages, start + 1)
    except (ValueError, IndexError):
        pass
    return start, end


def extract_untrusted_text(text: str) -> str:
    """Extract text from inside <untrusted_source_data> tags."""
    pattern = r"<untrusted_source_data>(.*?)</untrusted_source_data>"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1)
    return text


def sanitize_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """Sanitize arguments for logging (remove sensitive data)."""
    safe = {}
    for k, v in args.items():
        if k in ("text", "input", "content") and isinstance(v, str):
            if len(v) > 100:
                safe[k] = v[:100] + "...[truncated]"
            else:
                safe[k] = v
        else:
            safe[k] = v
    return safe
