"""Place dubbed lines on the timeline, mix over the original bed, mux, and write subtitles.

* Placement: each line starts at its source start, or just after the previous line if
  that one ran long, so lines never overlap. The shift is the reported sync error.
* Bed: the original audio stays, so music and effects keep playing. It is ducked to -26 dB,
  with short ramps, only where a dub line replaces the original speech (the source span of
  each dubbed unit and wherever its line actually plays). Music, vocals the LLM marked as not
  narration, and lines that could not be dubbed keep their original audio: no silent holes.
* Loudness: the dub speech track is normalised to -16 LUFS on its own before mixing.
* Length: the mix is built at exactly the input's duration and the video is stream-copied.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio, media
from .languages import LANGUAGE_NAMES
from .synth import SENTENCE_GAP_S, DubLine

log = logging.getLogger(__name__)

DUCK_GAIN = 10 ** (-26 / 20)
RAMP_S = 0.15
MIN_GAP_S = 0.08
TARGET_LUFS = -16.0
# A 15-minute bed at 48 kHz stereo float32 is ~350 MB and mixing copies it a few times; mixing
# takes seconds next to the API-bound stages, so languages take turns instead of tripling peak memory.
_MIX_LOCK = threading.Lock()
ISO639_2 = {"as": "asm", "bn": "ben", "brx": "brx", "doi": "doi", "gu": "guj", "hi": "hin", "kn": "kan",
            "kok": "kok", "ks": "kas", "mai": "mai", "ml": "mal", "mni": "mni", "mr": "mar", "ne": "nep",
            "or": "ori", "pa": "pan", "sa": "san", "sat": "sat", "sd": "snd", "ta": "tam", "te": "tel", "ur": "urd"}


def place(lines: Sequence[DubLine], video_s: float) -> None:
    cursor = 0.0
    for line in lines:
        if not line.sentences:
            continue
        line.placed_start = max(line.unit.start, cursor)
        line.placed_end = min(video_s, line.placed_start + line.spoken_s)
        cursor = line.placed_end + MIN_GAP_S


def line_audio(line: DubLine) -> np.ndarray:
    """Sentences joined with short gaps at TTS rate, time-stretched, at the mix rate."""
    rate = line.sentences[0].rate
    gap = np.zeros(int(SENTENCE_GAP_S * rate), dtype=np.float32)
    pieces: list[np.ndarray] = []
    for i, s in enumerate(line.sentences):
        if i:
            pieces.append(gap)
        pieces.append(s.samples if s.samples is not None else np.zeros(0, dtype=np.float32))
    return audio.stretch_and_resample(np.concatenate(pieces), rate, line.tempo)


def duck_envelope(n: int, intervals: Sequence[tuple[float, float]], rate: int = audio.MIX_RATE) -> np.ndarray:
    env = np.ones(n, dtype=np.float32)
    ramp = int(RAMP_S * rate)
    for start, end in intervals:
        a, b = max(0, int(start * rate) - ramp), min(n, int(end * rate) + ramp)
        if b <= a:
            continue
        seg = np.full(b - a, DUCK_GAIN, dtype=np.float32)
        r = min(ramp, (b - a) // 2)
        if r:
            seg[:r] = np.linspace(1.0, DUCK_GAIN, r)
            seg[-r:] = np.linspace(DUCK_GAIN, 1.0, r)
        env[a:b] = np.minimum(env[a:b], seg)
    return env


@dataclass
class MixResult:
    speech_lufs: float | None
    output_duration_s: float


def render(lines: Sequence[DubLine], source: Path, video_s: float, out_video: Path, work: Path, lang: str,
           has_video: bool) -> MixResult:
    with _MIX_LOCK:
        return _render(lines, source, video_s, out_video, work, lang, has_video)


def duck_intervals(lines: Sequence[DubLine]) -> list[tuple[float, float]]:
    """Where the original voice must give way: each dubbed unit's source span and its placed line."""
    spans: list[tuple[float, float]] = []
    for ln in lines:
        if ln.sentences:
            spans += [(ln.unit.start, ln.unit.end), (ln.placed_start, ln.placed_end)]
    return spans


