"""Core data structures shared across pipeline stages.

Plain dataclasses (no pydantic) keep the dependency surface compile-free. Every
stage reads/writes these and they serialize straight to the JSON artifacts the
spec requires (transcript.en.json, segments.<lang>.json, report.json).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Segment:
    """One source-language unit on the original timeline.

    Produced by segmentation (pause-bounded speech runs). ``start``/``end`` are
    seconds on the source timeline and are the sync budget every dub must hit.
    """

    index: int
    start: float
    end: float
    text: str = ""              # English transcription (filled by transcribe stage)
    is_speech: bool = True       # False => music/silence span, never dubbed
    speaker: str | None = None   # stable label, e.g. "M1" / "F1"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(self.duration, 3)
        return d


@dataclass
class QAResult:
    """Per-segment quality measurements against the spec's targets."""

    cer: float | None = None               # char error rate: back-transcription vs translation
    sync_offset_ms: float | None = None    # start delta vs source segment
    speed_change: float | None = None      # applied time-stretch factor
    loudness_lufs: float | None = None
    back_transcript: str | None = None
    misbehavior: str | None = None         # "repeat" | "omission" | None
    within_sync: bool | None = None        # |offset| <= 300 ms
    within_speed: bool | None = None       # 0.85 <= speed <= 1.2

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DubSegment:
    """A source Segment rendered into one target language."""

    index: int
    start: float
    end: float
    source_text: str
    translation: str = ""
    script: str = "native"          # native | roman | codemix
    voice: str = ""
    style: str = ""              # "" => lang-only instructions (always valid); see qa.STYLE_FALLBACKS
    speaker: str | None = None
    audio_path: str | None = None
    audio_duration: float | None = None
    stretch: float = 1.0            # time-stretch factor actually applied
    fit_action: str = "none"        # none | pad | stretch | condense | overflow | skip
    condense_rounds: int = 0
    retries: int = 0
    qa: QAResult = field(default_factory=QAResult)
    status: str = "pending"         # pending | dubbed | skipped | failed
    reason: str | None = None       # why skipped/failed, for the coverage audit

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(max(0.0, self.end - self.start), 3)
        return d


@dataclass
class LanguageResult:
    """Per-language rollup for report.json."""

    lang: str
    status: str = "pending"         # ok | partial | failed
    segments: list[DubSegment] = field(default_factory=list)
    video_path: str | None = None
    subtitles_path: str | None = None
    burned_video_path: str | None = None
    error: str | None = None
    timings: dict = field(default_factory=dict)      # stage -> seconds
    cost_inr: float = 0.0
    retries: int = 0
