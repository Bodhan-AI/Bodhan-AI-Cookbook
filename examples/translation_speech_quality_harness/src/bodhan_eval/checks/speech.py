from collections import Counter
import re
from typing import Protocol

from ..config import CHECK_DEFAULTS, CHECK_THRESHOLDS
from ..audio import WavData, silence_stats, voiced_regions
from ..models import Flag, Item


class Transcriber(Protocol):
    async def transcribe(self, wav_bytes: bytes, language: str) -> str: ...


def mask_numbers(text: str) -> str:
    masked = re.sub(r"\b\d[\d,]*(?:\.\d+)?\b", "<NUM>", text)
    masked = re.sub(r"[\u0966-\u096f\u09e6-\u09ef\u0b66-\u0bef\u0be6-\u0bef\u0c66-\u0c6f\u0ce6-\u0cef\u0d66-\u0d6f]+", "<NUM>", masked)
    return masked


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", mask_numbers(text).casefold(), flags=re.UNICODE)


def _repeated_ngrams(words: list[str], script_words: list[str], size: int = 3) -> list[tuple[str, ...]]:
    transcript_counts = Counter(
        tuple(words[index:index + size])
        for index in range(max(0, len(words) - size + 1))
    )
    script_counts = Counter(
        tuple(script_words[index:index + size])
        for index in range(max(0, len(script_words) - size + 1))
    )
    return sorted(
        ngram
        for ngram, count in transcript_counts.items()
        if count > max(1, script_counts[ngram])
    )


def check_speech(
    item: Item,
    wav: WavData,
    transcriber=None,
    stats=None,
    *,
    transcript: str | None = None,
) -> list[Flag]:
    del transcriber, stats
    if not item.translation:
        return []
    audio_stats = silence_stats(wav)
    regions = voiced_regions(wav)
    active_duration = sum(end - start for start, end in regions)
    transcript_words = _words(transcript or "")
    suspected_hallucination = bool(
        transcript_words
        and len(transcript_words) >= 5
        and active_duration < CHECK_THRESHOLDS["speech_min_active_duration_s"]["value"]
    )
    max_words_per_second = CHECK_THRESHOLDS["speech_max_words_per_second"]["value"]
    unsupported_rate = bool(
        transcript_words
        and active_duration > 0
        and len(transcript_words) / active_duration > max_words_per_second
    )
    suspected_hallucination = suspected_hallucination or unsupported_rate
    flags: list[Flag] = []
    if float(audio_stats["ratio"]) >= CHECK_THRESHOLDS["speech_silence_ratio"]["value"]:
        duration = (0.0, wav.duration_s)
        evidence = {
            "silence_ratio": audio_stats["ratio"],
            "longest_s": audio_stats["longest_s"],
        }
        if suspected_hallucination:
            evidence["suspected_asr_hallucination"] = True
        flags.append(Flag(
            category="garbled",
            severity="major",
            modality="speech",
            audio_span_s=duration,
            evidence=evidence,
            detectors=["silence"],
            confidence=CHECK_DEFAULTS["confidence"]["speech_silence"],
        ))
    if wav.duration_s > CHECK_THRESHOLDS["speech_max_duration_s"]["value"]:
        flags.append(Flag(
            category="speech_omission",
            severity="minor",
            modality="speech",
            audio_span_s=(0.0, wav.duration_s),
            evidence={"duration_s": wav.duration_s, "chars_per_second": len(item.translation) / wav.duration_s},
            detectors=["duration"],
            confidence=CHECK_DEFAULTS["confidence"]["speech_duration"],
            suppressed_reason="single_noisy_signal",
        ))
    if transcript_words and active_duration > 0 and not suspected_hallucination:
        for ngram in _repeated_ngrams(transcript_words, _words(item.translation)):
            flags.append(Flag(
                category="repetition",
                severity="major",
                modality="speech",
                audio_span_s=(0.0, wav.duration_s),
                evidence={
                    "repeated_ngram": " ".join(ngram),
                    "transcript_words": len(transcript_words),
                    "speech_active_duration_s": round(active_duration, 6),
                },
                detectors=["asr_repetition"],
                confidence=CHECK_DEFAULTS["confidence"]["speech_duration"],
            ))
            break
    return flags


async def check_speech_with_transcriber(
    item: Item,
    wav: WavData,
    wav_bytes: bytes,
    transcriber: Transcriber,
    stats=None,
) -> list[Flag]:
    transcript = await transcriber.transcribe(wav_bytes, item.lang)
    return check_speech(item, wav, stats=stats, transcript=transcript)
