"""Dataset -> candidates -> calibrated flags.

Order per item, cheapest first:
  1. deterministic text checks (script, numbers, format)          free
  2. back-translation with Indic-Translate                        ~₹0.0001
  3. LLM judge, given the hints from 1-2                          LLM only
  4. speech: signal analysis, then Indic-Transcribe back-check    ₹0.10/min (+ Indic-Speak if generating)
Speech generation is the only expensive step, so items for it are chosen up front, deterministically,
to fit the rupee budget.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sacrebleu
import soundfile as sf

from bodhan_eval import judge as judge_mod
from bodhan_eval import speechchecks, textchecks
from bodhan_eval.clients import PRICE_SPEAK_PER_CHAR, PRICE_TRANSCRIBE_PER_SEC, PRICE_TRANSLATE_PER_TOKEN, BudgetExceeded, ModelError
from bodhan_eval.flags import SEVERITIES, SPEECH_CATEGORIES, Candidate, canonical_category, overlaps, span_ref
from bodhan_eval.langs import SCRIPTS

log = logging.getLogger("bodhan_eval.pipeline")

HIGH_PRECISION_CHECKS = {"numbers", "format", "script"}
TTS_MAX_CHARS = 220


@dataclass
class Options:
    langs: list[str]
    generate: bool = False
    judge: bool = True
    speech_sample: float = 0.3  # share of items to synthesize audio for when generating
    max_cost_inr: float | None = None
    judge_repeats: int = 20  # items re-judged to measure consistency
    concurrency: int = 4


@dataclass
class ItemState:
    item: dict
    status: str = "ok"
    candidates: list[Candidate] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    checks_run: list[str] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)  # check -> reason
    cost_inr: float = 0.0
    audio_out: str | None = None
    judge_hints: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.item['id']}|{self.item['lang']}"

    @property
    def key_tuple(self) -> tuple[str, str]:
        return (self.item["id"], self.item["lang"])


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_dataset(path: Path, langs: list[str], calibration: Path | list[Path] | None) -> tuple[list[dict], dict]:
    """Read JSONL, drop bad lines with a logged reason, and merge label files by id. A label file holds
    either item labels ({id, lang?, labels}) or per-flag human judgments ({id, lang, category, verdict}),
    returned in ``stats["judgments"]``. A review_queue.csv answered by a person is also accepted."""
    stats = {"lines": 0, "bad": [], "filtered_lang": 0}
    items, seen = [], set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):  # -sig: tolerate a BOM
        if not line.strip():
            continue
        stats["lines"] += 1
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("line is not a JSON object")
            for k in ("id", "source", "lang"):
                if not isinstance(item.get(k), str) or not item[k].strip():
                    raise ValueError(f"missing or empty '{k}'")
            if item["lang"] not in SCRIPTS:
                raise ValueError(f"unsupported lang '{item['lang']}'")
            if (item["id"], item["lang"]) in seen:
                raise ValueError("duplicate id+lang")
            if "translation" in item and not isinstance(item["translation"], str):
                raise ValueError("'translation' must be a string")
            item["labels"] = _clean_labels(item["labels"]) if "labels" in item else None
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            stats["bad"].append({"line": lineno, "reason": str(e)})
            log.warning("bad item skipped", extra={"line": lineno, "reason": str(e)})
            continue
        seen.add((item["id"], item["lang"]))
        if item["lang"] not in langs:
            stats["filtered_lang"] += 1
            continue
        if item.get("audio"):  # reports keep the dataset's relative path; reads use the resolved one
            item["_audio_path"] = str((path.parent / item["audio"]).resolve())
        items.append(item)

    stats["judgments"] = []
    for path_ in ([calibration] if isinstance(calibration, Path) else calibration or []):
        by_id = defaultdict(list)
        for it in items:
            by_id[it["id"]].append(it)
        rows = review_csv_judgments(path_) if path_.suffix.lower() == ".csv" else \
            [line for line in path_.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        for line in rows:
            try:
                row = json.loads(line) if isinstance(line, str) else line
                if "verdict" in row:  # a person's verdict on one flagged category of one item
                    stats["judgments"].append(_clean_judgment(row))
                    continue
                for it in [it for it in by_id.get(row["id"], []) if row.get("lang") in (None, it["lang"])]:
                    it["labels"] = _clean_labels(row.get("labels") or [])
            except (ValueError, KeyError, TypeError) as e:
                stats["bad"].append({"calibration_line": str(line)[:80], "reason": str(e)})
    items.sort(key=lambda it: (it["lang"], it["id"]))
    return items, stats


def _clean_judgment(row: dict) -> dict:
    cat = canonical_category(str(row["category"]), row.get("area"))
    verdict = str(row["verdict"]).strip().lower()
    if not cat or verdict not in {"error", "clean"}:
        raise ValueError(f"bad judgment (category={row.get('category')!r}, verdict={row.get('verdict')!r})")
    span = row.get("span")
    return {"id": row["id"], "lang": row["lang"], "category": cat, "verdict": verdict, "note": row.get("note", ""),
            "span": tuple(span) if isinstance(span, list | tuple) and len(span) == 2 else None,
            "annotator": row.get("annotator", "human")}


def review_csv_judgments(path: Path, annotator: str = "human:review-queue") -> list[dict]:
    """Rows of an answered review_queue.csv become judgments: for that item, the flagged category is a
    real 'error' or 'clean' (a false alarm). A verdict is about one flagged category, not the whole item."""
    out = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            answer = (row.get("human_label (error|clean)") or row.get("human_label") or "").strip().lower()
            if answer in {"error", "clean"}:
                out.append({"id": row["id"], "lang": row["lang"], "category": row["category"], "verdict": answer,
                            "span": json.loads(row.get("span") or "null"), "annotator": annotator})
    return out


def _clean_labels(labels) -> list[dict]:
    out = []
    for lab in labels or []:
        cat = canonical_category(str(lab.get("type", "")), lab.get("area"))
        if not cat:
            continue
        span = lab.get("span")
        out.append({
            "category": cat,
            "severity": lab.get("severity", "major"),
            "span": tuple(span) if isinstance(span, list | tuple) and len(span) == 2 else None,
            "ref": lab.get("ref") or span_ref(cat),
        })
    return out


# ---------------------------------------------------------------------------
# Speech helpers
# ---------------------------------------------------------------------------

def speakable_segments(text: str) -> list[tuple[int, int, str]]:
    """Split text into short TTS inputs at sentence ends (then commas), with Markdown/LaTeX
    syntax removed. Returns (raw_start, raw_end, speakable_text)."""
    out = []
    for m in re.finditer(r"[^.!?।\n]+[.!?।]*", text):
        s, e = m.start(), m.end()
        pieces = [(s, e)]
        if e - s > TTS_MAX_CHARS:
            pieces, start = [], s
            for c in re.finditer(r"[,;:]\s+", text[s:e]):
                if s + c.end() - start > TTS_MAX_CHARS * 0.6:
                    pieces.append((start, s + c.end()))
                    start = s + c.end()
            pieces.append((start, e))
        for a, b in pieces:
            clean = re.sub(r"\$|\\[A-Za-z]+|[#*_`|>{}]|\[|\]\([^)]*\)|^-{3,}$|:?-{3,}:?", " ", text[a:b])
            clean = re.sub(r"\s+", " ", clean).strip()
            if re.search(r"\w", clean):
                out.append((a, b, clean))
    return out


def synthesize(bodhan, text: str, lang: str, voice: str | None = None, style: str | None = None) -> tuple[bytes, list[dict], float]:
    """Synthesize text sentence by sentence and join with 250 ms pauses."""
    parts, segs, cost, t, sr = [], [], 0.0, 0.0, 24000
    for a, b, clean in speakable_segments(text):
        wav, c = bodhan.speak(clean, lang, voice, style) if voice or style else bodhan.speak(clean, lang)
        cost += c
        x, sr = sf.read(io.BytesIO(wav), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        parts += [x, np.zeros(int(0.25 * sr), dtype="float32")]
        segs.append({"span": [a, b], "start_s": round(t, 3), "end_s": round(t + len(x) / sr, 3)})
        t += len(x) / sr + 0.25
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(parts) if parts else np.zeros(sr // 10, dtype="float32"), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), segs, cost


def hash01(key: str) -> float:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def plan_speech(items: list[dict], opts: Options) -> set[str]:
    """Pick items to synthesize: a deterministic, hash-ordered sample that fits the budget.
    Supplied audio is always checked (transcription is cheap)."""
    if not opts.generate:
        return set()
    pool = sorted((it for it in items if not it.get("audio")), key=lambda it: hash01(f"{it['id']}|{it['lang']}"))
    want = round(len(pool) * opts.speech_sample)
    budget = (opts.max_cost_inr * 0.8) if opts.max_cost_inr else float("inf")
    chosen, spent = set(), 0.0
    for it in pool[:want]:
        chars = len(it.get("translation") or it["source"]) * 1.2
        cost = chars * PRICE_SPEAK_PER_CHAR + (chars / 12) * PRICE_TRANSCRIBE_PER_SEC
        if spent + cost > budget:
            break
        chosen.add(f"{it['id']}|{it['lang']}")
        spent += cost
    return chosen


# ---------------------------------------------------------------------------
# Per-item evaluation
# ---------------------------------------------------------------------------

def evaluate_item(state: ItemState, bodhan, llm, opts: Options, speak: bool, audio_dir: Path) -> ItemState:
    item = state.item
    lid = state.key
    try:
        if not item.get("translation"):
            if not opts.generate:
                state.skipped["translation"] = "no translation supplied (use --generate)"
            else:
                out = bodhan.translate(item["source"], item["lang"])
                item["translation"], item["generated_translation"] = out["translation"], True
                state.cost_inr += out["tokens"] * PRICE_TRANSLATE_PER_TOKEN
                if out["truncated"]:
                    state.candidates.append(Candidate("omission", 0.9, "truncated", "major", None, (len(item["source"]) - 1, len(item["source"])),
                                                      evidence={"problem": "Indic-Translate reported truncated output"}))
        if item.get("translation"):
            _text_checks(state, bodhan, llm, opts)
        if item.get("audio") or speak:
            _speech_checks(state, bodhan, speak, audio_dir)
    except BudgetExceeded as e:
        state.skipped["budget"] = str(e)
    except ModelError as e:
        state.status = "error"
        state.skipped["error"] = str(e)
        log.error("item failed", extra={"item": lid, "error": str(e)})
    except Exception as e:  # a bad item must never stop the run
        state.status = "error"
        state.skipped["error"] = f"{type(e).__name__}: {e}"
        log.exception("item crashed", extra={"item": lid})
    return state


def _text_checks(state: ItemState, bodhan, llm, opts: Options) -> None:
    item, lid = state.item, state.key
    src, tgt, lang = item["source"], item["translation"], item["lang"]
    for name, fn in (("script", lambda: textchecks.check_script(src, tgt, lang)),
                     ("numbers", lambda: textchecks.check_numbers(src, tgt)),
                     ("format", lambda: textchecks.check_format(src, tgt))):
        cands = fn()
        state.candidates += cands
        state.checks_run.append(name)
        log.info("check", extra={"item": lid, "check": name, "candidates": [c.to_dict() for c in cands]})

    if item.get("reference"):
        state.metrics["chrf_ref"] = round(sacrebleu.sentence_chrf(tgt, [item["reference"]], word_order=2).score, 2)

    whole_wrong = any(c.check == "script" and c.span == (0, len(tgt)) for c in state.candidates)
    back = None
    if not whole_wrong and lang != "en":
        try:
            out = bodhan.translate(tgt, "en")
            back = out["translation"]
            state.cost_inr += out["tokens"] * PRICE_TRANSLATE_PER_TOKEN
            if textchecks.latin_share(back) < 0.6:
                state.skipped["backtranslation"] = "back-translation is not English; ignored as checker noise"
            cands = textchecks.check_backtranslation(src, back) + textchecks.check_entities(src, back)
            state.candidates += cands
            state.metrics["chrf_backtranslation"] = round(sacrebleu.sentence_chrf(back, [src], word_order=2).score, 2)
            state.checks_run.append("backtranslation")
            log.info("check", extra={"item": lid, "check": "backtranslation", "back": back, "candidates": [c.to_dict() for c in cands]})
        except BudgetExceeded as e:
            state.skipped["backtranslation"] = str(e)
    state.metrics["back_translation"] = back

    if llm and opts.judge:
        state.judge_hints = [{"check": c.check, "category": c.category, **{k: v for k, v in c.evidence.items() if k != "back_translation"}}
                             for c in state.candidates]
        cands = judge_mod.judge(llm, item, back, state.judge_hints)
        state.candidates += cands
        state.checks_run.append("judge")
        state.metrics["judge_verdict"] = list(judge_mod.verdict(cands))
        log.info("check", extra={"item": lid, "check": "judge", "candidates": [c.to_dict() for c in cands]})


def _speech_checks(state: ItemState, bodhan, speak: bool, audio_dir: Path) -> None:
    item, lid = state.item, state.key
    text = item.get("translation")
    if not text:
        state.skipped["speech"] = "no text to compare audio against"
        return
    if item.get("audio"):
        path = Path(item["_audio_path"])
        if not path.exists():
            state.skipped["speech"] = f"audio file not found: {path}"
            return
        wav = path.read_bytes()
    else:
        wav, segs, cost = synthesize(bodhan, text, item["lang"])
        state.cost_inr += cost
        state.metrics["audio_segments"] = segs
        audio_dir.mkdir(parents=True, exist_ok=True)
        out = audio_dir / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', item['id'])}.{item['lang']}.wav"
        out.write_bytes(wav)
        state.audio_out = f"{audio_dir.name}/{out.name}"  # relative to the report directory

    audio = speechchecks.analyze_audio(wav)
    state.metrics["audio"] = audio
    cands = speechchecks.audio_candidates(audio, text)
    if not audio.get("empty"):
        tr = bodhan.transcribe(wav, item["lang"])
        state.cost_inr += tr["cost"]
        more, m = speechchecks.transcript_candidates(text, tr["text"])
        cands += more
        state.metrics.update({"transcript": tr["text"], "speech_cer": m["cer"]})
    state.candidates += cands
    state.checks_run.append("speech")
    log.info("check", extra={"item": lid, "check": "speech", "audio": audio, "candidates": [c.to_dict() for c in cands]})


# ---------------------------------------------------------------------------
# Merging candidates into flags
# ---------------------------------------------------------------------------

def merge(cands: list[Candidate]) -> list[dict]:
    """Cluster candidates of the same category whose spans overlap. Agreement between
    independent checks raises the score; a span-less hint joins the best cluster of its category."""
    clusters: list[list[Candidate]] = []
    for c in sorted(cands, key=lambda c: (-c.score, c.check, c.category)):
        if c.primary_span() is None:
            continue
        home = next((cl for cl in clusters if cl[0].category == c.category and overlaps(cl[0].primary_span(), c.primary_span())), None)
        if home:
            home.append(c)
        else:
            clusters.append([c])
    for c in (c for c in cands if c.primary_span() is None):
        home = next((cl for cl in clusters if cl[0].category == c.category), None)
        if home:
            home.append(c)
        else:
            clusters.append([c])

    flags = []
    for cl in clusters:
        lead = cl[0]
        checks = sorted({c.check for c in cl})
        score = min(1.0, max(c.score for c in cl) + 0.1 * (len(checks) - 1))
        spanned = [c for c in cl if c.primary_span() is not None]
        exact = next((c for c in spanned if c.check in HIGH_PRECISION_CHECKS), spanned[0] if spanned else lead)
        flags.append({
            "category": lead.category,
            "severity": max((c.severity for c in cl), key=SEVERITIES.index),
            "score": round(score, 3),
            "span": list(exact.span) if exact.span else None,
            "source_span": list(exact.source_span) if exact.source_span else next((list(c.source_span) for c in cl if c.source_span), None),
            "checks": checks,
            "evidence": [{"check": c.check, **c.evidence} for c in cl],
        })

    # A judge-only flag sitting on the same text as an exact deterministic flag is the same error seen twice.
    exact_flags = [f for f in flags if set(f["checks"]) & HIGH_PRECISION_CHECKS and f["score"] >= 0.9]
    flags = [f for f in flags if not (f["checks"] == ["judge"] and any(
        f is not e and overlaps(tuple(f["span"]) if f["span"] else None, tuple(e["span"]) if e["span"] else None) for e in exact_flags))]
    return sorted(flags, key=lambda f: (-f["score"], f["category"], f["span"] or [0, 0]))


def cut(clusters: list[dict], thresholds: dict[str, float]) -> list[dict]:
    return [f for f in clusters if f["score"] >= thresholds.get(f["category"], 0.5)]


def finalize(states: list[ItemState], thresholds: dict[str, float]) -> None:
    for st in states:
        st.metrics["clusters"] = merge(st.candidates)
        st.metrics["flags"] = cut(st.metrics["clusters"], thresholds)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(items: list[dict], bodhan, llm, opts: Options, audio_dir: Path) -> list[ItemState]:
    states = [ItemState(item=it) for it in items]
    speak = plan_speech(items, opts)
    with ThreadPoolExecutor(max_workers=max(1, opts.concurrency * 2)) as pool:
        list(pool.map(lambda st: evaluate_item(st, bodhan, llm, opts, st.key in speak, audio_dir), states))

    by_lang = defaultdict(list)
    for st in states:
        if st.item.get("translation"):
            by_lang[st.item["lang"]].append((st.key, st.item["source"], st.item["translation"]))
    by_key = {st.key: st for st in states}
    for rows in by_lang.values():
        for key, cands in textchecks.length_outliers(rows).items():
            by_key[key].candidates += cands

    speech_by_lang = defaultdict(list)
    for st in states:
        if "speech" in st.checks_run and not st.metrics["audio"].get("empty"):
            speech_by_lang[st.item["lang"]].append((st.key, st.item["translation"], st.metrics["audio"]["speech_s"]))
    for rows in speech_by_lang.values():
        for key, cands in speechchecks.speech_rate_outliers(rows).items():
            by_key[key].candidates += cands
    return states


def judge_consistency(states: list[ItemState], llm, n: int) -> dict:
    """Re-judge a deterministic sample with a fresh (separately cached) call; report agreement."""
    pool = sorted((st for st in states if "judge" in st.checks_run), key=lambda st: hash01("judge|" + st.key))[:n]
    if not pool or llm is None:
        return {"items": 0}
    agree, by_lang = 0, defaultdict(lambda: [0, 0])

    def again(st: ItemState):
        try:
            return st, judge_mod.verdict(judge_mod.judge(llm, st.item, st.metrics.get("back_translation"), st.judge_hints, salt="repeat-1"))
        except (ModelError, BudgetExceeded):
            return st, None

    with ThreadPoolExecutor(max_workers=4) as ex:
        for st, v in ex.map(again, pool):
            if v is None:
                continue
            same = v == tuple(st.metrics.get("judge_verdict", []))
            agree += same
            by_lang[st.item["lang"]][0] += same
            by_lang[st.item["lang"]][1] += 1
    total = sum(t for _, t in by_lang.values())
    return {
        "items": total,
        "same_verdict_rate": round(agree / total, 3) if total else None,
        "per_language": {k: round(a / t, 3) for k, (a, t) in sorted(by_lang.items()) if t},
        "note": "verdict = set of error categories; the repeat is the identical prompt sampled again (cache bypassed)",
    }


def is_speech(category: str) -> bool:
    return category in SPEECH_CATEGORIES