def _render(lines: Sequence[DubLine], source: Path, video_s: float, out_video: Path, work: Path, lang: str,
            has_video: bool) -> MixResult:
    n = int(round(video_s * audio.MIX_RATE))
    dub = np.zeros(n, dtype=np.float32)
    for line in lines:
        if not line.sentences:
            continue
        clip = line_audio(line)
        a = int(line.placed_start * audio.MIX_RATE)
        b = min(n, a + len(clip))
        dub[a:b] += clip[: b - a]
    dub, speech_lufs = audio.normalize_loudness(dub, TARGET_LUFS)

    bed = audio.decode_audio(source, audio.MIX_RATE, 2)
    bed = np.pad(bed, ((0, max(0, n - len(bed))), (0, 0)))[:n]
    mix = bed * duck_envelope(n, duck_intervals(lines))[:, None] + dub[:, None]
    peak = float(np.max(np.abs(mix))) if n else 0.0
    if peak > 0.99:  # music peaks plus speech: scale the whole mix, never clip
        mix *= 0.99 / peak

    work.mkdir(parents=True, exist_ok=True)
    mix_wav = work / "mix.wav"
    mix_wav.write_bytes(audio.array_to_wav(mix, audio.MIX_RATE))
    (work / "dub_speech.wav").write_bytes(audio.array_to_wav(dub, audio.MIX_RATE))
    mux(source, mix_wav, out_video, lang, has_video)
    # No speech at all (a music-only video) has no measurable loudness: None, not -inf, keeps report.json valid JSON.
    lufs = round(speech_lufs, 2) if np.isfinite(speech_lufs) else None
    return MixResult(speech_lufs=lufs, output_duration_s=media.probe(out_video).duration_s)


def mux(source: Path, mix_wav: Path, out_video: Path, lang: str, has_video: bool) -> None:
    out_video.parent.mkdir(parents=True, exist_ok=True)
    meta = ["-metadata", f"title=AI-dubbed ({LANGUAGE_NAMES[lang]})",
            "-metadata", "comment=AI-dubbed with Bodhan Indic models; synthetic voice, not the original speaker",
            "-metadata:s:a:0", f"language={ISO639_2.get(lang, 'und')}", "-metadata:s:a:0", "title=AI dub"]
    audio_args = ["-c:a", "aac", "-b:a", "192k"]
    if not has_video:
        _run(["-i", str(mix_wav), "-map", "0:a:0", *audio_args, *meta, str(out_video)])
        return
    base = ["-i", str(source), "-i", str(mix_wav), "-map", "0:v:0", "-map", "1:a:0"]
    try:  # stream-copy keeps picture quality and the exact frame timing
        _run([*base, "-c:v", "copy", *audio_args, *meta, "-movflags", "+faststart", str(out_video)])
    except media.MediaError:
        log.warning("video stream copy into MP4 failed; re-encoding with H.264", extra={"stage": "mix", "lang": lang})
        _run([*base, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", *audio_args,
              *meta, "-movflags", "+faststart", str(out_video)])


def _run(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise media.MediaError(f"ffmpeg mux failed: {proc.stderr.strip()[-400:]}")


# ---------------------------------------------------------------------------
# subtitles
# ---------------------------------------------------------------------------

MAX_CUE_CHARS = 84  # two lines of ~42


def _ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _split_long(text: str) -> list[str]:
    words, chunks, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_CUE_CHARS:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [text]


def subtitle_cues(lines: Sequence[DubLine]) -> list[tuple[float, float, str]]:
    """One cue per spoken sentence, timed from the actual clip lengths (long ones split by length)."""
    cues: list[tuple[float, float, str]] = []
    for line in lines:
        t = line.placed_start
        for i, s in enumerate(line.sentences):
            if i:
                t += SENTENCE_GAP_S / line.tempo
            dur = s.best.duration_s / line.tempo
            chunks = _split_long(s.text)
            total = sum(len(c) for c in chunks)
            for c in chunks:
                d = dur * len(c) / total
                cues.append((t, min(t + d, line.placed_end), c))
                t += d
    return cues


AI_LABEL = "[AI-dubbed audio]"


def add_ai_label(cues: list[tuple[float, float, str]], video_s: float) -> list[tuple[float, float, str]]:
    """The brief requires labelling the dub: a cue of its own where there is room, else on the first cue."""
    if not cues:
        return [(0.0, min(3.0, video_s), AI_LABEL)]
    if cues[0][0] >= 1.2:
        return [(0.2, min(2.5, cues[0][0] - 0.2), AI_LABEL), *cues]
    if video_s - cues[-1][1] >= 1.2:
        return [*cues, (cues[-1][1] + 0.2, min(video_s, cues[-1][1] + 2.5), AI_LABEL)]
    start, end, text = cues[0]
    return [(start, end, f"{AI_LABEL}\n{text}"), *cues[1:]]


def write_srt(lines: Sequence[DubLine], path: Path, video_s: float) -> int:
    cues = add_ai_label(subtitle_cues(lines), video_s)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{text}\n" for i, (a, b, text) in enumerate(cues, 1))
    path.write_text(body, encoding="utf-8")
    return len(cues)
