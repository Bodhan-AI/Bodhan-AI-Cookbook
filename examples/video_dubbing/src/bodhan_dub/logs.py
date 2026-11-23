"""Structured logging: JSON-lines file for the report/UI, plus one readable line per event on the console
(this is what appears in Render / Docker logs). Never logs API keys."""
import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_path: Path | None = None
_echo = True
_tag = ""
BAD = ("fatal", "failed", "error")
WARN = ("retry", "rejected", "not_found", "skipped", "degraded")


def setup(path: Path | None, echo: bool = True, tag: str = "") -> None:
    global _path, _echo, _tag
    _path, _echo, _tag = path, echo, tag
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)


def level_of(event: str, fields: dict) -> str:
    if any(b in event for b in BAD) or fields.get("error"):
        return "ERROR"
    if any(w in event for w in WARN) or (isinstance(fields.get("status"), int) and fields["status"] >= 400):
        return "WARN"
    return "INFO"


def log(event: str, **fields) -> None:
    now = time.time()
    rec = {"t": round(now, 3), "event": event, **fields}
    line = json.dumps(rec, ensure_ascii=False, default=str)
    with _lock:
        if _path:
            try:
                with open(_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:     # logging must never take a job down (e.g. the project folder was deleted)
                pass
        if _echo:
            stamp = time.strftime("%H:%M:%S", time.gmtime(now))
            detail = " ".join(f"{k}={str(v)[:300]}" for k, v in fields.items())
            tag = f"[{_tag}] " if _tag else ""
            print(f"{stamp}Z {level_of(event, fields):5} {tag}{event} {detail}".rstrip(), flush=True)
