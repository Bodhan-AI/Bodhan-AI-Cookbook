"""roundtrip.compare: Pure comparison functions for the two Bodhan round trips (no I/O, no model calls).
noise: Checker-noise separation: is a round-trip difference a system error, checker noise, or uncertain?
checks.roundtrip: Tier-2 round-trip checks. They read results computed by the pipeline (back-translation,
router: Router: decides which stages run for an item, runs the model-backed round trips, and
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

import jiwer
import numpy as np
import regex
from rapidfuzz import fuzz
from sacrebleu.metrics import CHRF

from .audio import read_audio, snap, write_wav
from .checks import CheckContext, Flag, english_number_words, extract_numbers, register
from .clients import MAX_TRANSCRIBE_SECONDS, BodhanClient, GenResult, OfflineCacheMiss, failure_reason
from .dataset import Item
from .infra import BudgetExceeded
from .text import clauses, english_entities, mask_protected, normalize, sentences

# -------------------- compare
_CHRF = CHRF(word_order=2)


_PREC = CHRF(word_order=2, beta=1e-6)  # ~ precision: share of back-translation n-grams found in the source


_REC = CHRF(word_order=2, beta=1e6)  # ~ recall: share of source n-grams that came back


_WORD = regex.compile(r"[\p{L}\p{M}\p{N}]+")


_MARKS_ZW = regex.compile(r"[\p{M}​‌‍⁠﻿]")


_LATIN = regex.compile(r"\p{Script=Latin}")


_LETTER = regex.compile(r"\p{L}")


def chrf_pr(hyp: str, ref: str) -> tuple[float, float, float]:
    """(chrF++, precision, recall) with hypothesis = back-translation, reference = source."""
    if not hyp.strip() or not ref.strip():
        return 0.0, 0.0, 0.0
    return (_CHRF.sentence_score(hyp, [ref]).score, _PREC.sentence_score(hyp, [ref]).score, _REC.sentence_score(hyp, [ref]).score)


def _mask_names(text: str, names: list[str], similarity: float) -> str:
    """Remove the names, and words that are spelling variants of a name word
    (back-translation often transliterates names back differently: Raonic -> Raonik)."""
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    name_words = {w.lower() for n in names for w in re.findall(r"[A-Za-z']+", n) if len(w) >= 3}
    kept = [w for w in text.split() if not any(fuzz.ratio(re.sub(r"[^A-Za-z']", "", w).lower(), nw) / 100.0 >= similarity for nw in name_words)]
    return " ".join(kept)


def _number_values(text: str) -> list[str]:
    return sorted({n.value for n in extract_numbers(text)} | set(english_number_words(text)))


@dataclass
class BTResult:
    chrf: float
    precision: float
    recall: float
    precision_masked: float  # with source entity names removed from both sides
    recall_masked: float
    numbers_missing: list[str]  # source values not found in the back-translation (digits or English words)
    numbers_extra: list[str]
    entities_missing: list[str]
    entities_swapped: list[str] = field(default_factory=list)
    min_clause_recall: float = 100.0
    min_sent_precision: float = 100.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _fuzzy_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 3]


def _entity_present(ent: str, words: list[str], sim: float) -> bool:
    ew = _fuzzy_words(ent)
    return not ew or all(max((fuzz.ratio(w, bw) / 100.0 for bw in words), default=0.0) >= sim for w in ew)


def compare_back_translation(source: str, back: str, name_similarity: float = 0.75) -> BTResult:
    src = mask_protected(source)  # code / LaTeX / URLs round-trip verbatim; they would inflate the scores
    bt = mask_protected(back)
    chrf, p, r = chrf_pr(bt, src)
    names = english_entities(source)
    _, pm, rm = chrf_pr(_mask_names(bt, names, name_similarity), _mask_names(src, names, name_similarity)) if names else (chrf, p, r)
    s_nums, b_nums = _number_values(source), _number_values(back)
    low = back.lower()
    src_w, bt_w = _fuzzy_words(src), _fuzzy_words(bt)
    fuzzy_sim = min(0.70, name_similarity)
    fuzzy_missing = [n for n in names if not _entity_present(n, bt_w, fuzzy_sim)]
    bt_extra_ents = [n for n in english_entities(back) if not _entity_present(n, src_w, fuzzy_sim)]
    swapped = [f"{m}->{bt_extra_ents[0]}" for m in fuzzy_missing] if (fuzzy_missing and bt_extra_ents) else []
    src_clauses = [src[a:b] for a, b in clauses(src) if b - a >= 15]
    min_cr = min((chrf_pr(bt, c)[2] for c in src_clauses), default=rm)
    bt_sents = [bt[a:b] for a, b in sentences(bt) if b - a >= 15]
    min_sp = min((chrf_pr(s, src)[1] for s in bt_sents), default=pm)
    return BTResult(
        chrf=chrf, precision=p, recall=r, precision_masked=pm, recall_masked=rm,
        numbers_missing=[v for v in s_nums if v not in b_nums], numbers_extra=[v for v in b_nums if v not in s_nums],
        entities_missing=[n for n in names if n.lower() not in low],
        entities_swapped=swapped, min_clause_recall=min_cr, min_sent_precision=min_sp,
    )


def asr_words(text: str) -> list[str]:
    """Comparison tokens: normalized (any-script digits -> ASCII, punctuation folded), letters/marks/digits only."""
    return _WORD.findall(normalize(mask_protected(text)))


def _skeleton(words: list[str]) -> str:
    return _MARKS_ZW.sub("", "".join(words))


def _is_latin(words: list[str]) -> bool:
    letters = _LETTER.findall("".join(words))
    return bool(letters) and all(_LATIN.match(c) for c in letters)


def explain(ref: list[str], hyp: list[str], th: dict) -> str | None:
    """Name the checker-noise rule that explains this difference, or None (a real difference)."""
    if ref and hyp and _skeleton(ref) == _skeleton(hyp):
        return "matra_zwj"
    num_tokens = sum(any(unicodedata.category(c) == "Nd" for c in w) for w in ref + hyp)
    if num_tokens and ref and hyp and len(ref) <= 2 * num_tokens + 1 and len(hyp) <= 4 * num_tokens + 1:
        if not _find_repeat(hyp, 0, len(hyp), int(th["repeat_ngram_min"])):
            return "digit_vs_word"  # e.g. "100" spoken back as number words (live smoke test, hi)
    if ref and hyp and _is_latin(ref) != _is_latin(hyp):
        return "latin_vs_indic"
    if ref and hyp and fuzz.ratio(_skeleton(ref), _skeleton(hyp)) / 100.0 >= th["asr_spelling_similarity"]:
        return "spelling_variant"
    return None


@dataclass
class Region:
    ref: tuple[int, int]  # word index range in the reference
    hyp: tuple[int, int]  # word index range in the hypothesis
    noise: str | None  # checker-noise rule, or None = real difference


@dataclass
class ASRResult:
    ref_words: list[str]
    hyp_words: list[str]
    wer: float
    cer: float
    residual_wer: float  # only unexplained regions count
    residual_cer: float
    regions: list[Region] = field(default_factory=list)
    tail_deleted: int = 0  # unexplained words missing at the end of the reference
    repeat: dict | None = None  # {"words": n, "hyp_start": i, "hyp_end": j} when an n-gram is repeated

    def to_dict(self) -> dict:
        return {
            "wer": self.wer, "cer": self.cer, "residual_wer": self.residual_wer, "residual_cer": self.residual_cer,
            "ref_words": len(self.ref_words), "hyp_words": len(self.hyp_words), "tail_deleted": self.tail_deleted,
            "repeat": self.repeat,
            "regions": [{"ref": list(r.ref), "hyp": list(r.hyp), "noise": r.noise} for r in self.regions],
        }


def _find_repeat(hyp: list[str], start: int, end: int, n_min: int) -> dict | None:
    """Extra hypothesis words [start, end) that repeat an n-gram (or a long word/stem) occurring nearby."""
    extra = hyp[start:end]
    for n in range(len(extra), n_min - 1, -1):
        for i in range(len(extra) - n + 1):
            gram = extra[i : i + n]
            before = hyp[max(0, start + i - 2 * n - 2) : start + i]
            after = hyp[start + i + n : min(len(hyp), start + i + 3 * n + 2)]
            if any(before[j : j + n] == gram for j in range(len(before) - n + 1)) or any(after[j : j + n] == gram for j in range(len(after) - n + 1)):
                return {"words": n, "hyp_start": start + i, "hyp_end": start + i + n}
    # Single-word or morphological stem repeat (e.g. an audio loop cut mid-word)
    for i, w in enumerate(extra):
        idx = start + i
        prev = hyp[max(0, idx - 3) : idx]
        nxt = hyp[idx + 1 : min(len(hyp), idx + 4)]
        if w in prev or w in nxt:
            return {"words": max(1, len(extra)), "hyp_start": start, "hyp_end": end}
        if len(w) >= 5 and any(len(u) >= 5 and (w.startswith(u[:5]) or w.endswith(u[-5:])) for u in prev + nxt):
            return {"words": max(1, len(extra)), "hyp_start": start, "hyp_end": end}
    return None


def compare_transcript(expected_text: str, transcript: str, th: dict) -> ASRResult | None:
    ref = asr_words(expected_text)
    hyp = asr_words(transcript)
    if not ref:
        return None
    out = jiwer.process_words(" ".join(ref), " ".join(hyp) if hyp else "")
    chunks = [c for c in out.alignments[0] if c.type != "equal"]
    # merge touching non-equal chunks into regions (e.g. "100" -> two number words = substitute + insert)
    merged: list[list[int]] = []
    for c in chunks:
        if merged and c.ref_start_idx <= merged[-1][1] and c.hyp_start_idx <= merged[-1][3]:
            merged[-1][1], merged[-1][3] = max(merged[-1][1], c.ref_end_idx), max(merged[-1][3], c.hyp_end_idx)
        else:
            merged.append([c.ref_start_idx, c.ref_end_idx, c.hyp_start_idx, c.hyp_end_idx])
    regions: list[Region] = []
    for rs, re_, hs, he in merged:
        regions.append(Region((rs, re_), (hs, he), explain(ref[rs:re_], hyp[hs:he], th)))

    real = [r for r in regions if r.noise is None]
    errors = sum(max(r.ref[1] - r.ref[0], r.hyp[1] - r.hyp[0]) for r in real)
    keep_ref = [w for i, w in enumerate(ref) if not any(r.noise and r.ref[0] <= i < r.ref[1] for r in regions)]
    keep_hyp = [w for i, w in enumerate(hyp) if not any(r.noise and r.hyp[0] <= i < r.hyp[1] for r in regions)]
    residual_cer = jiwer.cer(" ".join(keep_ref), " ".join(keep_hyp)) if keep_ref else 0.0
    res = ASRResult(ref, hyp, out.wer, jiwer.cer(" ".join(ref), " ".join(hyp)), errors / len(ref), residual_cer, regions)

    last = regions[-1] if regions else None
    # a truncated clip often ends in a cut-off partial word, so the last region may be
    # "substitute + delete": count the words it loses at the end of the reference
    if last is not None and last.noise is None and last.ref[1] == len(ref) and last.hyp[1] == len(hyp):
        res.tail_deleted = max(0, (last.ref[1] - last.ref[0]) - (last.hyp[1] - last.hyp[0]))
    for r in real:
        extra = (r.hyp[1] - r.hyp[0]) - (r.ref[1] - r.ref[0])
        if extra >= 1:
            rep = _find_repeat(hyp, r.hyp[0], r.hyp[1], int(th["repeat_ngram_min"]))
            if rep:
                res.repeat = rep
                break
    return res


# -------------------- noise
SYSTEM_ERROR, CHECKER_NOISE, UNCERTAIN = "system_error", "checker_noise", "uncertain"


SIGNALS = {"bt_precision": "low", "bt_recall": "low", "asr_residual_wer": "high", "asr_residual_cer": "high"}


@dataclass
class Verdict:
    signal: str
    verdict: str
    reasons: list[str] = field(default_factory=list)
    value: float | None = None
    floor: float | None = None

    def to_dict(self) -> dict:
        return {"signal": self.signal, "verdict": self.verdict, "reasons": sorted(self.reasons), "value": self.value, "floor": self.floor}


def compute_floors(observations: list[tuple[str, str, float]], langs: list[str], th: dict) -> dict:
    """observations: (signal, lang, value) from CLEAN items only.
    Returns {signal: {lang: {"value", "n", "source", "percentile"}}} for every requested language."""
    floors: dict = {}
    for signal, tail in sorted(SIGNALS.items()):
        pct = th["floor_low_pct"] if tail == "low" else th["floor_high_pct"]
        pooled = [v for s, _, v in observations if s == signal]
        floors[signal] = {}
        for lang in sorted(langs):
            own = [v for s, lg, v in observations if s == signal and lg == lang]
            if len(own) >= th["floor_min_items"]:
                vals, source = own, "lang"
            elif len(pooled) >= th["floor_min_pooled"]:
                vals, source = pooled, "pooled"
            else:
                floors[signal][lang] = {"value": None, "n": len(pooled), "source": "insufficient", "percentile": pct}
                continue
            floors[signal][lang] = {"value": float(np.percentile(np.asarray(vals, dtype=float), pct)), "n": len(vals), "source": source, "percentile": pct}
    return floors


def beyond(signal: str, value: float | None, floor: float | None) -> bool | None:
    """True if the value is on the bad side of the floor; None when undecidable."""
    if value is None or floor is None:
        return None
    return value < floor if SIGNALS[signal] == "low" else value > floor


def decide(signal: str, value: float | None, floor: float | None, *, explained_by: list[str], corroborated_by: list[str]) -> Verdict:
    """The single verdict rule used by every round-trip check."""
    side = beyond(signal, value, floor)
    if side is None:
        return Verdict(signal, CHECKER_NOISE, ["no_noise_floor"], value, floor)
    if not side:
        return Verdict(signal, CHECKER_NOISE, ["within_noise_floor"], value, floor)
    if explained_by:
        return Verdict(signal, CHECKER_NOISE, list(explained_by), value, floor)
    if corroborated_by:
        return Verdict(signal, SYSTEM_ERROR, [f"corroborated_by:{c}" for c in corroborated_by], value, floor)
    return Verdict(signal, UNCERTAIN, ["uncorroborated"], value, floor)


# -------------------- roundtrip
PAD_S = 0.25


def _record(ctx: CheckContext, v: Verdict) -> Verdict:
    ctx.metrics.setdefault("verdicts", []).append(v.to_dict())
    return v


def _corroborators(ctx: CheckContext, categories: set[str]) -> list[str]:
    """Tier-1 signals (strong or weak) of a compatible category on the same item."""
    return sorted({f.check_id for f in ctx.prior_flags if f.category in categories})


def _span_from(ctx: CheckContext, categories: set[str], unit: str):
    spans = [f.span for f in ctx.prior_flags if f.category in categories and f.span is not None and f.unit == unit]
    return min(spans) if spans else None


def _flag(ctx, v: Verdict, check_id: str, category: str, message: str, span=None, unit="chars", evidence=None) -> list[Flag]:
    if v.verdict not in (SYSTEM_ERROR, UNCERTAIN):
        return []
    ev = {"verdict": v.verdict, "reasons": sorted(v.reasons), "value": v.value, "floor": v.floor, **(evidence or {})}
    weak = v.verdict == UNCERTAIN
    return [Flag(check_id, category, "major", span, unit, message, ev, 0.5 if weak else 0.75, needs_corroboration=weak)]


@register(
    "back_translation",
    needs=("translation", "back_translation"),
    tier=2,
    description="Back-translation to English vs the source (precision/recall, numbers, names)",
)
def check_back_translation(ctx: CheckContext) -> list[Flag]:
    bt = ctx.bt
    if bt is None:
        return []
    ctx.metrics["back_translation"] = bt.to_dict()
    if ctx.bt_truncated:
        # the BACK call was cut off: our checker output is incomplete, so recall says nothing
        _record(ctx, Verdict("bt_recall", CHECKER_NOISE, ["back_translation_truncated"], bt.recall_masked))
        return []
    flags: list[Flag] = []
    asr_tail = ["asr_tail_missing"] if ctx.asr is not None and ctx.asr.tail_deleted else []

    floor = ctx.floors.get("bt_recall")
    rec_val = min(bt.recall_masked, bt.min_clause_recall)
    v = decide("bt_recall", rec_val, floor, explained_by=[], corroborated_by=_corroborators(ctx, {"omission"}) + asr_tail)
    if v.verdict == CHECKER_NOISE and v.reasons == ["within_noise_floor"] and beyond("bt_recall", bt.recall, floor):
        v.reasons = ["proper_noun"]  # only the names drifted: masking them brings recall back inside the floor
    _record(ctx, v)
    om_span = _span_from(ctx, {"omission"}, "chars") or ((0, len(ctx.translation)) if ctx.translation else None)
    flags += _flag(
        ctx,
        v,
        "back_translation",
        "omission",
        f"Back-translation recovers little of the source (recall {rec_val:.1f}); content may be missing.",
        om_span,
    )

    floor = ctx.floors.get("bt_precision")
    prec_val = min(bt.precision_masked, bt.min_sent_precision)
    v = decide("bt_precision", prec_val, floor, explained_by=[], corroborated_by=_corroborators(ctx, {"addition", "mistranslation"}))
    if v.verdict == CHECKER_NOISE and v.reasons == ["within_noise_floor"] and beyond("bt_precision", bt.precision, floor):
        v.reasons = ["proper_noun"]
    _record(ctx, v)
    add_span = _span_from(ctx, {"addition", "mistranslation"}, "chars") or ((0, len(ctx.translation)) if ctx.translation else None)
    flags += _flag(
        ctx,
        v,
        "back_translation",
        "addition",
        f"Back-translation contains much that is not in the source (precision {prec_val:.1f}); content may be added.",
        add_span,
    )

    if bt.numbers_missing and bt.numbers_extra:
        corr = _corroborators(ctx, {"number_unit"})
        v = Verdict("bt_numbers", SYSTEM_ERROR if corr else UNCERTAIN, [f"corroborated_by:{c}" for c in corr] or ["uncorroborated"])
        _record(ctx, v)
        flags += _flag(
            ctx,
            v,
            "back_translation",
            "number_unit",
            f"Back-translation has {', '.join(bt.numbers_extra)} where the source has {', '.join(bt.numbers_missing)}.",
            _span_from(ctx, {"number_unit"}, "chars"),
            evidence={"missing": bt.numbers_missing, "extra": bt.numbers_extra},
        )
    elif bt.numbers_missing or bt.numbers_extra:
        _record(ctx, Verdict("bt_numbers", CHECKER_NOISE, ["bt_number_rendering"]))
    if bt.entities_swapped:
        v = Verdict("bt_entities", UNCERTAIN, ["uncorroborated"])
        _record(ctx, v)
        flags += _flag(ctx, v, "back_translation", "term_entity", f"Back-translation replaces entity ({', '.join(bt.entities_swapped)}).")
    elif bt.entities_missing:
        _record(ctx, Verdict("bt_entities", CHECKER_NOISE, ["proper_noun"]))
    return flags


@register("asr_roundtrip", needs=("audio", "asr_text", "audio_text"), tier=2, description="Back-transcription of the audio vs the text it should say")
def check_asr_roundtrip(ctx: CheckContext) -> list[Flag]:
    a = ctx.asr
    if a is None:
        return []
    ctx.metrics["asr"] = a.to_dict()
    prof = ctx.audio
    flags: list[Flag] = []
    acoustic = _corroborators(ctx, {"speech_garbled"})

    for signal, value, raw in (("asr_residual_wer", a.residual_wer, a.wer), ("asr_residual_cer", a.residual_cer, a.cer)):
        floor = ctx.floors.get(signal)
        v = decide(signal, value, floor, explained_by=[], corroborated_by=acoustic)
        if v.verdict == CHECKER_NOISE and v.reasons == ["within_noise_floor"] and beyond(signal, raw, floor):
            v.reasons = sorted({r.noise for r in a.regions if r.noise}) or ["within_noise_floor"]
        _record(ctx, v)
        if signal == "asr_residual_wer":
            flags += _flag(
                ctx,
                v,
                "asr_roundtrip",
                "speech_garbled",
                f"The transcript differs from the text beyond normal ASR noise (residual WER {value:.2f}).",
                _span_from(ctx, {"speech_garbled"}, "seconds"),
                "seconds",
            )

    z = ctx.stats.z("speech_s_per_char", ctx.lang, ctx.item_id, min_n=int(ctx.th("min_stat_items")))
    voiced_start = prof.leading_silence_s
    voiced_end = prof.duration_s - prof.trailing_silence_s
    if a.tail_deleted >= ctx.th("truncation_min_words"):
        confirm = z is not None and z < -ctx.th("duration_confirm_z")
        v = Verdict(
            "asr_tail_missing",
            SYSTEM_ERROR if confirm else UNCERTAIN,
            [f"duration_short:z={z:.2f}"] if confirm else [f"duration_not_short:z={'n/a' if z is None else f'{z:.2f}'}"],
            float(a.tail_deleted),
        )
        _record(ctx, v)
        span = (round(max(0.0, voiced_end - 0.5), 3), round(prof.duration_s, 3))
        flags += _flag(
            ctx,
            v,
            "asr_roundtrip",
            "speech_omission_truncation",
            f"Speech stops early: the last {a.tail_deleted} word(s) of the text are not in the audio (ends at {prof.duration_s:.2f} s).",
            span,
            "seconds",
            {"tail_deleted_words": a.tail_deleted},
        )
    if a.repeat:
        confirm = a.repeat["words"] >= ctx.th("repeat_strong_words") or (z is not None and z > ctx.th("duration_confirm_z"))
        v = Verdict(
            "asr_repeat",
            SYSTEM_ERROR if confirm else UNCERTAIN,
            ["repeated_ngram_confirmed"] if confirm else ["short_repeat_duration_normal"],
            float(a.repeat["words"]),
        )
        _record(ctx, v)
        n = max(1, len(a.hyp_words))
        t0 = voiced_start + (voiced_end - voiced_start) * a.repeat["hyp_start"] / n
        t1 = voiced_start + (voiced_end - voiced_start) * a.repeat["hyp_end"] / n
        span = (round(max(0.0, t0 - PAD_S), 3), round(min(prof.duration_s, t1 + PAD_S), 3))
        flags += _flag(
            ctx,
            v,
            "asr_roundtrip",
            "speech_repetition",
            f"{a.repeat['words']} word(s) are spoken twice (around {t0:.1f}-{t1:.1f} s, estimated from word position).",
            span,
            "seconds",
            {"approximate_span": True, "repeat": a.repeat},
        )
    return flags


# -------------------- router
log = logging.getLogger("bodhan_eval.router")


ASR_CHUNK_TARGET_S = 25.0  # split long audio near every 25 s, at the quietest frame (API limit: 30 s)


@dataclass
class RoundTrip:
    back_translation: str | None = None
    bt_truncated: bool = False
    asr_text: str | None = None
    route: list[dict] = field(default_factory=list)

    def stage(self, name: str, ran: bool, reason: str) -> None:
        self.route.append({"stage": name, "ran": ran, "reason": reason})


def split_for_asr(wav: bytes, max_s: float = MAX_TRANSCRIBE_SECONDS, target_s: float = ASR_CHUNK_TARGET_S) -> list[bytes]:
    """One request per <= 30 s piece; cut points snap to the quietest frame near each target."""
    samples, sr = read_audio(wav)
    if len(samples) / sr <= max_s:
        return [wav]
    cuts = [0]
    while len(samples) / sr - cuts[-1] / sr > max_s:
        cut = snap(samples, sr, cuts[-1] / sr + target_s)
        cuts.append(cut if cut > cuts[-1] else cuts[-1] + int(target_s * sr))
    cuts.append(len(samples))
    return [write_wav(samples[a:b], sr) for a, b in zip(cuts, cuts[1:], strict=False) if b > a]


def _skip_reason(exc: Exception) -> str:
    if isinstance(exc, BudgetExceeded):
        return "budget"
    if isinstance(exc, OfflineCacheMiss):
        return "offline_cache_miss"
    return f"error:{failure_reason(exc)}"


async def run_round_trips(bodhan: BodhanClient, item: Item, *, audio_decodable: bool, generated: GenResult | None, generate: bool) -> RoundTrip:
    rt = RoundTrip()
    rt.stage("tier1", True, "always (zero cost)")

    if generate:
        if generated is not None and generated.audio:
            rt.stage("tts", True, "--generate: audio was missing")
        else:
            why = next((s.split(":", 1)[1] for s in (generated.skipped if generated else []) if s.startswith("audio:")), "audio supplied or not needed")
            rt.stage("tts", False, why)
    else:
        rt.stage("tts", False, "--generate not set")

    if not item.translation:
        rt.stage("back_translation", False, "no translation")
    else:
        try:
            res = await bodhan.translate(item.translation, "en")
            rt.back_translation, rt.bt_truncated = res.text, res.truncated
            rt.stage("back_translation", True, "translation present")  # cache state goes to logs only (determinism)
        except Exception as exc:  # this stage only
            rt.stage("back_translation", False, _skip_reason(exc))
            log.warning("stage_skipped", extra={"fields": {"item": item.id, "stage": "back_translation", "error": str(exc)}})

    if item.audio_missing:
        rt.stage("asr", False, "audio_missing")
    elif item.audio is None:
        rt.stage("asr", False, "no audio")
    elif not audio_decodable:
        rt.stage("asr", False, "audio not decodable")
    else:
        try:
            pieces = split_for_asr(item.audio.read_bytes())
            texts = []
            for piece in pieces:
                res = await bodhan.transcribe(piece, item.lang)
                texts.append(res.text)
            rt.asr_text = " ".join(t.strip() for t in texts).strip()
            rt.stage("asr", True, "supplied audio" + (f", {len(pieces)} chunks" if len(pieces) > 1 else ""))
        except Exception as exc:  # this stage only
            rt.stage("asr", False, _skip_reason(exc))
            log.warning("stage_skipped", extra={"fields": {"item": item.id, "stage": "asr", "error": str(exc)}})
    log.info("route", extra={"fields": {"item": item.id, "route": rt.route}})
    return rt
