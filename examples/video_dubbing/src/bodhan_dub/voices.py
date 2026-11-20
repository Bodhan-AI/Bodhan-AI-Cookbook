"""Voice choice: who is speaking (by pitch), and which Indic-Speak voice reads each unit."""

import logging
from pathlib import Path

import numpy as np
import yaml

from . import audio

log = logging.getLogger(__name__)

# Indic-Speak voices (female, male) for each production-tier language.
VOICES = {
    "hi": ("Kavya", "Amit"), "bn": ("Ishita", "Sourav"), "mr": ("Anagha", "Chinmay"), "te": ("Sravani", "Vamsi"),
    "ta": ("Anitha", "Arun"), "gu": ("Dhara", "Parth"), "kn": ("Deepika", "Adarsh"), "ml": ("Lakshmi", "Kiran"),
    "or": ("Itishree", "Akash"), "pa": ("Kaur", "Manpreet"), "as": ("Prastuti", "Ankur"), "ur": ("Saba", "Zaid"),
    "en": ("Kavya", "Amit"),  # every voice speaks English; these are the Hindi pair
}
# Every voice in the library; any voice can read any language.
ALL_VOICES = {
    "Prastuti", "Ankur", "Ishita", "Sourav", "Gwrbw", "Sansuma", "Preeti", "Sham", "Dhara", "Parth", "Kavya",
    "Suhani", "Amit", "Deepika", "Adarsh", "Anjali", "Sandeep", "Zoon", "Ishfaq", "Vaidehi", "Madhukar", "Lakshmi",
    "Kiran", "Thoibi", "Chaoba", "Anagha", "Chinmay", "Srijana", "Sagar", "Itishree", "Akash", "Kaur", "Manpreet",
    "Bharati", "Aryaman", "Phulmani", "Sibu", "Moomal", "Rano", "Anitha", "Arun", "Sravani", "Vamsi", "Saba", "Zaid",
}

GENDER_SPLIT_HZ = 165  # typical boundary between male (~85-155 Hz) and female (~165-255 Hz) voices
TWO_SPEAKER_RATIO = 1.45  # two pitch groups must be this far apart to count as different speakers
MIN_SHARE = 0.15  # ... and each must hold at least this share of the speech


def load_overrides(path: Path) -> dict:
    """Read --voices. Per language: a voice name for everyone, "male"/"female" for everyone,
    or a mapping {female: Name, male: Name} to set the voice for each detected speaker gender.

        hi: Amit
        ta: female
        bn: {female: Ishita, male: Sourav}
    """
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("voices file must map language codes to a voice, 'male'/'female', or {female:, male:}")
    for lang, value in data.items():
        names = value.values() if isinstance(value, dict) else [value]
        for name in names:
            if name not in ALL_VOICES and name not in ("male", "female"):
                raise ValueError(f"unknown voice {name!r} for {lang}; choose from {sorted(ALL_VOICES)}")
    return data


def speaker_genders(units: list[dict], wav: Path) -> tuple[list[str], dict]:
    """Decide, per unit, whether a male or female voice should read it.

    Per-unit pitch is noisy (one narrator ranges 160-270 Hz; an excited speaker jumps up), so the
    decision is made for the whole video first: only if unit pitches form two clearly separate
    groups, one on each side of GENDER_SPLIT_HZ, is the video treated as two speakers, and each
    unit takes the gender of the nearer group. Otherwise every unit gets the video's gender.
    """
    pitch = np.array([audio.median_pitch(wav, [(u["start"], u["end"])]) for u in units])
    weight = np.array([u["end"] - u["start"] for u in units])
    voiced = pitch > 0
    if not voiced.any():
        return ["female"] * len(units), {"speakers": 1, "reason": "no pitch found"}

    overall = _weighted_median(pitch[voiced], weight[voiced])
    low, high, share = _two_groups(pitch[voiced], weight[voiced])
    two = high / low >= TWO_SPEAKER_RATIO and low < GENDER_SPLIT_HZ <= high and share >= MIN_SHARE
    if not two:
        gender = "female" if overall >= GENDER_SPLIT_HZ else "male"
        info = {"speakers": 1, "gender": gender, "median_pitch_hz": round(overall)}
        log.info("one speaker gender: %s (median pitch %.0f Hz)", gender, overall, extra={"data": info})
        return [gender] * len(units), info

    genders, last = [], "female" if overall >= GENDER_SPLIT_HZ else "male"
    for p in pitch:
        if p > 0:  # nearer group on a log scale; unvoiced units keep the previous speaker
            last = "female" if abs(np.log(p / high)) < abs(np.log(p / low)) else "male"
        genders.append(last)
    info = {"speakers": 2, "male_pitch_hz": round(low), "female_pitch_hz": round(high),
            "units_female": genders.count("female"), "units_male": genders.count("male")}
    log.info("two speaker genders: male ~%.0f Hz, female ~%.0f Hz", low, high, extra={"data": info})
    return genders, info


def voice_for(lang: str, gender: str, overrides: dict) -> str:
    choice = overrides.get(lang)
    if isinstance(choice, dict):
        return choice.get(gender) or VOICES[lang][gender == "male"]
    if choice in ("male", "female"):
        return VOICES[lang][choice == "male"]
    if choice:
        return choice
    return VOICES[lang][gender == "male"]


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cum, cum[-1] / 2)])


def _two_groups(values: np.ndarray, weights: np.ndarray) -> tuple[float, float, float]:
    """Best split of sorted log-pitches into two groups (1-D, weighted). Returns
    (low centre Hz, high centre Hz, smaller group's share of the weight)."""
    order = np.argsort(values)
    v, w = np.log(values[order]), weights[order]
    if len(v) < 2:
        c = float(np.exp(v[0]))
        return c, c, 0.0
    best = None
    for k in range(1, len(v)):
        lo, hi = np.average(v[:k], weights=w[:k]), np.average(v[k:], weights=w[k:])
        sse = (w[:k] * (v[:k] - lo) ** 2).sum() + (w[k:] * (v[k:] - hi) ** 2).sum()
        if best is None or sse < best[0]:
            best = (sse, lo, hi, min(w[:k].sum(), w[k:].sum()) / w.sum())
    _, lo, hi, share = best
    return float(np.exp(lo)), float(np.exp(hi)), float(share)
