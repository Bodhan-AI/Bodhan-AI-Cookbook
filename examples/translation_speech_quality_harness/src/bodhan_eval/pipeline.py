"""Dataset -> signals -> thresholds -> flags.

Order per item (cheap first, costly last):
  1. validate, obtain translation (supplied, or Indic-Translate with --generate)
  2. deterministic checks (numbers, script, Markdown/LaTeX, length prior, chrF vs reference)
  3. back-translation per sentence (cheap Indic-Translate calls)  -> soft candidates
  4. LLM judge (several runs)                                     -> confirms/suppresses candidates
  5. speech triage: supplied audio is always checked; with --generate, audio is synthesized only for
     items the earlier stages found suspicious, plus a deterministic hash sample
  6. synthesize (Indic-Speak) -> transcribe (Indic-Transcribe) -> audio metrics
After all items: tune thresholds on labelled items, then decide() flags for everything.
"""
from __future__ import annotations

import hashlib
import json
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import checks_speech, checks_text
from .calibrate import canon_label, tune
from .clients import NotConfigured, Services
from .cost import BudgetExceeded
from .decide import DEFAULTS, decide
from .judge import judge_item
from .normalize import has_letters

log = logging.getLogger("bodhan_eval")


@dataclass
class Config:
    dataset: str
    out: str
    langs: list[str] = field(default_factory=list)
    generate: bool = False
    baseline: str | None = None
    calibration: str | None = None
    max_cost_inr: float | None = 50.0
    cache_dir: str = ".cache/bodhan_eval"
    concurrency: int = 8
    judge_runs: int = 3
    no_judge: bool = False
    no_speech: bool = False
    no_bt: bool = False
    speech_sample: float = 0.25
    seed: int = 0
    holdout_frac: float = 0.3
    ci: bool = False
    ci_tolerance: float = 0.02


def slog(event: str, **kw):
    log.info(json.dumps({"event": event, **kw}, ensure_ascii=False, default=str))


# ------------------------------------------------------------------ loading
def load_dataset(path: str, langs: list[str]):
    items, skipped, filtered = [], [], 0
    seen = set()
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            if not line.strip():
                continue
            obj = None
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("not an object")
                for k in ("id", "source", "lang"):
                    if not isinstance(obj.get(k), str) or not obj[k].strip():
                        raise ValueError(f"missing or empty field '{k}'")
                if obj["id"] in seen:
                    raise ValueError(f"duplicate id {obj['id']}")
            except ValueError as e:
                skipped.append({"line": ln, "id": (obj.get("id") if isinstance(obj, dict) else None), "reason": str(e)})
                slog("skip_item", line=ln, reason=str(e))
                continue
            seen.add(obj["id"])
            if langs and obj["lang"].lower() not in langs:
                filtered += 1
                continue
            items.append(obj)
    return items, skipped, filtered


