"""Structured run log: every log record of a run also goes to <out>/run.log.jsonl, one JSON object per line.

Fields: t (ISO time), level, stage (pipeline stage, from the module), module, lang and seg (when known),
msg, and data (the structured fields a call passed with extra={"data": {...}}). Per-segment records carry
lang + seg, so `grep '"seg": 12'` or a few lines of Python show why one line sounds the way it does.

lang and seg come from context variables, set where a language or a segment starts in its own thread.
A record can also name them directly with extra={"lang": ..., "seg": ...}.
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import datetime
from pathlib import Path

LANG: contextvars.ContextVar[str | None] = contextvars.ContextVar("bodhan_dub_lang", default=None)
SEG: contextvars.ContextVar[int | None] = contextvars.ContextVar("bodhan_dub_seg", default=None)

LOG_NAME = "run.log.jsonl"

# module -> pipeline stage (names match report.json timings_s)
STAGES = {
    "audio": "extract_audio", "separate": "separation", "vad": "asr", "chunking": "asr", "transcribe": "asr",
    "align": "align", "punctuate": "english", "sentences": "english", "review": "english", "units": "english",
    "translate": "translate", "glossary": "translate", "dub_language": "dub", "fit": "dub", "pausefit": "dub",
    "tts": "dub", "condense": "dub", "qa": "dub", "schedule": "dub", "stretch": "dub", "assemble": "assemble",
    "subtitles": "assemble", "sync_check": "verify", "llm": "llm", "api": "api", "pipeline": "pipeline",
}


def _jsonable(x):
    try:
        json.dumps(x)
        return x
    except (TypeError, ValueError):
        return str(x)


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        module = record.name.rsplit(".", 1)[-1] if record.name.startswith("bodhan_dub") else record.name
        out = {"t": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
               "level": record.levelname, "stage": STAGES.get(module, "other"), "module": module,
               "lang": getattr(record, "lang", None) or LANG.get(),
               "seg": getattr(record, "seg", None) if getattr(record, "seg", None) is not None else SEG.get(),
               "msg": record.getMessage()}
        data = getattr(record, "data", None)
        if data is not None:
            out["data"] = {k: _jsonable(v) for k, v in data.items()} if isinstance(data, dict) else _jsonable(data)
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def attach_run_log(out_dir: str | Path, level: int = logging.INFO) -> logging.Handler:
    """Add a JSON-lines file handler for one run (appends, so a resumed run keeps its earlier lines).
    The root logger is raised to `level` if it is quieter, so the file gets INFO records even when the
    caller never configured logging."""
    path = Path(out_dir) / LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    h = logging.FileHandler(path, encoding="utf-8")
    h.setLevel(level)
    h.setFormatter(JsonLinesFormatter())
    root = logging.getLogger()
    root.addHandler(h)
    h.previous_root_level = root.level      # restored by detach_run_log
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return h


def detach_run_log(h: logging.Handler) -> None:
    root = logging.getLogger()
    root.removeHandler(h)
    root.setLevel(getattr(h, "previous_root_level", root.level))
    h.close()
