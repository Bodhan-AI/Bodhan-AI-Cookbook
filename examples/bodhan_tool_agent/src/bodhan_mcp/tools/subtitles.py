"""
Generate standard SRT subtitle files from speech transcription segments.
"""
from __future__ import annotations

from pathlib import Path


def create_srt_subtitles(
    text: str,
    segments: list[dict] | dict | str | None,
    out_path: Path,
) -> dict:
    """Format and write an SRT subtitle file."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(segments, str):
        import ast
        try:
            segments = ast.literal_eval(segments)
        except Exception:
            try:
                import json
                segments = json.loads(segments)
            except Exception:
                segments = None

    if isinstance(segments, dict):
        if "segments" in segments and isinstance(segments["segments"], list):
            segments = segments["segments"]
        elif "start" in segments:
            segments = [segments]
        else:
            segments = None

    if not isinstance(segments, list):
        segments = None
    else:
        cleaned = []
        for seg in segments:
            if isinstance(seg, dict):
                cleaned.append({
                    "start": float(seg.get("start", 0.0)),
                    "end": float(seg.get("end", 0.0)),
                    "text": str(seg.get("text", "")),
                })
            elif isinstance(seg, str):
                cleaned.append({"start": 0.0, "end": 0.0, "text": seg})
        segments = cleaned

    if not segments:
        sentences = [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]
        segments = []
        t = 0.0
        for s in sentences:
            dur = max(2.5, len(s.split()) * 0.4)
            segments.append({"start": t, "end": t + dur, "text": s})
            t += dur
    elif text and len(segments) == 1:
        segments[0]["text"] = text

    lines = []
    for i, seg in enumerate(segments, 1):
        s = seg.get("start", 0.0)
        e = max(seg.get("end", 0.0), s + 1.0)
        s_h, s_m, s_s = int(s // 3600), int((s % 3600) // 60), s % 60
        e_h, e_m, e_s = int(e // 3600), int((e % 3600) // 60), e % 60
        s_str = f"{s_h:02d}:{s_m:02d}:{int(s_s):02d},{int((s_s % 1) * 1000):03d}"
        e_str = f"{e_h:02d}:{e_m:02d}:{int(e_s):02d},{int((e_s % 1) * 1000):03d}"
        lines.append(f"{i}\n{s_str} --> {e_str}\n{seg.get('text', '')}\n")

    srt_content = "\n".join(lines)
    out_path.write_text(srt_content, encoding="utf-8")
    return {"srt_path": str(out_path), "subtitle_count": len(segments)}
