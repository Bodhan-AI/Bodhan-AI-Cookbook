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

from .languages import LANGUAGE_NAMES, STYLES, VOICES
from .vad import SAMPLE_RATE

log = logging.getLogger(__name__)

GENDER_SPLIT_HZ = 165.0  # typical boundary between adult male and female speaking pitch


@dataclass
class VoiceConfig:
    per_lang: dict[str, dict[str, str]] = field(default_factory=dict)  # lang -> {"male": v, "female": v}
    style: str | None = None


_VOICE_GENDER = {name.lower(): (name, gender) for genders in VOICES.values()
                 for gender, names in genders.items() for name in names}
_LANG_BY_NAME = {**{code: code for code in VOICES}, **{name.lower(): code for code, name in LANGUAGE_NAMES.items()
                                                      if code in VOICES}}


def load_voice_config(path: Path) -> tuple[VoiceConfig, list[str]]:
    """Read a voices file in any reasonable shape. Returns (config, warnings); never raises.

        hi: Kavya                  # one voice for every speaker
        ta: {male: Arun, female: Anitha}
        bn: [Ishita, Sourav]       # a list: each voice's gender comes from Indic-Speak's library
        Tamil: arun                # language names and voice names are case-insensitive
        default: Amit              # any language not listed (any voice can read any language)
        style: educational lecture
    Unknown languages, voices or styles become warnings in report.json, never a failed run.
    """
    cfg, warnings = VoiceConfig(), []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return cfg, [f"voices {path.name}: unreadable, ignored ({str(exc).splitlines()[0][:120]})"]
    if isinstance(data, dict) and isinstance(data.get("voices"), dict):
        data = {**data.pop("voices"), **data}
    if not isinstance(data, dict):
        return cfg, [f"voices {path.name}: expected a mapping like 'hi: Kavya'; ignored"]
    style = data.pop("style", None)
    if style is not None:
        match = next((s for s in STYLES if s.lower() == str(style).strip().lower()), None)
        if match is None:
            warnings.append(f"voices {path.name}: unknown style {style!r}; using the neutral voice")
        cfg.style = match
    for key, value in data.items():
        target = "default" if str(key).lower() in ("default", "all", "*") else _LANG_BY_NAME.get(str(key).lower())
        if target is None:
            warnings.append(f"voices {path.name}: unknown language {key!r}; skipped")
            continue
        chosen = _voices_from(value, f"voices {path.name}: {key}", warnings)
        if chosen:
            cfg.per_lang[target] = {"male": chosen.get("male") or chosen.get("female"),
                                    "female": chosen.get("female") or chosen.get("male")}
    return cfg, warnings


def _voices_from(value: object, where: str, warnings: list[str]) -> dict[str, str]:
    """A voice name, a list of names or a {male, female} mapping -> {gender: canonical voice name}."""
    if isinstance(value, dict):
        named = [(str(g).lower(), v) for g, v in value.items()]
    elif isinstance(value, list):
        named = [(None, v) for v in value]
    else:
        named = [(None, value)]
    out: dict[str, str] = {}
    for slot, raw in named:
        found = _VOICE_GENDER.get(str(raw).strip().lower())
        if found is None:
            warnings.append(f"{where}: unknown voice {raw!r}; skipped")
            continue
        name, gender = found
        out[slot if slot in ("male", "female") else gender] = name
    return out


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


@dataclass(frozen=True)
class Speaker:
    label: int  # 0 = lower-pitched speaker, 1 = higher-pitched
    pitch_hz: float | None
    gender: str  # which voice list to draw from
    slot: int = 0  # index within that list (a second voice when both speakers share a gender)


# Two pitch clusters count as two speakers only if they are clearly apart: centroids at least
# 15% apart, well separated relative to their spread, and each holding a real share of the speech.
MIN_SPEAKER_RATIO = 1.15
MIN_SEPARATION = 2.0
MIN_SHARE = 0.1
# Male and female speaking pitch differ by ~1.5-2x; two women or two men are usually much closer.
MALE_FEMALE_RATIO = 1.25


