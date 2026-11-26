"""Session record: every final caption of a meeting, in both languages.

While the app runs, final captions are appended to out/sessions/<date>_<time>.jsonl (one JSON
object per line, flushed each time, so a crash loses nothing). On exit the same folder gets a
Markdown transcript and an SRT subtitle file; `python -m tools.export_session <file.jsonl>`
rebuilds them.

JSONL format: a first line {"type": "session", ...} with the languages and start time, then one
{"type": "caption", ...} per final caption. A caption with "replaces" continues an earlier one
(a merged sentence) and supersedes it in the exports. Times are seconds from the session start.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from app.bodhan.languages import language_name
from app.pipeline.common import UI, Caption

log = logging.getLogger(__name__)
MIN_SUBTITLE_S = 1.2  # an SRT entry stays up at least this long, even for "हाँ"


@dataclass
class Entry:
    direction: str         # "in" = they spoke, "out" = you spoke
    seq: int
    start: float           # seconds from the session start
    end: float
    original: str
    translated: str
    replaces: Optional[int] = None


# --------------------------------------------------------------------------- recording

class SessionRecorder:
    def __init__(self, path: Path, meta: dict[str, Any], clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self._clock = clock
        self._t0 = clock()
        self._count = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        self._write({"type": "session", "started": datetime.now().isoformat(timespec="seconds"), **meta})

    @classmethod
    def create(cls, folder: str, meta: dict[str, Any]) -> "SessionRecorder":
        return cls(Path(folder) / f"{datetime.now():%Y-%m-%d_%H%M%S}.jsonl", meta)

    def record(self, caption: Caption) -> None:
        if not caption.final:
            return
        now = self._clock()
        start = caption.start if caption.start is not None else now
        end = caption.end if caption.end is not None else now
        self._write({"type": "caption", "dir": caption.direction, "seq": caption.seq,
                     "start": round(start - self._t0, 2), "end": round(max(end, start) - self._t0, 2),
                     "original": caption.original, "translated": caption.translated,
                     "replaces": caption.replaces})
        self._count += 1

    def close(self) -> list[Path]:
        """Close the file and write the Markdown and SRT exports. Returns the files kept."""
        self._file.close()
        if self._count == 0:
            self.path.unlink(missing_ok=True)  # nothing was said: leave no clutter
            return []
        return [self.path, *export(self.path)]

    def _write(self, obj: dict[str, Any]) -> None:
        self._file.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._file.flush()


class RecordingUI:
    """The pipelines' UI: shows everything on the overlay and records the final captions."""

    def __init__(self, ui: UI, recorder: SessionRecorder) -> None:
        self._ui = ui
        self._recorder = recorder

    def show_caption(self, caption: Caption) -> None:
        self._ui.show_caption(caption)
        try:
            self._recorder.record(caption)
        except OSError:
            log.exception("Could not write the session record")

    def set_status(self, text: str, active: bool = False) -> None:
        self._ui.set_status(text, active)


# ----------------------------------------------------------------------------- export

def load(path: Path) -> tuple[dict[str, Any], list[Entry]]:
    meta: dict[str, Any] = {}
    entries: list[Entry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("type") == "session":
            meta = obj
        elif obj.get("type") == "caption":
            entries.append(Entry(obj["dir"], obj["seq"], obj["start"], obj["end"], obj["original"],
                                 obj["translated"], obj.get("replaces")))
    return meta, entries


def resolve(entries: Iterable[Entry]) -> list[Entry]:
    """Drop captions that a merged caption superseded (keeping the earliest start), sort by time."""
    current: dict[tuple[str, int], Entry] = {}
    for e in entries:
        if e.replaces is not None:
            earlier = current.pop((e.direction, e.replaces), None)
            if earlier is not None:
                e = Entry(e.direction, e.seq, min(e.start, earlier.start), e.end, e.original,
                          e.translated, e.replaces)
        current[(e.direction, e.seq)] = e
    return sorted(current.values(), key=lambda e: e.start)


def _labels(meta: dict[str, Any]) -> dict[str, str]:
    mine, theirs = language_name(meta.get("my_language", "?")), language_name(meta.get("their_language", "?"))
    return {"in": f"Them ({theirs} → {mine})", "out": f"You ({mine} → {theirs})"}


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def to_markdown(meta: dict[str, Any], entries: list[Entry]) -> str:
    labels = _labels(meta)
    started = meta.get("started", "")
    title = f"# Meeting transcript, {started.replace('T', ' ')[:16]}" if started else "# Meeting transcript"
    length = _clock(max((e.end for e in entries), default=0))
    theirs = language_name(meta.get("their_language", "?"))
    mine = language_name(meta.get("my_language", "?"))
    lines = [title, "",
             f"They spoke {theirs}, you spoke {mine}. Length {length}. Transcribed and translated by "
             f"Bodhan.AI (speech recognition: {meta.get('asr_backend', 'hosted')}).", ""]
    for e in entries:
        lines.append(f"**{_clock(e.start)} · {labels[e.direction]}**  ")
        lines.append(f"{e.original}  ")
        lines.append(f"→ {e.translated}" if e.translated and e.translated != e.original
                     else "→ *(no translation)*" if not e.translated else "")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms % 3_600_000 // 60_000:02d}:{ms % 60_000 // 1000:02d},{ms % 1000:03d}"


def to_srt(entries: list[Entry]) -> str:
    """Bilingual subtitles: the translation on top, the original under it."""
    blocks = []
    for i, e in enumerate(entries, start=1):
        who = "Them" if e.direction == "in" else "You"
        text = [f"{who}: {e.translated or e.original}"]
        if e.translated and e.translated != e.original:
            text.append(e.original)
        end = max(e.end, e.start + MIN_SUBTITLE_S)
        blocks.append(f"{i}\n{_srt_time(e.start)} --> {_srt_time(end)}\n" + "\n".join(text))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export(path: Path) -> list[Path]:
    """Write <name>.md and <name>.srt next to a session's JSONL file."""
    meta, entries = load(path)
    entries = resolve(entries)
    md, srt = path.with_suffix(".md"), path.with_suffix(".srt")
    md.write_text(to_markdown(meta, entries), encoding="utf-8")
    srt.write_text(to_srt(entries), encoding="utf-8")
    return [md, srt]
