import json
import time
from pathlib import Path


class Trace:
    """Writes one JSON line per event, so a run can be replayed step by step."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.write_text("", encoding="utf-8")
        self.started = time.time()

    def log(self, **fields) -> None:
        fields = {"t": round(time.time() - self.started, 2), **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False, default=str) + "\n")