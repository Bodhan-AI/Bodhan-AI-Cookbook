import json
import pathlib
import time
import uuid
from typing import Any, IO


class Tracer:
    """Writes a JSONL trace of every agent event to <out_dir>/trace.jsonl."""

    def __init__(self, out_dir: pathlib.Path) -> None:
        self.out_dir = out_dir
        self.run_id: str = str(uuid.uuid4())[:8]
        self._fh: IO[str] | None = None
        self._step: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._fh = open(  # noqa: WPS515
            self.out_dir / "trace.jsonl", "w", encoding="utf-8"
        )

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, event_type: str, **kwargs: Any) -> None:
        entry: dict[str, Any] = {
            "run_id": self.run_id,
            "step": self._step,
            "ts": time.time(),
            "event": event_type,
            **kwargs,
        }
        if self._fh is None:
            raise RuntimeError("Tracer.open() must be called before log()")
        self._fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        self._step += 1
