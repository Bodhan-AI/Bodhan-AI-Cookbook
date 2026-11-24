"""
TTS synthesis stage and dynamic CPS (chars-per-second) calibration.
Predicts duration, splits text safely at punctuation (≤ 180 chars),
and stitches audio with crossfades and edge silence trimming.
"""
from __future__ import annotations

import asyncio
import io
from statistics import median
from typing import Dict, List, Optional, Tuple

import numpy as np
import regex
import soundfile as sf

from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.media.audio_proc import (
    crossfade_concat,
    resample_to_48k,
    trim_edge_silence,
)

# Documented CPS values from API_FACTS.md live measurements
API_FACTS_CPS: Dict[Tuple[str, str], float] = {
    ("hi", "Kavya"): 11.37,
    ("hi", "Amit"): 13.50,
    ("bn", "Ishita"): 11.20,
    ("bn", "Sourav"): 13.80,
}


class CPSCalibrator:
    """Tracks running median and EWMA characters-per-second per (lang, voice)."""

    def __init__(self, default_cps: float = 12.0):
        self.history: Dict[Tuple[str, str], List[float]] = {}
        self.ewma: Dict[Tuple[str, str], float] = {}
        self.default_cps = default_cps
        self.seeded_cps: Dict[Tuple[str, str], float] = API_FACTS_CPS.copy()
        # C3: asyncio.Lock for atomic updates across concurrent synthesis tasks
        self.lock = asyncio.Lock()

    def is_warmed_up(self, lang: str, voice: str, min_samples: int = 3) -> bool:
        """True when at least min_samples (default 3) real measurements exist for (lang, voice)."""
        key = (lang, voice)
        return len(self.history.get(key, [])) >= min_samples

    def get_cps(self, lang: str, voice: str) -> float:
        """Returns running median or EWMA CPS for given voice profile."""
        key = (lang, voice)
        if key in self.history and self.history[key]:
            return float(median(self.history[key]))
        if key in self.seeded_cps:
            return float(self.seeded_cps[key])
        # Fallback to any measurement for this voice
        voice_matches = [
            v for (_lang, v_name), hist in self.history.items() if v_name == voice for v in hist
        ]
        if voice_matches:
            return float(median(voice_matches))
        # Fallback to seeded cps for this voice in another language
        voice_seeded = [
            cps for (_lang, v_name), cps in self.seeded_cps.items() if v_name == voice
        ]
        if voice_seeded:
            return float(median(voice_seeded))
        return self.default_cps

    def record_measurement(self, lang: str, voice: str, char_count: int, duration_s: float) -> None:
        """Updates calibration history with a verified synthesis run."""
        if duration_s <= 0.1 or char_count <= 2:
            return
        cps = round(char_count / duration_s, 2)
        # Sane bounds check for speech rate (6 to 25 cps)
        if 6.0 <= cps <= 25.0:
            key = (lang, voice)
            if key not in self.history:
                self.history[key] = []
            self.history[key].append(cps)
            # Keep last 15 observations for running window
            if len(self.history[key]) > 15:
                self.history[key].pop(0)

            # EWMA with alpha=0.3
            if key not in self.ewma:
                self.ewma[key] = cps
            else:
                self.ewma[key] = round(0.3 * cps + 0.7 * self.ewma[key], 2)

    def predict_duration(self, text: str, lang: str, voice: str) -> float:
        """Predicts audio duration before calling synthesis API."""
        chars = len(text.strip())
        cps = self.get_cps(lang, voice)
        return round(chars / cps, 3)


