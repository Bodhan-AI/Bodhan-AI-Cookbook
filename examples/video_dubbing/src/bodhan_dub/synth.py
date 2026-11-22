"""Speak each translated unit, check it by ear, and fit it into its time slot.

Per request of one or two sentences (short TTS inputs, as Bodhan recommends):
  1. Indic-Speak synthesises it; leading/trailing silence is trimmed.
  2. QA (ttsqa.py): Indic-Transcribe hears the clip back; a number-aware CER, a loop
     detector and the length ratio flag repeated or dropped words. A flagged clip is
     re-spoken sentence by sentence, then once more fresh, and the best take is kept.

Per unit, timing (duration drift):
  * the budget is the unit's own slot plus part of the silence after it,
  * fits the budget → natural speed; fits within 1.2× → gentle time-stretch,
  * still too long → the LLM shortens the ENGLISH source (numbers and glossary terms
    kept, checked by code), Indic-Translate re-translates it and we re-synthesise.
    Up to 3 rounds, each asking for less; the shortest acceptable version wins.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import audio, textcheck, ttsqa
from .bodhan import BodhanClient, BodhanError
from .languages import STYLES
from .llm import LLM, LLMError
from .repair import Unit
from .translate import LanguageResult, TranslatedUnit, Translator

log = logging.getLogger(__name__)

SENTENCE_GAP_S = 0.15
MAX_TEMPO = 1.2
CONDENSE_ROUNDS = 3
MAX_ASR_CLIP_S = 29.0
RETAKE_ISSUES = frozenset({"repetition", "omission", "empty audio"})
# Bodhan: "a sentence or two per request". Packing two short sentences per request halves
# the calls, which matters with a rate-limited speak key.
MAX_SENTENCES_PER_REQUEST = 2
MAX_CHARS_PER_REQUEST = 220
_INDIC_SENTENCE_END = re.compile(r"(?<=[।॥.?!۔])\s+")


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

def split_indic_sentences(text: str, min_chars: int = 12) -> list[str]:
    """Split on sentence ends (including danda); glue tiny fragments to their neighbour."""
    parts = [p.strip() for p in _INDIC_SENTENCE_END.split(text.strip()) if p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and len(part) < min_chars:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    if len(merged) > 1 and len(merged[0]) < min_chars:
        merged[1] = f"{merged[0]} {merged[1]}"
        merged.pop(0)
    return merged


def pack_requests(sentences: list[str]) -> list[list[str]]:
    """Group consecutive sentences into TTS requests of at most two sentences / ~220 characters."""
    groups: list[list[str]] = []
    for sentence in sentences:
        if (groups and len(groups[-1]) < MAX_SENTENCES_PER_REQUEST
                and len(" ".join(groups[-1])) + 1 + len(sentence) <= MAX_CHARS_PER_REQUEST):
            groups[-1].append(sentence)
        else:
            groups.append([sentence])
    return groups


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

@dataclass
class Take:
    method: str  # "whole": one request; "split": one request per sentence; "fresh": a new take
    duration_s: float = 0.0
    asr_text: str = ""
    cer: float = 1.0  # number-aware CER (ttsqa.wildcard_cer): the QA decision uses this
    cer_plain: float = 1.0  # ordinary CER against the translation, reported for transparency
    issue: str = ""
    detail: str = ""


@dataclass
class SpokenSentence:
    """One TTS request's worth of text (one or two sentences) and the takes tried for it."""

    text: str
    parts: list[str] = field(default_factory=list)
    tts_text: str = ""  # what Indic-Speak was sent, when it differs from `text` (native digits)
    takes: list[Take] = field(default_factory=list)
    chosen: int = 0
    samples: np.ndarray | None = field(default=None, repr=False)  # trimmed clip of the chosen take
    rate: int = 24_000

    @property
    def best(self) -> Take:
        return self.takes[self.chosen]

    def to_dict(self) -> dict[str, Any]:
        spoken_as = {"tts_text": self.tts_text} if self.tts_text and self.tts_text != self.text else {}
        return {"text": self.text, **spoken_as, "chosen_take": self.chosen,
                "duration_s": round(self.best.duration_s, 3),
                "cer": round(self.best.cer, 3), "cer_plain": round(self.best.cer_plain, 3),
                "issue": self.best.issue, "asr_text": self.best.asr_text,
                "detail": self.best.detail,
                "takes": [{"method": t.method, "duration_s": round(t.duration_s, 3), "cer": round(t.cer, 3),
                           "issue": t.issue, "detail": t.detail} for t in self.takes]}


