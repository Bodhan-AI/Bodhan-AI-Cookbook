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
    similarity >= 0.75."""

    min_duration_sec: float = 0.5
    min_similarity: float = 0.75

    def verify(
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

        import asyncio
        try:
            audio_info = asyncio.run(
                self._check_audio_duration(audio_path)
            )
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

        try:
            transcription = asyncio.run(
                transcribe_func(audio_path)
            )
            results["back_transcribed"] = transcription.get("text", "")
            src = source_script
            tgt = transcription.get("text", "")
            sim = self._compute_similarity(src, tgt)
            results["similarity"] = round(sim, 4)
        except Exception as e:
            results["details"]["transcription_error"] = str(e)

        fe = results["file_exists"]
        do = results["duration_ok"]
        sm = results["similarity"] >= self.min_similarity
        results["passed"] = fe and do and sm
        results["details"]["min_duration_threshold"] = self.min_duration_sec
        results["details"]["similarity_threshold"] = self.min_similarity
        return results

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
    millis = int((seconds % 1) * 1000)
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