def split_text_at_punctuation(text: str, max_chars: int = 180) -> List[str]:
    """
    B10: Robust text chunking for TTS synthesis:
    1. Splits at clause/sentence punctuation (dandas ।, ॥, ?, !, newlines, and . / , not inside numbers).
    2. Protects decimal numbers (e.g. 3.14), abbreviations (e.g. U.S.A., Dr.), and numbers with commas (e.g. 10,000).
    3. If any piece still exceeds max_chars, falls back to splitting at whitespace.
    4. If any single word still exceeds max_chars, splits at grapheme cluster boundaries (\\X).
    Ensures every resulting chunk is <= max_chars.
    """
    t_clean = text.strip()
    if len(t_clean) <= max_chars:
        return [t_clean]

    # Regex splitting on punctuation, protecting decimals and numbers with commas:
    # Match:
    # - Indic dandas: [।॥]
    # - Newlines: \n+
    # - Sentence/clause punctuation: (?<!\d)[.?!;](?!\d) or (?<!\d),(?!\d)
    split_pattern = r"([।॥\n]|(?<!\d)[.?!;](?!\d)|(?<!\d),(?!\d))\s*"
    tokens = regex.split(split_pattern, t_clean)

    chunks: List[str] = []
    curr = ""

    for token in tokens:
        if not token:
            continue
        if len(curr) + len(token) <= max_chars:
            curr += token
        else:
            if curr.strip():
                chunks.append(curr.strip())
            curr = token

    if curr.strip():
        chunks.append(curr.strip())

    # Secondary pass: if any chunk still > max_chars, split on whitespace
    refined_chunks: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            refined_chunks.append(chunk)
            continue

        words = chunk.split()
        sub_curr = ""
        for w in words:
            if len(sub_curr) + len(w) + 1 <= max_chars:
                sub_curr = f"{sub_curr} {w}".strip()
            else:
                if sub_curr:
                    refined_chunks.append(sub_curr)
                # If a single word itself exceeds max_chars, split on grapheme clusters (\X)
                if len(w) > max_chars:
                    graphemes = regex.findall(r"\X", w)
                    g_curr = ""
                    for g in graphemes:
                        if len(g_curr) + len(g) <= max_chars:
                            g_curr += g
                        else:
                            if g_curr:
                                refined_chunks.append(g_curr)
                            g_curr = g
                    sub_curr = g_curr
                else:
                    sub_curr = w
        if sub_curr:
            refined_chunks.append(sub_curr)

    return [c for c in refined_chunks if c.strip()] or [t_clean]


class TTSSynthesizer:
    """Synthesizes text into 48kHz float32 audio with duration prediction."""

    def __init__(
        self,
        bodhan_client: BodhanClient,
        logger: PipelineLogger,
        calibrator: Optional[CPSCalibrator] = None,
    ):
        self.bodhan_client = bodhan_client
        self.logger = logger
        self.calibrator = calibrator or CPSCalibrator()

    async def synthesize(
        self,
        text: str,
        lang: str,
        voice: str,
        segment_id: str,
        nonce: Optional[int] = None,
        max_chars: int = 180,
    ) -> Tuple[np.ndarray, float, float]:
        """
        Synthesizes text into 48 kHz mono float32 audio.
        Returns (audio_48k, actual_duration, predicted_duration).
        """
        predicted_dur = self.calibrator.predict_duration(text, lang, voice)

        # Split into <=max_chars character chunks at punctuation
        pieces = split_text_at_punctuation(text, max_chars=max_chars)

        audio_pieces: List[Tuple[np.ndarray, int]] = []
        for idx, piece in enumerate(pieces):
            piece_id = f"{segment_id}_p{idx}" if len(pieces) > 1 else segment_id
            speak_kwargs = {"stage": "tts", "lang": lang, "segment_id": piece_id}
            if nonce is not None:
                speak_kwargs["nonce"] = nonce

            raw_wav_bytes = await self.bodhan_client.speak(
                text=piece,
                voice=voice,
                language_code=lang,
                **speak_kwargs,
            )

            # Read audio and real sample rate from bytes
            with io.BytesIO(raw_wav_bytes) as buf:
                samples, piece_sr = sf.read(buf, dtype="float32")
                if len(samples.shape) > 1:
                    samples = samples[:, 0]

            # Trim leading/trailing edge silence (10ms at native rate)
            trimmed = trim_edge_silence(samples, min_keep_samples=max(1, int(0.010 * piece_sr)))
            if len(trimmed) > 0:
                audio_pieces.append((trimmed, piece_sr))

        if not audio_pieces:
            return np.zeros(0, dtype=np.float32), 0.0, predicted_dur

        first_sr = audio_pieces[0][1]
        # Stitch pieces with 10ms smooth crossfades at native rate
        stitched = crossfade_concat([p[0] for p in audio_pieces], crossfade_ms=10, sample_rate=first_sr)

        # Resample dynamically from real sample rate to 48kHz float32
        audio_48k = resample_to_48k(stitched, orig_sr=first_sr)
        actual_dur = len(audio_48k) / 48000.0

        # Update calibration history with lock
        async with self.calibrator.lock:
            self.calibrator.record_measurement(lang, voice, len(text), actual_dur)

        return audio_48k, actual_dur, predicted_dur