def load_calibration(path: str | None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not path:
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
                out[o["id"]] = o.get("labels", [])
            except (ValueError, KeyError):
                continue
    return out


def _canon_labels(raw) -> list[dict] | None:
    if raw is None or not isinstance(raw, list):
        return None
    return [c for c in (canon_label(l) for l in raw if isinstance(l, dict)) if c]


# ------------------------------------------------------------------ per item
def _pick_for_speech(item_id: str, rate: float, seed: int) -> bool:
    h = int(hashlib.sha1(f"{seed}:{item_id}".encode()).hexdigest()[:8], 16) % 10_000
    return h < rate * 10_000


def process_item(item: dict, cfg: Config, svc: Services, base_dir: Path) -> dict:
    res = {"id": item["id"], "lang": item["lang"].lower(), "status": "ok", "checks_run": [], "errors": [],
           "signals": {}, "source": item["source"], "translation": None}
    sig = res["signals"]
    lang, source = res["lang"], item["source"]

    def err(stage, e):
        res["errors"].append({"stage": stage, "error": f"{type(e).__name__}: {e}"})
        slog("check_error", id=item["id"], stage=stage, error=str(e))

    # 1. translation
    translation = item.get("translation") if isinstance(item.get("translation"), str) and item.get("translation").strip() else None
    if translation is None and cfg.generate:
        try:
            translation = svc.translate(source, "en", lang)
            res["translation_generated"] = True
            res["checks_run"].append("generate_translation")
        except (BudgetExceeded, NotConfigured, Exception) as e:  # noqa: BLE001
            err("generate_translation", e)
    res["translation"] = translation
    reference = item.get("reference") if isinstance(item.get("reference"), str) and item.get("reference").strip() else None

    bt_text = None
    if translation:
        tx: dict = {}
        sig["text"] = tx
        tx["numbers"] = checks_text.number_signal(source, translation)
        tx["script"] = checks_text.script_signal(source, translation, lang)
        tx["format"] = checks_text.format_signal(source, translation)
        tx["length_ratio"] = checks_text.length_ratio(source, translation)
        res["checks_run"] += ["numbers", "script", "format", "length_prior"]
        if reference:
            tx["chrf_ref"] = checks_text.chrf_score(translation, reference)
            res["checks_run"].append("chrf_reference")
        if not cfg.no_bt:
            try:
                segs = checks_text.back_translate(translation, lang, svc)
                ref_segs = checks_text.back_translate(reference, lang, svc) if reference else None
                tx["bt"] = checks_text.bt_signal(source, segs, ref_segs)
                bt_text = tx["bt"]["back_translation"]
                res["checks_run"].append("back_translation")
            except (BudgetExceeded, NotConfigured, Exception) as e:  # noqa: BLE001
                err("back_translation", e)
        # 4. judge
        if not cfg.no_judge and svc.llm_available():
            try:
                sig["judge"] = judge_item(item, translation, bt_text, svc, cfg.judge_runs)
                res["checks_run"].append("llm_judge")
            except Exception as e:  # noqa: BLE001
                err("llm_judge", e)
    else:
        res["checks_run"].append("no_translation")

    # 5/6. speech
    if not cfg.no_speech:
        audio = None
        audio_field = item.get("audio")
        spoken = item.get("speech_text") or translation or (source if lang == "en" else None)
        if isinstance(audio_field, str) and audio_field:
            p = Path(audio_field)
            cands = [p] if p.is_absolute() else [base_dir / p, Path.cwd() / p]  # dataset folder, then cwd
            p = next((c for c in cands if c.exists()), cands[0])
            try:
                audio = p.read_bytes()
            except OSError as e:
                err("read_audio", e)
        elif cfg.generate and spoken and has_letters(spoken):
            f0, s0 = decide(item, sig, DEFAULTS, None)
            if f0 or s0 or _pick_for_speech(item["id"], cfg.speech_sample, cfg.seed):
                try:
                    audio = svc.speak(spoken, lang)
                    res["checks_run"].append("synthesize")
                    res["speech_triggered_by"] = "suspicious" if (f0 or s0) else "sample"
                except (BudgetExceeded, NotConfigured, Exception) as e:  # noqa: BLE001
                    err("synthesize", e)
        if audio is not None and spoken:
            try:
                transcript = None
                try:
                    transcript = svc.transcribe(audio, lang)
                    res["checks_run"].append("transcribe")
                except (BudgetExceeded, NotConfigured, Exception) as e:  # noqa: BLE001
                    err("transcribe", e)
                sig["speech"] = checks_speech.speech_signal(spoken, transcript, audio)
                res["checks_run"].append("audio_metrics")
                sig["speech"]["text"] = spoken
            except Exception as e:  # noqa: BLE001
                err("audio_metrics", e)

    if not translation and "speech" not in sig:
        res["status"] = "skipped"
        res["skip_reason"] = "no translation or audio to evaluate (use --generate)"
    slog("item_done", id=item["id"], checks=res["checks_run"], errors=len(res["errors"]))
    return res


# ------------------------------------------------------------------ run
def run(cfg: Config, svc: Services) -> dict:
    items, skipped, filtered = load_dataset(cfg.dataset, cfg.langs)
    base_dir = Path(cfg.dataset).resolve().parent
    calib = load_calibration(cfg.calibration)
    slog("dataset", items=len(items), skipped=len(skipped), filtered=filtered)

    def work(it):
        try:
            return process_item(it, cfg, svc, base_dir)
        except Exception as e:  # noqa: BLE001 - never let one item stop the run
            slog("item_crash", id=it.get("id"), error=str(e))
            return {"id": it["id"], "lang": it["lang"].lower(), "status": "error", "checks_run": [], "signals": {},
                    "errors": [{"stage": "item", "error": f"{type(e).__name__}: {e}"}], "source": it["source"], "translation": None}

    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as ex:
        results = list(ex.map(work, items))

    by_id = {it["id"]: it for it in items}
    for r in results:
        raw = calib.get(r["id"], by_id[r["id"]].get("labels"))
        r["labels"] = _canon_labels(raw)
        r["has_reference"] = bool(by_id[r["id"]].get("reference"))

    # language prior for the length check: median ratio per language in THIS run
    ratios: dict[str, list[float]] = {}
    for r in results:
        lr = (r["signals"].get("text") or {}).get("length_ratio")
        if lr is not None:
            ratios.setdefault(r["lang"], []).append(lr)
    lang_median = {l: statistics.median(v) for l, v in ratios.items() if len(v) >= 8}

    def decide_fn(r, thr):
        return decide(by_id[r["id"]], r["signals"], thr, lang_median.get(r["lang"]))

    # held-out split: thresholds are tuned on one part of the labelled items and the targets are
    # reported on the other part, so the headline numbers are not in-sample
    labelled = [r for r in results if r["labels"] is not None and r["status"] == "ok"]
    split = {"enabled": False, "holdout_frac": cfg.holdout_frac, "n_labelled": len(labelled), "n_tune": len(labelled), "n_holdout": 0}
    if cfg.holdout_frac > 0 and len(labelled) >= 16:
        for r in labelled:
            pct = int(hashlib.sha1(f"{cfg.seed}:holdout:{r['id']}".encode()).hexdigest()[:8], 16) % 100
            r["split"] = "holdout" if pct < cfg.holdout_frac * 100 else "tune"
        n_hold = sum(1 for r in labelled if r["split"] == "holdout")
        if n_hold == 0 or len(labelled) - n_hold < 8:
            for r in labelled:
                r.pop("split", None)
        else:
            split.update(enabled=True, n_holdout=n_hold, n_tune=len(labelled) - n_hold)
    thresholds, tune_report = tune([r for r in results if r.get("split") != "holdout"], decide_fn)
    for r in results:
        r["flags"], r["suppressed"] = decide_fn(r, thresholds) if r["status"] == "ok" else ([], [])
    judge_stats = _judge_stats(results)
    return {"results": results, "skipped": skipped, "filtered": filtered, "thresholds": thresholds,
            "tune_report": tune_report, "lang_median": lang_median, "judge_stats": judge_stats, "split": split}


def _judge_stats(results):
    js = [r["signals"]["judge"] for r in results if r["signals"].get("judge")]
    if not js:
        return None
    n = len(js)
    return {
        "items_judged": n, "runs_per_item": js[0]["runs"],
        "verdict_consistency": round(sum(j["verdict_consistent"] for j in js) / n, 4),
        "category_consistency": round(sum(j["category_consistent"] for j in js) / n, 4),
        "parse_failures": sum(j["parse_failures"] for j in js),
        "ungrounded_quotes_dropped": sum(j["ungrounded_quotes"] for j in js),
    }
