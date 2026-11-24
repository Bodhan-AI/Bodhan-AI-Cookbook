"""Quality assurance: back-transcription fidelity + spec-target checks.

Content fidelity is measured by back-transcribing each dubbed segment with
Indic-Transcribe and computing per-segment character error rate against the
translation. We also flag Indic-Speak misbehaviour (repeats/omissions) from the
length ratio so the orchestrator can retry with an alternate voice/style, and
record whether each segment meets the sync (±300 ms) and speed (0.85–1.2×) targets.
"""

from __future__ import annotations

import io

import soundfile as sf

from ..logging_setup import get_logger
from ..types import DubSegment

log = get_logger("qa")

# Valid Indic-Speak styles to try when recovering from misbehaviour (an invalid
# style errors server-side), chosen to suit lecture/narration content.
STYLE_FALLBACKS = ["educational lecture", "news", "happy"]


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate = edit_distance / len(reference), in [0, ~1+]."""
    ref = "".join(reference.split())
    hyp = "".join(hypothesis.split())
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return round(prev[-1] / len(ref), 4)


def detect_misbehavior(translation: str, back: str) -> str | None:
    """Heuristic repeat/omission detector from length ratio + n-gram repetition."""
    t, b = "".join(translation.split()), "".join(back.split())
    if not t:
        return None
    ratio = len(b) / len(t)
    if ratio < 0.5:
        return "omission"
    if ratio > 1.8 or _has_long_repeat(back):
        return "repeat"
    return None


def _has_long_repeat(text: str) -> bool:
    words = text.split()
    n = len(words)
    for size in range(3, 7):
        seen = set()
        for i in range(n - size + 1):
            gram = " ".join(words[i : i + size])
            if gram in seen:
                return True
            seen.add(gram)
    return False


async def measure_segment(api, dub: DubSegment, lang_asr: str, settings) -> None:
    """Fill dub.qa: back-transcription CER, misbehaviour, sync/speed flags."""
    q = dub.qa
    q.speed_change = dub.stretch
    q.within_speed = settings.min_stretch - 1e-3 <= dub.stretch <= settings.max_stretch + 1e-3
    if q.sync_offset_ms is not None:
        q.within_sync = abs(q.sync_offset_ms) <= 300.0

    if settings.mock or dub.status != "dubbed" or not dub.audio_path:
        return  # back-transcription CER is meaningless against mock placeholders

    with open(dub.audio_path, "rb") as f:
        audio_bytes = f.read()
    data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
    dur = len(data) / sr if sr else 0.0
    back = await api.transcribe(audio_bytes, dur, lang=lang_asr)
    q.back_transcript = back
    q.cer = cer(dub.translation, back)
    q.misbehavior = detect_misbehavior(dub.translation, back)


def aggregate(dubs: list[DubSegment]) -> dict:
    dubbed = [d for d in dubs if d.status == "dubbed"]
    cers = [d.qa.cer for d in dubbed if d.qa.cer is not None]
    return {
        "segments_total": len(dubs),
        "segments_dubbed": len(dubbed),
        "segments_skipped": sum(1 for d in dubs if d.status == "skipped"),
        "segments_failed": sum(1 for d in dubs if d.status == "failed"),
        "sync_within_300ms": sum(1 for d in dubbed if d.qa.within_sync),
        "speed_within_band": sum(1 for d in dubbed if d.qa.within_speed),
        "mean_cer": round(sum(cers) / len(cers), 4) if cers else None,
        "misbehavior_flags": sum(1 for d in dubbed if d.qa.misbehavior),
        "total_retries": sum(d.retries for d in dubs),
    }
