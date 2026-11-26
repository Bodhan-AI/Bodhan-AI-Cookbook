"""Per-stage latency of the running session, for the settings window's engine readout.

The pipelines already log one INFO line per utterance ("in #3 2.4s audio | asr 0.81s mt 0.40s |
e2e 1.25s | ..."); tools.latency_report parses the same lines from log files. LatencyStats is a
logging handler that keeps the most recent values, so no pipeline needs to know about it.
"""
from __future__ import annotations

import logging
import re
import statistics
import threading
from collections import defaultdict, deque

UTTERANCE_LINE = re.compile(
    r"\b(?P<dir>in|out) #\d+ (?P<audio>[\d.]+)s audio \| asr (?P<asr>[\d.]+)s mt (?P<mt>[\d.]+)s"
    r"(?: tts (?P<tts>[\d.]+)s)? \| e2e (?P<e2e>[\d.]+)s \|(?: partial (?P<partial>[\d.]+)s \|)?"
)
STAGES = ("audio", "asr", "mt", "tts", "partial", "e2e")


def parse_line(text: str) -> tuple[str, dict[str, float]] | None:
    """(direction, {stage: seconds}) for one utterance log line, else None."""
    m = UTTERANCE_LINE.search(text)
    if not m:
        return None
    return m.group("dir"), {s: float(m.group(s)) for s in STAGES if m.group(s) is not None}


class LatencyStats(logging.Handler):
    """Median of the last `keep` utterances per direction and stage. Thread-safe."""

    def __init__(self, keep: int = 20) -> None:
        super().__init__(level=logging.INFO)
        self._keep = keep
        self._values: dict[str, dict[str, deque[float]]] = defaultdict(dict)
        self._counts: dict[str, int] = defaultdict(int)
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            parsed = parse_line(record.getMessage())
        except Exception:  # never let a stats problem break logging
            return
        if parsed is None:
            return
        direction, stages = parsed
        with self._guard:
            self._counts[direction] += 1
            for stage, value in stages.items():
                self._values[direction].setdefault(stage, deque(maxlen=self._keep)).append(value)

    def reset(self) -> None:
        with self._guard:
            self._values.clear()
            self._counts.clear()

    def snapshot(self) -> dict[str, tuple[int, dict[str, float]]]:
        """direction -> (utterances seen, {stage: median of the recent ones})."""
        with self._guard:
            return {d: (self._counts[d], {s: statistics.median(v) for s, v in stages.items() if v})
                    for d, stages in self._values.items()}
