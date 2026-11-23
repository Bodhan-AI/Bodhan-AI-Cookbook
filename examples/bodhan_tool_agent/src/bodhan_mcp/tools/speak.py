"""Speech synthesis tool with automatic sentence splitting and PCM frame joining."""

from __future__ import annotations

import contextlib
import re
import tempfile
import wave
from pathlib import Path
from typing import List, Optional

from ..client.client import BodhanClient, VOICE_CATALOG, normalize_language_code
from ..models import SpeakResult

# Official regex for Indian language and English sentence boundaries
SENTENCE_END = re.compile(r"(?<=[।.!?])\s+|\n+")
MAX_TTS_CHUNK_CHARS = 220


def _split_long_clause(clause: str, max_chars: int = MAX_TTS_CHUNK_CHARS) -> List[str]:
    """Break a long sentence without terminal punctuation into <= max_chars word-aligned chunks."""
    if len(clause) <= max_chars:
        return [clause]
    words = clause.split()
    if not words:
        return [clause[:max_chars]]
    parts: List[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip() if cur else w
        if len(cand) <= max_chars:
            cur = cand
        else:
            if cur:
                parts.append(cur)
            # Hard-slice single ultra-long token if needed
            while len(w) > max_chars:
                parts.append(w[:max_chars])
                w = w[max_chars:]
            cur = w
    if cur:
        parts.append(cur)
    return parts


def _split_into_sentences(text: str, max_chars: int = MAX_TTS_CHUNK_CHARS) -> List[str]:
    """Split text on sentence terminators and enforce max_chars per TTS chunk."""
    raw = [s.strip() for s in SENTENCE_END.split(text.strip()) if s.strip()]
    if not raw:
        return [text.strip()] if text.strip() else []
    chunks: List[str] = []
    for sent in raw:
        chunks.extend(_split_long_clause(sent, max_chars=max_chars))
    return chunks


def _resolve_default_voice(language: str, preferred_gender: str = "female") -> str:
    """Select standard voice for a language from official Bodhan catalog."""
    lang = language.strip().lower()
    entry = VOICE_CATALOG.get(lang)
    if not entry:
        # Fallback to English/Hindi default
        return "Kavya"
    voices = entry.get(preferred_gender) or entry.get("female") or entry.get("male")
    if voices:
        return voices[0]
    return "Kavya"


def bodhan_speak(
    text: Optional[str] = None,
    file_path: Optional[str] = None,
    language: Optional[str] = None,
    voice: Optional[str] = None,
    style: Optional[str] = None,
    output_file: Optional[str] = None,
    verify: bool = False,
    client: Optional[BodhanClient] = None,
) -> SpeakResult:
    """Synthesize natural speech in 22 Indian languages plus English from text or a file.
    
    Handles inputs of any length by splitting into sentence chunks (~220 chars),
    calling Indic-Speak, optionally back-transcribing & repairing bad chunks once,
    and joining PCM16 24kHz mono WAV frames into a single audio file.
    """
    if file_path:
        src_p = Path(file_path).resolve()
        if not src_p.exists():
            raise FileNotFoundError(f"Input text file not found: {file_path}")
        raw_text = src_p.read_text(encoding="utf-8")
    elif text is not None:
        raw_text = text
    else:
        raise ValueError("Either 'text' or 'file_path' must be provided to bodhan_speak.")

    clean_text = raw_text.strip()
    if not clean_text:
        raise ValueError("Text for speech synthesis cannot be empty.")

    target_lang = normalize_language_code(language or "hi")
    if target_lang not in VOICE_CATALOG:
        raise ValueError(
            f"Target language '{language}' is not supported by Indic-Speak. "
            f"Supported languages: {', '.join(sorted(VOICE_CATALOG.keys()))}"
        )

    valid_voices = [v for vlist in VOICE_CATALOG[target_lang].values() for v in vlist]
    if voice:
        if voice not in valid_voices:
            raise ValueError(
                f"Voice '{voice}' is not valid for language '{target_lang}'. "
                f"Available voices for '{target_lang}': {', '.join(valid_voices)}"
            )
        chosen_voice = voice
    else:
        chosen_voice = _resolve_default_voice(target_lang)
    b_client = client or BodhanClient()

    out_path = Path(output_file) if output_file else Path("speech_output.wav")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sentences = _split_into_sentences(clean_text)

    def _speak_and_maybe_repair(chunk_text: str, dest: Path) -> SpeakResult:
        res = b_client.speak(
            text=chunk_text,
            voice=chosen_voice,
            language=target_lang,
            style=style,
            output_path=str(dest),
        )
        if verify and not b_client.mock_mode:
            from .transcribe import bodhan_transcribe
            tr = bodhan_transcribe(str(dest), language=target_lang, client=b_client)
            ratio = len(tr.text.strip()) / max(1, len(chunk_text.strip()))
            if ratio < 0.35 or ratio > 2.2:
                # One repair retry bypassing cache for Indic-Speak omission/repetition
                res_retry = b_client.speak(
                    text=chunk_text,
                    voice=chosen_voice,
                    language=target_lang,
                    style=style,
                    output_path=str(dest),
                    bypass_cache=True,
                )
                res_retry.cost_inr = round(res.cost_inr + tr.cost_inr + res_retry.cost_inr, 4)
                return res_retry
            res.cost_inr = round(res.cost_inr + tr.cost_inr, 4)
        return res

    # If single sentence and within chunk limit, single call
    if len(sentences) <= 1 and len(clean_text) <= MAX_TTS_CHUNK_CHARS:
        return _speak_and_maybe_repair(clean_text, out_path)

    # Multi-sentence speech synthesis and byte-level PCM frame stitching
    temp_dir = Path(tempfile.mkdtemp(prefix=f"_temp_speech_{out_path.stem}_", dir=out_path.parent))

    frames = []
    params = None
    total_cost = 0.0
    total_chars = 0

    try:
        for idx, sentence in enumerate(sentences):
            temp_part = temp_dir / f"part_{idx:03d}.wav"
            res = _speak_and_maybe_repair(sentence, temp_part)
            total_cost += res.cost_inr
            total_chars += res.character_count

            with contextlib.closing(wave.open(str(temp_part), "rb")) as wf:
                if params is None:
                    params = wf.getparams()
                frames.append(wf.readframes(wf.getnframes()))

        # Join into single WAV
        with contextlib.closing(wave.open(str(out_path), "wb")) as wf:
            if params is not None:
                wf.setparams(params)
                for f in frames:
                    wf.writeframes(f)

        # Compute combined duration
        with contextlib.closing(wave.open(str(out_path), "rb")) as wf:
            rate = wf.getframerate()
            duration = wf.getnframes() / float(rate) if rate > 0 else 0.0

    finally:
        # Clean up temporary part files
        for p in temp_dir.glob("part_*.wav"):
            try:
                p.unlink()
            except Exception:
                pass
        try:
            temp_dir.rmdir()
        except Exception:
            pass

    return SpeakResult(
        audio_path=str(out_path.resolve()),
        voice=chosen_voice,
        language=target_lang,
        style=style,
        character_count=total_chars,
        duration_seconds=round(duration, 2),
        cost_inr=round(total_cost, 4),
        cached=False,
    )
