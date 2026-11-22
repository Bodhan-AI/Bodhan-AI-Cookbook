"""SRT subtitles from timed segments.

Indic-Transcribe returns text without word times, so each segment's time span comes from its audio window.
A long segment is split into readable cues (two lines of about 42 characters, at most about 6 s each) with time
shared in proportion to characters, the usual approximation when word timings are unavailable.
"""

from __future__ import annotations

from .textproc import split_sentences


def fmt_ts(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def build_cues(segments: list[dict], max_chars_per_line: int = 42, max_cue_s: float = 6.0) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        start, end = float(seg["start"]), float(seg["end"])
        if not text or end <= start:
            continue
        pieces: list[str] = []
        for sentence in split_sentences(text) or [text]:
            lines = _wrap(sentence, max_chars_per_line)
            for i in range(0, len(lines), 2):
                pieces.append("\n".join(lines[i: i + 2]))
        total_chars = sum(len(p) for p in pieces) or 1
        span = end - start
        t = start
        for p in pieces:
            dur = span * len(p) / total_chars
            cue_end = min(end, t + max(dur, 0.8))
            cues.append((t, cue_end if p is not pieces[-1] else end, p))
            t = cue_end
        # very long single cues are split evenly
    out: list[tuple[float, float, str]] = []
    for s, e, p in cues:
        if e - s > max_cue_s * 2 and "\n" in p:
            a, b = p.split("\n", 1)
            mid = s + (e - s) * len(a) / max(1, len(a) + len(b))
            out += [(s, mid, a), (mid, e, b)]
        else:
            out.append((s, e, p))
    # Audio windows overlap by 0.5 s at hard cuts; subtitles must not, so each cue starts where the last one ended.
    fixed: list[tuple[float, float, str]] = []
    for s, e, p in out:
        if fixed and s < fixed[-1][1]:
            s = fixed[-1][1]
        fixed.append((s, max(e, s + 0.5), p))
    return fixed


def to_srt(cues: list[tuple[float, float, str]]) -> str:
    return "\n".join(f"{i}\n{fmt_ts(s)} --> {fmt_ts(e)}\n{text}\n" for i, (s, e, text) in enumerate(cues, start=1))
