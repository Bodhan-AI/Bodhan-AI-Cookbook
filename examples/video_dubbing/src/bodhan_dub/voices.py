"""Choose an Indic-Speak voice for every unit, keeping each speaker's voice consistent.

Indic-Transcribe has no diarization and most languages have one male and one female
voice, so we estimate each unit's speaking pitch from the source audio (plain
autocorrelation, no speech model) and map low voices to the male voice and high voices
to the female one. A unit only switches voice on clear evidence; otherwise it keeps the
previous unit's voice, so one speaker never flip-flops between voices.

`--voices` YAML overrides it all:
    hi: Kavya                      # this voice for every speaker
    ta: {male: Arun, female: Anitha}
    style: educational lecture     # optional Indic-Speak style for all languages
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .languages import ALL_VOICES, STYLES, VOICES
from .vad import SAMPLE_RATE

log = logging.getLogger(__name__)

GENDER_SPLIT_HZ = 165.0  # typical boundary between adult male and female speaking pitch
SWITCH_MARGIN_HZ = 20.0  # need this much past the split to change voice mid-video


class VoiceConfigError(ValueError):
    pass


@dataclass
class VoiceConfig:
    per_lang: dict[str, dict[str, str]] = field(default_factory=dict)  # lang -> {"male": v, "female": v}
    style: str | None = None


def load_voice_config(path: Path) -> VoiceConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise VoiceConfigError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise VoiceConfigError(f"{path}: expected a mapping like 'hi: Kavya'")
    cfg = VoiceConfig()
    style = data.pop("style", None)
    if style is not None and style not in STYLES:
        raise VoiceConfigError(f"{path}: style must be one of: {', '.join(STYLES)}")
    cfg.style = style
    for lang, value in data.items():
        if lang not in VOICES:
            raise VoiceConfigError(f"{path}: unknown language {lang!r}")
        mapping = {"male": value, "female": value} if isinstance(value, str) else value
        if not isinstance(mapping, dict) or not set(mapping) <= {"male", "female"}:
            raise VoiceConfigError(f"{path}: {lang} must be a voice name or {{male: .., female: ..}}")
        for voice in mapping.values():
            if voice not in ALL_VOICES:
                raise VoiceConfigError(f"{path}: unknown voice {voice!r} for {lang}")
        cfg.per_lang[lang] = {"male": mapping.get("male") or mapping.get("female"),
                              "female": mapping.get("female") or mapping.get("male")}
    return cfg


def median_pitch(samples: np.ndarray, start: float, end: float) -> float | None:
    """Median F0 in Hz over voiced 40 ms frames of [start, end), or None if too little voicing."""
    seg = samples[int(start * SAMPLE_RATE): int(end * SAMPLE_RATE)]
    frame, hop = int(0.04 * SAMPLE_RATE), int(0.02 * SAMPLE_RATE)
    lo_lag, hi_lag = SAMPLE_RATE // 400, SAMPLE_RATE // 70  # search 70-400 Hz
    pitches = []
    for i in range(0, len(seg) - frame, hop):
        x = seg[i: i + frame] - np.mean(seg[i: i + frame])
        energy = float(np.dot(x, x))
        if energy < 1e-4:
            continue
        ac = np.correlate(x, x, mode="full")[frame - 1:]
        lag = lo_lag + int(np.argmax(ac[lo_lag:hi_lag]))
        if ac[lag] / energy > 0.5:  # clearly periodic: voiced speech
            pitches.append(SAMPLE_RATE / lag)
    return float(np.median(pitches)) if len(pitches) >= 5 else None


def assign_genders(pitches: Sequence[float | None]) -> list[str]:
    """Male/female per unit with hysteresis, so a speaker keeps one voice."""
    known = [p for p in pitches if p is not None]
    current = "male" if (np.median(known) if known else 0.0) < GENDER_SPLIT_HZ else "female"
    out = []
    for p in pitches:
        if p is not None:
            if current == "female" and p < GENDER_SPLIT_HZ - SWITCH_MARGIN_HZ:
                current = "male"
            elif current == "male" and p > GENDER_SPLIT_HZ + SWITCH_MARGIN_HZ:
                current = "female"
        out.append(current)
    return out


def voice_for(lang: str, gender: str, cfg: VoiceConfig) -> str:
    if lang in cfg.per_lang:
        return cfg.per_lang[lang][gender]
    names = VOICES[lang].get(gender) or VOICES[lang]["female"]
    return names[0]
