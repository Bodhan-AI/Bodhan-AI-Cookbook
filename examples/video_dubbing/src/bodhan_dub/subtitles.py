"""Write SRT subtitles."""

from __future__ import annotations

from pathlib import Path


def srt_time(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


MIN_CUE = 0.2   # seconds; write_srt never shows a cue for less than this


def clip_cues(cues: list[tuple[float, float, str]], total: float) -> list[tuple[float, float, str]]:
    """Keep every cue inside [0, total]: a cue that would end after the video is cut at its end, one that would
    start too late to be shown is dropped. Measured 2026-11-20: a last cue running past a 90 s cut made the
    subtitle stream (and the MP4) 166-249 ms longer than the video, failing the length target."""
    out = []
    for start, end, text in cues:
        if start > total - MIN_CUE:
            continue
        out.append((start, min(max(end, start + MIN_CUE), total), text))
    return out


LABEL_MAX = 4.0   # seconds the AI-dubbed notice stays on screen when it gets its own cue
LABEL_MIN = 1.0   # shorter free time before the first line: the notice joins the first cue instead


def add_label_cue(cues: list[tuple[float, float, str]], label: str) -> list[tuple[float, float, str]]:
    """Put an AI-dubbed notice at the start of the subtitles. It gets its own cue when the first spoken line
    starts at least LABEL_MIN s in; otherwise it becomes the first line of the first cue, so it never hides or
    shifts a translated line."""
    if not label:
        return list(cues)
    first = next((k for k, c in enumerate(cues) if c[2].strip()), None)
    if first is None:
        return [(0.0, LABEL_MAX, label)]
    start = cues[first][0]
    if start >= LABEL_MIN:
        return [(0.0, min(LABEL_MAX, start - 0.1), label), *cues]
    out = list(cues)
    s, e, text = out[first]
    out[first] = (s, e, f"{label}\n{text}")
    return out


def write_srt(cues: list[tuple[float, float, str]], path: str | Path) -> Path:
    """cues: (start_sec, end_sec, text) in order. Cues with empty text are skipped."""
    lines = []
    n = 0
    for start, end, text in cues:
        if not text.strip():
            continue
        n += 1
        lines += [str(n), f"{srt_time(start)} --> {srt_time(max(end, start + 0.2))}", text.strip(), ""]
    path = Path(path)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