@dataclass
class DubLine:
    unit: TranslatedUnit
    voice: str = ""
    gender: str = ""
    sentences: list[SpokenSentence] = field(default_factory=list)
    budget_s: float = 0.0
    tempo: float = 1.0
    condensed: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"  # ok / flagged / skipped
    notes: list[str] = field(default_factory=list)
    placed_start: float = 0.0
    placed_end: float = 0.0

    @property
    def natural_s(self) -> float:
        if not self.sentences:
            return 0.0
        return sum(s.best.duration_s for s in self.sentences) + SENTENCE_GAP_S * (len(self.sentences) - 1)

    @property
    def spoken_s(self) -> float:
        return self.natural_s / self.tempo

    def _weighted(self, attr: str) -> float | None:
        chars = [(len(ttsqa.normalize(s.text)), getattr(s.best, attr)) for s in self.sentences]
        total = sum(n for n, _ in chars)
        return round(sum(n * c for n, c in chars) / total, 3) if total else None

    @property
    def cer(self) -> float | None:
        return self._weighted("cer")

    @property
    def cer_plain(self) -> float | None:
        return self._weighted("cer_plain")

    def to_dict(self) -> dict[str, Any]:
        u = self.unit
        return {
            "id": u.id, "source_start": u.start, "source_end": u.end, "source_text": u.source_text,
            "translation": " ".join(s.text for s in self.sentences) if self.sentences else u.translation,
            "translation_status": u.status, "translation_note": u.note, "translation_chosen": u.chosen,
            "status": self.status, "notes": self.notes, "voice": self.voice, "gender": self.gender,
            "natural_duration_s": round(self.natural_s, 3), "budget_s": round(self.budget_s, 3),
            "tempo": round(self.tempo, 4), "audio_duration_s": round(self.spoken_s, 3),
            "placed_start": round(self.placed_start, 3), "placed_end": round(self.placed_end, 3),
            "sync_error_ms": round((self.placed_start - u.start) * 1000) if self.sentences else None,
            "cer": self.cer, "cer_plain": self.cer_plain, "condensed": self.condensed,
            "sentences": [s.to_dict() for s in self.sentences],
            "translation_candidates": [c.__dict__ for c in u.candidates],
        }


# ---------------------------------------------------------------------------
# synthesis
# ---------------------------------------------------------------------------

