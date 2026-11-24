"""Verification helpers: check outputs before the agent finishes.

- Audio: back-transcribe generated speech and compare it with the script
  (character ratio + word overlap). Indic-Speak sometimes repeats or omits
  content, so a low score means retry once, then report honestly.
- Translation: coverage checks — non-empty, not truncated, sane length
  ratio, numbers preserved, not identical to the source.
- Subtitles: distribute a translation across transcript segments
  proportionally by source length, and format SRT.
"""

from __future__ import annotations

import difflib
import re

_DIGITS = re.compile(r"[0-9\u0966-\u096f\u09e6-\u09ef\u0be6-\u0bef\u0c66-\u0c6f]+")


def audio_fidelity(script: str, back_transcript: str) -> dict:
    """Compare a TTS script with its back-transcription.

    Returns the character ratio, word overlap and a pass flag (ratio >= 0.6
    and word overlap >= 0.5). A low score usually means the synthesis
    repeated or omitted content.
    """
    script_n = " ".join(script.split())
    back_n = " ".join(back_transcript.split())
    ratio = difflib.SequenceMatcher(None, script_n, back_n).ratio()
    script_words = set(script_n.lower().split())
    back_words = set(back_n.lower().split())
    overlap = (
        len(script_words & back_words) / max(1, len(script_words))
        if script_words
        else 0.0
    )
    return {
        "char_ratio": round(ratio, 3),
        "word_overlap": round(overlap, 3),
        "passed": ratio >= 0.6 and overlap >= 0.5,
    }


def _number_tokens(text: str) -> list[str]:
    return _DIGITS.findall(text or "")


def translation_coverage(source: str, translation: str, truncated: bool) -> dict:
    """Coverage checks for a translation (reference-free).

    A translation passes when it is non-empty, not truncated, within a
    sane length ratio of the source, preserves the source's numbers, and
    is not just a copy of the source.
    """
    src = source or ""
    tgt = translation or ""
    src_numbers = _number_tokens(src)
    tgt_numbers = _number_tokens(tgt)
    numbers_ok = len(tgt_numbers) >= len(src_numbers) or not src_numbers
    ratio = (len(tgt) / len(src)) if src else 0.0
    length_ok = 0.3 <= ratio <= 3.0 if src else False
    passed = bool(tgt.strip()) and not truncated and length_ok and numbers_ok
    return {
        "non_empty": bool(tgt.strip()),
        "not_truncated": not truncated,
        "length_ratio": round(ratio, 2),
        "length_ok": length_ok,
        "numbers_in_source": len(src_numbers),
        "numbers_in_translation": len(tgt_numbers),
        "numbers_preserved": numbers_ok,
        "identical_to_source": bool(src.strip()) and src.strip() == tgt.strip(),
        "passed": passed and src.strip() != tgt.strip(),
    }


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def align_translation_to_segments(translation: str, segments: list[dict]) -> list[dict]:
    """Split a full translation across transcript segments.

    Sentences are dealt to segments in proportion to each segment's source
    character count. Timing stays approximate; translating each segment
    separately is more accurate and is preferred when the planner does it.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", translation) if s.strip()]
    if not segments:
        return []
    if not sentences:
        return [{**seg, "translated_text": ""} for seg in segments]
    total_src = sum(len(seg.get("text", "")) for seg in segments) or 1
    out: list[dict] = []
    idx = 0
    for seg in segments:
        share = len(seg.get("text", "")) / total_src
        if seg is segments[-1]:
            take = len(sentences) - idx
        else:
            take = max(1, round(share * len(sentences)))
        take = min(take, len(sentences) - idx)
        out.append({**seg, "translated_text": " ".join(sentences[idx:idx + take])})
        idx += take
    return out


def make_srt(translated_segments: list[dict]) -> str:
    """Format translated segments as an SRT subtitle file."""
    lines = []
    for i, seg in enumerate(translated_segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(seg['start'])} --> {_srt_timestamp(seg['end'])}")
        lines.append(seg.get("translated_text") or seg.get("text", ""))
        lines.append("")
    return "\n".join(lines)