def plan_speakers(pitches: Sequence[float | None], durations: Sequence[float]) -> tuple[list[int], dict[int, Speaker]]:
    """Label each speech region with a speaker by clustering pitch, and pick a voice gender per speaker.

    There is no diarization model, so this tells speakers apart only by pitch (relative, not an
    absolute male/female cut-off: Indic-Speak's own male voice speaks at about 180 Hz). Regions
    without a pitch estimate take their neighbour's label.
    """
    valid = [(i, float(np.log(p))) for i, p in enumerate(pitches) if p]
    labels: list[int | None] = [None] * len(pitches)
    two = False
    if len(valid) >= 4:
        order = sorted(valid, key=lambda iv: iv[1])
        values = np.array([v for _, v in order])
        best = (0.0, 0)
        for k in range(2, len(values) - 1):
            lo, hi = values[:k], values[k:]
            pooled = np.sqrt((lo.var() * len(lo) + hi.var() * len(hi)) / len(values)) + 1e-3
            best = max(best, ((hi.mean() - lo.mean()) / pooled, k))
        score, k = best
        if score:
            lo_ids = {i for i, _ in order[:k]}
            centroids = (values[:k].mean(), values[k:].mean())
            share_lo = sum(durations[i] for i in lo_ids) / max(1e-6, sum(durations[i] for i, _ in valid))
            two = (np.exp(centroids[1] - centroids[0]) >= MIN_SPEAKER_RATIO and score >= MIN_SEPARATION
                   and MIN_SHARE <= share_lo <= 1 - MIN_SHARE)
            if two:
                for i, value in valid:
                    labels[i] = 0 if abs(value - centroids[0]) <= abs(value - centroids[1]) else 1
    if not two:
        labels = [0 if p else None for p in pitches]
    for i in range(len(labels)):  # fill gaps from the previous, else the next, labelled region
        if labels[i] is None:
            labels[i] = next((labels[j] for j in range(i - 1, -1, -1) if labels[j] is not None),
                             next((labels[j] for j in range(i + 1, len(labels)) if labels[j] is not None), 0))
    final = [int(x) for x in labels]

    speakers: dict[int, Speaker] = {}
    for label in sorted(set(final)):
        hz = [p for p, lab in zip(pitches, final, strict=True) if p and lab == label]
        speakers[label] = Speaker(label, float(np.median(hz)) if hz else None,
                                  "male" if hz and np.median(hz) < GENDER_SPLIT_HZ else "female")
    if len(speakers) == 2 and speakers[0].pitch_hz and speakers[1].pitch_hz:
        low, high = speakers[0], speakers[1]
        if high.pitch_hz / low.pitch_hz >= MALE_FEMALE_RATIO:
            # Far apart, as a man and a woman typically are: lower gets the male voice even if it is
            # above the textbook cut-off (Indic-Speak's own male voice speaks near 180 Hz).
            speakers[0], speakers[1] = Speaker(0, low.pitch_hz, "male"), Speaker(1, high.pitch_hz, "female")
        elif low.gender == high.gender:
            # Close and on the same side: two voices of that gender where the language has two.
            speakers[0] = Speaker(0, low.pitch_hz, low.gender, slot=1)
    return final, speakers


def voice_for_speaker(lang: str, speaker: Speaker, cfg: VoiceConfig) -> str:
    """The speaker's voice: from --voices if given, else the language's library (second voice if asked)."""
    if lang in cfg.per_lang or "default" in cfg.per_lang:
        return voice_for(lang, speaker.gender, cfg)
    names = VOICES[lang].get(speaker.gender) or []
    if speaker.slot < len(names):
        return names[speaker.slot]
    other = "male" if speaker.gender == "female" else "female"
    return (VOICES[lang].get(other) or names)[0] if speaker.slot else names[0]


def voice_for(lang: str, gender: str, cfg: VoiceConfig) -> str:
    if lang in cfg.per_lang:
        return cfg.per_lang[lang][gender]
    if "default" in cfg.per_lang:
        return cfg.per_lang["default"][gender]
    names = VOICES[lang].get(gender) or VOICES[lang]["female"]
    return names[0]