class Synthesizer:
    def __init__(self, client: BodhanClient, llm: LLM | None, translator: Translator, *,
                 style: str | None, max_workers: int = 4, native_digits: bool = True):
        self.client = client
        self.llm = llm
        self.translator = translator
        self.style = style
        self.max_workers = max_workers
        self.native_digits = native_digits

    def tts_text(self, text: str, lang: str) -> str:
        """What Indic-Speak receives: the translation, with digits in the language's own script."""
        return textcheck.native_digits(text, lang) if self.native_digits else text

    def speak_sentence(self, parts: list[str], voice: str, lang: str) -> SpokenSentence:
        """Speak one request's text and check it by ear; on a bad take, try something different.

        Take order: the whole text in one request; then each sentence on its own (a genuinely
        different request, which also breaks most loops); then one fresh take of the whole text.
        A fresh take that comes back byte-identical (a deterministic model) is not retried again.
        """
        text = " ".join(parts)
        spoken = SpokenSentence(text=text, parts=parts, tts_text=self.tts_text(text, lang))
        clips: list[np.ndarray] = []
        plan = ["whole", *(["split"] if len(parts) > 1 else []), "fresh"]
        first_audio: bytes | None = None
        for method in plan:
            take = Take(method=method)
            try:
                if method == "split":
                    pieces = [self._clip(self.client.speak(self.tts_text(p, lang), voice, lang, self.style))
                              for p in parts]
                    rate = pieces[0][1]
                    gap = np.zeros(int(SENTENCE_GAP_S * rate), dtype=np.float32)
                    samples = np.concatenate([x for i, (clip, _) in enumerate(pieces)
                                              for x in ((gap, clip) if i else (clip,))])
                else:
                    wav = self.client.speak(self.tts_text(text, lang), voice, lang, self.style,
                                            variant=1 if method == "fresh" else 0)
                    if method == "fresh" and wav == first_audio:
                        take.issue, take.detail = "skipped", "model returned identical audio for the same text"
                        spoken.takes.append(take)
                        clips.append(np.zeros(0, dtype=np.float32))
                        break
                    first_audio = first_audio or wav
                    samples, rate = self._clip(wav)
                spoken.rate = rate
                take.duration_s = len(samples) / rate
                take.asr_text = self.back_transcribe(samples, rate, lang)
            except (BodhanError, audio.AudioError) as exc:
                take.issue, take.detail = "error", str(exc)[:200]
                spoken.takes.append(take)
                clips.append(np.zeros(0, dtype=np.float32))
                continue
            verdict = ttsqa.judge_clip(text, take.asr_text, take.duration_s)
            take.cer, take.issue, take.detail = verdict.cer, verdict.issue, verdict.detail
            take.cer_plain = ttsqa.plain_cer(text, take.asr_text)
            spoken.takes.append(take)
            clips.append(samples)
            # Retake only for the failures Bodhan warns about (loops, dropped words) or silence.
            # A plain "mismatch" is more often back-transcription noise than bad speech, and a
            # retake costs a call against the speak key's rate limit; it stays flagged instead.
            if take.issue not in RETAKE_ISSUES:
                break
            log.info("tts take rejected", extra={"stage": "synth", "lang": lang, "method": method,
                                                 "issue": take.issue, "cer": round(take.cer, 3), "detail": take.detail})
        usable = [i for i, t in enumerate(spoken.takes) if t.duration_s >= 0.2]
        if usable:
            spoken.chosen = min(usable, key=lambda i: (bool(spoken.takes[i].issue), spoken.takes[i].cer))
            spoken.samples = clips[spoken.chosen]
        return spoken

    @staticmethod
    def _clip(wav: bytes) -> tuple[np.ndarray, int]:
        samples, rate = audio.wav_to_array(wav)
        return audio.trim_silence(samples, rate), rate

    def back_transcribe(self, samples: np.ndarray, rate: int, lang: str) -> str:
        step = int(MAX_ASR_CLIP_S * rate)
        texts = [self.client.transcribe(audio.array_to_wav(samples[i: i + step], rate), language=lang)
                 for i in range(0, max(1, len(samples)), step)]
        return " ".join(t for t in texts if t)

    def speak_text(self, text: str, voice: str, lang: str) -> list[SpokenSentence]:
        return [self.speak_sentence(group, voice, lang) for group in pack_requests(split_indic_sentences(text))]

    # -- timing --------------------------------------------------------------------

    def fit(self, line: DubLine, source_unit: Unit, lang: str) -> None:
        if line.natural_s <= line.budget_s:
            line.tempo = 1.0
            return
        if line.natural_s <= line.budget_s * MAX_TEMPO:
            line.tempo = line.natural_s / line.budget_s
            return
        # Each round asks for less: from the best English so far, aiming at what the measured speech
        # needs, and 15% harder again after a round that didn't help (a repeat would just hit the cache).
        text = source_unit.text
        ratio = line.budget_s * 1.1 / line.natural_s  # aim a little long; the stretch absorbs the rest
        for round_no in range(1, CONDENSE_ROUNDS + 1):
            shorter, why = self.condense(text, ratio, lang)
            record: dict[str, Any] = {"round": round_no, "target_ratio": round(ratio, 3), "english": shorter}
            line.condensed.append(record)
            if not shorter:
                record["result"] = f"no acceptable shorter English: {why}"
                if self.llm is None:
                    break
                ratio *= 0.85
                continue
            shorter_unit = Unit(id=source_unit.id, start=source_unit.start, end=source_unit.end, text=shorter,
                                raw_text=source_unit.raw_text, source_ids=source_unit.source_ids)
            cand = self.translator.make_candidate(shorter_unit, lang, line.unit.chosen or "unit")
            # Same bar as translation recovery: hard facts only. Terms were already checked in the English.
            if cand.hard_fail or cand.missing_numbers or cand.missing_keep:
                record["result"] = "re-translation failed checks: " + "; ".join(
                    cand.hard_fail + [f"missing number {n}" for n in cand.missing_numbers]
                    + [f"changed kept term {t}" for t in cand.missing_keep])
                ratio *= 0.85
                continue
            # Shortened English gets the same meaning check as any translation before it is spoken.
            self.translator.judge([(shorter_unit, cand)], lang, LanguageResult(lang=lang))
            record["judge"] = cand.verdict
            if cand.verdict == "major":
                record["result"] = f"judge rejected the re-translation: {cand.issue}"
                ratio *= 0.85
                continue
            sentences = self.speak_text(cand.text, line.voice, lang)
            natural = sum(s.best.duration_s for s in sentences) + SENTENCE_GAP_S * (len(sentences) - 1)
            record.update(translation=cand.text, natural_s=round(natural, 3))
            text = shorter
            if natural < line.natural_s and all(s.samples is not None for s in sentences):
                record["result"] = "accepted"
                line.sentences = sentences
                line.unit.translation = cand.text
                if natural <= line.budget_s * MAX_TEMPO:
                    break
                ratio = line.budget_s * 1.1 / natural
            else:
                record["result"] = "not shorter"
                ratio *= 0.85
        line.tempo = min(MAX_TEMPO, max(1.0, line.natural_s / line.budget_s))
        if line.natural_s / line.tempo > line.budget_s + 0.05:
            line.notes.append(f"runs {line.natural_s / line.tempo - line.budget_s:.2f}s past its slot "
                              f"after condensing and a {MAX_TEMPO}x stretch")

    def condense(self, english: str, ratio: float, lang: str) -> tuple[str, str]:
        """Ask the LLM for shorter ENGLISH; code verifies numbers and glossary terms survived.

        Returns (text, "") or ("", why it was rejected).
        """
        if self.llm is None:
            return "", "no LLM configured"
        terms = [t for t, k in textcheck.checkable_terms(self.translator.terms, english)]
        # A hard word count is followed far more closely than a percentage (seen live: asked for 64%,
        # got ~85%), so the target is stated as "at most N words".
        max_words = max(3, int(len(english.split()) * ratio))
        system = ("You shorten English narration so its dubbed translation fits the original timing. "
                  "Rewrite the text in AT MOST max_words words. Keep every number and every listed term "
                  "exactly, and keep the facts a learner needs. Drop filler, restatements, connectives and "
                  "anything the listener can infer; use the shortest phrasing. "
                  'Reply with JSON only: {"text": "..."}')
        user = json.dumps({"text": english, "max_words": max_words, "terms_to_keep": terms}, ensure_ascii=False)
        try:
            reply = self.llm.complete_json(system, user)
        except LLMError as exc:
            log.warning("condense failed", extra={"stage": "synth", "lang": lang, "error": str(exc)})
            return "", f"LLM error: {exc}"
        text = str(reply.get("text") or "").strip() if isinstance(reply, dict) else ""
        if not text:
            return "", "empty reply"
        if len(text) >= len(english) * 0.97:
            return "", "not shorter"
        if lost := textcheck.missing_numbers(english, text):
            return "", f"dropped numbers {lost}"
        dropped = [t for t, k in textcheck.checkable_terms(self.translator.terms, english)
                   if not textcheck.term_in(t, k, text)]
        if dropped:
            return "", f"dropped glossary terms {dropped}"
        return text, ""

    # -- per language ----------------------------------------------------------------

    def synthesize_language(self, translated: list[TranslatedUnit], units: list[Unit],
                            voice_of: dict[str, tuple[str, str]], lang: str, video_s: float) -> list[DubLine]:
        """`voice_of` maps a unit id to (voice name, voice gender) for its speaker."""
        by_id = {u.id: u for u in units}
        lines = [DubLine(unit=t, voice=voice_of[t.id][0], gender=voice_of[t.id][1]) for t in translated]
        speakable = [ln for ln in lines if ln.unit.status != "skipped"]
        for i, line in enumerate(speakable):
            u = line.unit
            nxt = speakable[i + 1].unit.start if i + 1 < len(speakable) else video_s
            slot = u.end - u.start
            # Spill into the following silence, but not so far that the line drifts off its picture.
            line.budget_s = max(slot, min(nxt - u.start - 0.1, slot * 1.5 + 1.0))

        def work(line: DubLine) -> DubLine:
            ctx = {"stage": "synth", "lang": lang, "unit": line.unit.id}
            line.sentences = self.speak_text(line.unit.translation, line.voice, lang)
            if not line.sentences or any(s.samples is None for s in line.sentences):
                line.status, line.sentences = "skipped", []
                line.notes.append("speech synthesis failed for every take")
                log.error("unit not spoken", extra=ctx)
                return line
            self.fit(line, by_id[line.unit.id], lang)
            bad = [s for s in line.sentences if s.best.issue]
            if bad:
                line.status = "flagged"
                line.notes.append("QA: " + "; ".join(f"{s.best.issue} (CER {s.best.cer:.2f})" for s in bad))
            if line.unit.status == "flagged":
                line.status = "flagged"
                line.notes.append(f"translation: {line.unit.note}")
            log.info("unit spoken", extra={**ctx, "natural_s": round(line.natural_s, 2),
                                           "budget_s": round(line.budget_s, 2), "tempo": round(line.tempo, 3),
                                           "cer": line.cer, "condensed": len(line.condensed), "status": line.status})
            return line

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            list(pool.map(work, speakable))
        for line in lines:
            if line.unit.status == "skipped":
                line.status = "skipped"
                line.notes.append(f"not dubbed: {line.unit.note}")
        return lines


def choose_style(llm: LLM | None, units: list[Unit]) -> str | None:
    """LLM picks the Indic-Speak delivery style that matches the video, or None for neutral."""
    if llm is None:
        return None
    sample = " ".join(u.text for u in units if not u.dropped)[:2000]
    system = ("Pick the speaking style for an AI dub of this video's narration, from this list: "
              f"{json.dumps(list(STYLES))}, or \"none\" for a neutral read. Match the genre "
              '(a lesson -> "educational lecture", a news report -> "news"). '
              'Reply with JSON only: {"style": "..."}')
    try:
        reply = llm.complete_json(system, sample)
    except LLMError:
        return None
    style = reply.get("style") if isinstance(reply, dict) else None
    return style if style in STYLES else None
