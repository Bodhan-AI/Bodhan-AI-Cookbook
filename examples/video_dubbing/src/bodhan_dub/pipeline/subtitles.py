"""Stage: write target-language subtitles (SRT, plus VTT) from dubbed segments."""

from __future__ import annotations

import platform

from ..types import DubSegment

# Fonts that cover each Indic script, chosen per platform so --burn-subs renders
# real glyphs (not tofu). macOS ships these system fonts; Linux/Docker uses Noto
# (installed via the Dockerfile). Override with --sub-font for anything else.
_MAC_FONTS = {
    "hi": "Kohinoor Devanagari", "mr": "Kohinoor Devanagari", "ta": "Tamil Sangam MN",
    "bn": "Bangla Sangam MN", "as": "Bangla Sangam MN", "te": "Kohinoor Telugu",
    "kn": "Kannada Sangam MN", "ml": "Malayalam Sangam MN", "gu": "Gujarati Sangam MN",
    "pa": "Gurmukhi MN", "or": "Oriya Sangam MN", "ur": "Geeza Pro",
}
_NOTO_FONTS = {
    "hi": "Noto Sans Devanagari", "mr": "Noto Sans Devanagari", "ta": "Noto Sans Tamil",
    "bn": "Noto Sans Bengali", "as": "Noto Sans Bengali", "te": "Noto Sans Telugu",
    "kn": "Noto Sans Kannada", "ml": "Noto Sans Malayalam", "gu": "Noto Sans Gujarati",
    "pa": "Noto Sans Gurmukhi", "or": "Noto Sans Oriya", "ur": "Noto Nastaliq Urdu",
}


def default_sub_font(lang: str) -> str:
    table = _MAC_FONTS if platform.system() == "Darwin" else _NOTO_FONTS
    return table.get(lang, "")


def _ts(seconds: float, sep: str) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(dubs: list[DubSegment], path: str) -> None:
    lines = []
    i = 1
    for d in sorted(dubs, key=lambda x: x.start):
        if not d.translation.strip():
            continue
        lines.append(str(i))
        lines.append(f"{_ts(d.start, ',')} --> {_ts(d.end, ',')}")
        lines.append(d.translation.strip())
        lines.append("")
        i += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_vtt(dubs: list[DubSegment], path: str) -> None:
    lines = ["WEBVTT", ""]
    for d in sorted(dubs, key=lambda x: x.start):
        if not d.translation.strip():
            continue
        lines.append(f"{_ts(d.start, '.')} --> {_ts(d.end, '.')}")
        lines.append(d.translation.strip())
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
