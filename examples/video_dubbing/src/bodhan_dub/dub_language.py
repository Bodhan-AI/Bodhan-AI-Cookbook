"""Dub one language: synthesize each segment, fit it to its source speech window, QA it, render and place it.

Per segment:  TTS -> analyse clip (voiced length, pauses) -> plan fit against the source window
              -> (shorten English -> re-translate -> TTS, while still too long; up to max_condense times)
              -> back-transcribe QA -> (fresh TTS sample if it repeats/omits; up to max_qa_retries times)
Then:         render (trim edge silence, adjust pauses, stretch) -> place first sound on the source start.
Failures stay local: one failed segment never stops the others.
"""

from __future__ import annotations

import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import soundfile as sf

from . import condense as condense_mod
from . import logs
from .assemble import SPEECH_SR, build_speech_track
from .config import get_key
from .fit import END_SLACK, MAX_SPEED, MIN_SPEED, SHORT_TOL, budget_for, condense_target_chars, plan_fit
from .pausefit import MAX_ADD, analyze_file, render_clip
from .qa import qa_clip
from .schedule import DELAY_FLAG, schedule
from .sync_check import as_16k_mono, speech_regions, unit_edges
from .translate import check_translation, hard_failures, retry_missing_terms, translate_text
from .tts import synthesize

log = logging.getLogger(__name__)


@dataclass
class DubConfig:
    lang: str = "hi"
    voice: str = "Kavya"
    style: str | None = "educational lecture"
    concurrency: int = 3
    max_condense: int = 2
    max_qa_retries: int = 2
    qa: bool = True
    limit: int | None = None    # dev runs: only the first N segments
    until: float | None = None  # dev runs: only segments starting before this time (s)
    sync_rounds: int = 2        # closed loop: measure placed clips, correct, re-measure (0 = off)
    vad_backend: str = "auto"   # detector used by the closed loop (same as for the source)
    catchup_condense: bool = True  # a delayed clip that max speed cannot save gets a shorter text for its real time
    glossary: dict | None = None   # {english term: {lang: rendering}} (glossary.py); also kept by condensing
    code_mixed: bool = False       # re-translations use "<Language> (code-mixed)" like the first translation


RECONDENSE_TOL = 0.15   # s; a delayed clip that still sounds this far past its end limit is shortened again


@dataclass
class Deps:
    tts: Callable[[str, int], dict]                 # (text, variant) -> synthesize() result
    qa: Callable[[str, str], dict | None]           # (wav_path, reference text) -> judge dict or None
    translate: Callable[[str], str | None]          # (english) -> translation or None
    condense: Callable[[str, int, str, str], str | None]  # (en, target_chars, prev_en, next_en)


def default_deps(cfg: DubConfig, work_dir: Path) -> Deps:
    cache = work_dir / "cache"

    def translate(en: str) -> str | None:
        """Re-translation of a shortened English unit: same mode and glossary as the first translation. A
        missing glossary term gets the same one retry; it never rejects the translation."""
        key = get_key("translate")
        r = translate_text(en, cfg.lang, key, cache, code_mixed=cfg.code_mixed)
        if not r["ok"]:
            return None
        chk = check_translation(en, r["text"], cfg.lang, r.get("finish"), cfg.glossary, cfg.code_mixed)
        text, _, chk, _ = retry_missing_terms(en, r["text"], r.get("finish"), chk, cfg.lang, key, cache,
                                              cfg.glossary, cfg.code_mixed)
        return None if hard_failures(chk) else text

    keep_terms = tuple(cfg.glossary or ())

    return Deps(
        tts=lambda text, variant: synthesize(text, cfg.voice, cfg.lang, work_dir / cfg.lang / "tts",
                                             style=cfg.style, variant=variant),
        qa=lambda path, ref: qa_clip(path, ref, cfg.lang, get_key("transcribe"), cache),
        translate=translate,
        condense=lambda en, target, prev, nxt: condense_mod.condense(en, target, prev, nxt, cache_dir=cache,
                                                                     keep_terms=keep_terms),
    )


def _plan(info: dict, bud: dict, inner_pause: float) -> dict:
    max_extend = min(info["n_pauses"] * MAX_ADD, inner_pause)
    return plan_fit(info["voiced"], bud["budget"], bud["target"], info["max_shrink"], max_extend,
                    ideal=bud.get("ideal"))


def process_segment(seg: dict, bud: dict, prev_en: str, next_en: str, cfg: DubConfig, deps: Deps) -> dict:
    """Return a dub record for one segment (TTS, fit plan, QA). Never raises."""
    rec = {"id": seg["id"], "status": "ok", "voice": cfg.voice, "text": seg["translation"], "en": seg["src"],
           "tts_log": [], "condense_rounds": 0, "notes": [], "qa": None, "qa_attempts": 0,
           "target": bud["target"], "budget": bud["budget"]}
    text, en = seg["translation"], seg["src"]
    inner = seg.get("inner_pause", 0.0) or 0.0
    if not text.strip():
        return {**rec, "status": "skipped", "notes": ["no translation"]}

    def synth(t: str, variant: int) -> dict:
        r = deps.tts(t, variant)
        rec["tts_log"].append({"variant": variant, "chars": r.get("chars", len(t)), "dur": r.get("dur"),
                               "cached": r.get("cached", False), "ok": r["ok"], "error": r.get("error")})
        return r

    try:
        r = synth(text, 0)
        for v in (1, 2):  # transient TTS failure: try again
            if r["ok"]:
                break
            r = synth(text, v)
        if not r["ok"]:
            return {**rec, "status": "failed", "notes": [f"tts failed: {r.get('error')}"]}
        info = analyze_file(r["path"])
        plan = _plan(info, bud, inner)

        # --- too long even with pauses shrunk and max speed: shorten the English, re-translate, re-synthesize
        while rec["condense_rounds"] < cfg.max_condense and plan["action"] == "condense":
            target = condense_target_chars(len(en), info["voiced"], bud["budget"], info["max_shrink"])
            new_en = deps.condense(en, target, prev_en, next_en)
            if not new_en:
                rec["notes"].append("condense rejected")
                break
            new_text = deps.translate(new_en)
            if not new_text:
                rec["notes"].append("re-translate failed")
                break
            r2 = synth(new_text, 0)
            if not r2["ok"]:
                rec["notes"].append("re-synthesis failed")
                break
            info2 = analyze_file(r2["path"])
            if info2["voiced"] >= info["voiced"]:
                rec["notes"].append("condense gave no gain")
                break
            en, text, r, info = new_en, new_text, r2, info2
            rec["condense_rounds"] += 1
            plan = _plan(info, bud, inner)

        # --- QA: back-transcribe, retry with a fresh sample if it repeats or omits
        attempts, variant = [], 0
        while True:
            qa = deps.qa(r["path"], text) if cfg.qa else None
            attempts.append((r, qa, variant, info))
            rec["qa_attempts"] = len(attempts)
            if qa is None or qa["ok"] or len(attempts) > cfg.max_qa_retries:
                break
            variant += 1
            r_new = synth(text, variant)
            if not r_new["ok"]:
                break
            r, info = r_new, analyze_file(r_new["path"])
        good = [a for a in attempts if a[1] is None or a[1]["ok"]]
        r, qa, variant, info = good[0] if good else min(attempts, key=lambda a: a[1]["cer"])
        if qa is not None and not qa["ok"]:
            rec["status"] = "qa_failed"

        plan = _plan(info, bud, inner)
        if plan["action"] == "condense":
            rec["notes"].append("still too long after condensing: best effort (max speed, pauses shrunk)")
            plan = {**plan, "overrun": True}
        rec.update({"text": text, "en": en, "tts_path": r["path"], "tts_voiced": info["voiced"],
                    "tts_pauses": info["n_pauses"], "fit": plan, "qa": qa, "variant": variant})
        return rec
    except Exception as e:  # keep one bad segment from stopping the run
        log.exception("segment %s crashed", seg["id"])
        return {**rec, "status": "failed", "notes": rec["notes"] + [f"crash: {type(e).__name__}: {e}"]}


def recondense(r: dict, avail: float, target: float, prev_en: str, next_en: str, cfg: DubConfig, deps: Deps) -> bool:
    """Shorten a placed clip's English for the `avail` seconds it really has (onset -> end limit), re-translate,
    re-synthesize and QA it. Updates r and returns True only if the new clip is shorter and passes QA; otherwise
    r is left as it was (with a note)."""
    en = r["en"]
    info = analyze_file(r["tts_path"])
    want = condense_target_chars(len(en), info["voiced"], avail, info["max_shrink"])
    if want >= len(en):
        return False
    new_en = deps.condense(en, want, prev_en, next_en)
    if not new_en:
        r["notes"].append("catch-up condense rejected")
        return False
    new_text = deps.translate(new_en)
    if not new_text:
        r["notes"].append("catch-up re-translate failed")
        return False
    t = deps.tts(new_text, 0)
    r["tts_log"].append({"variant": 0, "chars": t.get("chars", len(new_text)), "dur": t.get("dur"),
                         "cached": t.get("cached", False), "ok": t["ok"], "error": t.get("error"), "catch_up": True})
    if not t["ok"]:
        return False
    info2 = analyze_file(t["path"])
    if info2["voiced"] >= info["voiced"]:
        r["notes"].append("catch-up condense gave no gain")
        return False
    qa = deps.qa(t["path"], new_text) if cfg.qa else None
    if qa is not None and not qa["ok"]:
        r["notes"].append(f"catch-up clip failed QA (cer {qa.get('cer')}): kept the earlier clip")
        return False
    plan = plan_fit(info2["voiced"], avail, target, info2["max_shrink"], 0.0, ideal=avail)
    if plan["action"] == "condense":
        plan = {**plan, "overrun": True}
    r.update({"text": new_text, "en": new_en, "tts_path": t["path"], "tts_voiced": info2["voiced"],
              "tts_pauses": info2["n_pauses"], "fit": plan, "qa": qa if qa is not None else r.get("qa"),
              "condense_rounds": r["condense_rounds"] + 1, "recondensed": round(avail, 3)})
    r["notes"].append(f"catch-up condense: shortened for the {avail:.2f}s it really had")
    return True


def dub_language(segments: list[dict], video_dur: float, work_dir: str | Path, cfg: DubConfig,
                 deps: Deps | None = None) -> tuple[list[dict], dict]:
    """Returns (dub records, summary). Writes <work_dir>/<lang>/dub.<lang>.json and the clip WAVs."""
    work_dir = Path(work_dir)
    out_dir = work_dir / cfg.lang
    clips_dir = out_dir / "clips"
    deps = deps or default_deps(cfg, work_dir)
    segs = sorted(segments, key=lambda s: s["start"])
    todo = [i for i, s in enumerate(segs) if cfg.until is None or s["start"] < cfg.until]
    if cfg.limit:
        todo = todo[: cfg.limit]

    def one(i: int) -> dict:
        logs.LANG.set(cfg.lang)      # records from this worker thread carry the language and segment
        logs.SEG.set(segs[i]["id"])
        prev_en = segs[i - 1]["src"] if i > 0 else ""
        next_en = segs[i + 1]["src"] if i + 1 < len(segs) else ""
        rec = process_segment(segs[i], budget_for(i, segs, video_dur), prev_en, next_en, cfg, deps)
        fit = rec.get("fit") or {}
        log.info("dub[%s] seg=%s status=%s voiced=%s target=%.2f fit=%s speed=%s pause_delta=%s condensed=%d qa=%s",
                 cfg.lang, rec["id"], rec["status"], rec.get("tts_voiced"), rec["target"], fit.get("action"),
                 fit.get("speed"), fit.get("pause_delta"), rec["condense_rounds"],
                 None if not rec["qa"] else rec["qa"]["cer"],
                 extra={"data": {"status": rec["status"], "en": rec.get("en"), "text": rec.get("text"),
                                 "tts_voiced": rec.get("tts_voiced"), "target": rec["target"],
                                 "budget": rec.get("budget"), "fit": fit, "condense_rounds": rec["condense_rounds"],
                                 "qa": rec.get("qa"), "qa_attempts": rec.get("qa_attempts"),
                                 "tts_log": rec.get("tts_log"), "notes": rec.get("notes")}})
        logs.SEG.set(None)
        return rec

    with ThreadPoolExecutor(max_workers=cfg.concurrency) as ex:
        recs = list(ex.map(one, todo))

    # --- render each clip (trim, pauses, speed), then place it
    by_id = {s["id"]: s for s in segs}
    pos = {s["id"]: i for i, s in enumerate(segs)}
    placeable = [r for r in recs if r["status"] in ("ok", "qa_failed")]

    def render(r: dict, tag: str = "") -> None:
        clip = clips_dir / f"seg_{r['id']:04d}{tag}.wav"
        try:
            info = render_clip(r["tts_path"], clip, r["fit"]["pause_delta"], r["fit"]["speed"])
        except Exception as e:
            r["notes"].append(f"render failed, using plain clip: {e}")
            info = render_clip(r["tts_path"], clip, 0.0, 1.0)
            r["fit"] = {**r["fit"], "pause_delta": 0.0, "speed": 1.0, "render_failed": True}
        r.update({"clip_path": str(clip), "clip_dur": info["dur"], "clip_lead": info["lead"],
                  "clip_voiced_end": info["voiced_end"], "model_lead": info["lead"],
                  "model_voiced_end": info["voiced_end"], "pause_change": info["pause_change"],
                  "speed": r["fit"]["speed"]})

    def place() -> list[dict]:
        return schedule([{"id": r["id"], "start": by_id[r["id"]]["start"], "end": by_id[r["id"]]["end"],
                          "dur": r["clip_dur"], "lead": r["clip_lead"], "voiced_end": r["clip_voiced_end"]}
                         for r in placeable], video_dur)

    for r in placeable:
        render(r)
    placed = place()

    # --- catch-up: a clip pushed late by its neighbour is refitted (more pause shrink / speed, still <= max),
    #     so the delay is absorbed right there instead of rippling through the following clips. If max speed is
    #     not enough (measured: the delayed clips were usually at 1.2x already), its English is shortened for
    #     the time it really has (catch-up condense), so the delay stops at this clip.
    idx = {r["id"]: k for k, r in enumerate(placeable)}
    tried: set = set()
    while True:  # one clip at a time (earliest delayed first), so only clips that need it are touched
        nxt = next(((r, p) for r, p in zip(placeable, placed) if p["delay"] > 0.05 and r["id"] not in tried), None)
        if nxt is None:
            break
        r, p = nxt
        tried.add(r["id"])
        bud = budget_for(pos[r["id"]], segs, video_dur)
        left = max(bud["end_limit"] - p["onset"], 0.3)
        info = analyze_file(r["tts_path"])
        plan = plan_fit(info["voiced"], left, bud["target"], info["max_shrink"], 0.0, ideal=left)
        if plan["speed"] > r["fit"]["speed"] + 0.005 or plan["pause_delta"] < r["fit"]["pause_delta"] - 0.005:
            r["fit"] = {**r["fit"], "pause_delta": plan["pause_delta"], "speed": plan["speed"],
                        "caught_up": round(p["delay"], 3)}
            r["notes"].append(f"catch-up after a {p['delay']:.2f}s forced delay")
            render(r)
            placed = place()
        p = placed[idx[r["id"]]]
        limit = min(bud["end_limit"], video_dur)
        if (cfg.catchup_condense and p["delay"] > DELAY_FLAG and p["voiced_end_at"] - limit > RECONDENSE_TOL
                and r["status"] == "ok"):
            i = pos[r["id"]]
            if recondense(r, max(limit - p["onset"], 0.3), bud["target"], segs[i - 1]["src"] if i > 0 else "",
                          segs[i + 1]["src"] if i + 1 < len(segs) else "", cfg, deps):
                render(r)
                placed = place()

    for r, p in zip(placeable, placed):
        r.update(p)

    # --- closed loop: hear the placed clips with the SAME detector used on the source, correct, re-measure
    loop = {"rounds": []}
    if cfg.sync_rounds > 0 and placeable:
        loop = sync_loop(placeable, segs, pos, video_dur, render, place, out_dir / "sync_loop", cfg)

    for r in placeable:
        if r["fit"].get("overrun"):
            r["flags"].append("overrun")

    summary = {**summarize(recs, cfg), "sync_loop": loop}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"dub.{cfg.lang}.json").write_text(
        json.dumps({"summary": summary, "segments": recs}, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("dub[%s] summary: %s", cfg.lang, summary)
    return recs, summary


# --------------------------------------------------------------------------- closed loop
LOOP_TOL = 0.04   # disagreements below this are measurement noise (10 ms frames, 30 ms sustain)
_CLIP_STATE = ("clip_path", "clip_dur", "clip_lead", "clip_voiced_end", "model_lead", "model_voiced_end",
               "pause_change", "speed", "fit")


def _unit_errors(edges: dict, recs: list[dict], by_id: dict) -> dict:
    return {r["id"]: (edges[r["id"]]["on"] - by_id[r["id"]]["start"], edges[r["id"]]["off"] - by_id[r["id"]]["end"])
            for r in recs if r["id"] in edges}


def _score(errs: dict) -> float:
    """Mean of |start error| + how far the end falls outside [end - SHORT_TOL, end + END_SLACK]."""
    if not errs:
        return 0.0
    return sum(abs(s) + max(0.0, e - END_SLACK) + max(0.0, -SHORT_TOL - e) for s, e in errs.values()) / len(errs)


def _stats(errs: dict) -> dict:
    s = [abs(a) for a, _ in errs.values()]
    e = [b for _, b in errs.values()]
    n = max(1, len(errs))
    return {"units": len(errs), "starts_within_0.1s": sum(x <= 0.1 for x in s),
            "start_err_abs_mean": round(sum(s) / n, 3),
            "ends_in_window": sum(-SHORT_TOL - LOOP_TOL <= x <= END_SLACK + LOOP_TOL for x in e),
            "end_err_mean": round(sum(e) / n, 3)}


def sync_loop(placeable: list[dict], segs: list[dict], pos: dict, video_dur: float, render: Callable,
              place: Callable, work: Path, cfg: DubConfig) -> dict:
    """Measure the placed clips with the same detector + edge rule as the source, then:
      1. replace each clip's assumed first/last sound with what the detector hears, and re-place it
         (so the HEARD start lands on the source start);
      2. if a clip's heard end now falls outside its window, re-stretch it (still 0.85x - 1.2x);
    then measure again. A round that makes things worse is undone. Returns a per-round log."""
    work.mkdir(parents=True, exist_ok=True)
    by_id = {s["id"]: s for s in segs}
    span = min(video_dur, max(r["placed_end"] for r in placeable) + 1.0)

    def apply(placed: list[dict]) -> None:
        for r, p in zip(placeable, placed):
            r.update(p)

    def measure(tag: str) -> dict:
        p24 = work / f"track_{tag}.wav"
        sf.write(str(p24), build_speech_track(placeable, span), SPEECH_SR, subtype="PCM_16")
        p16 = as_16k_mono(p24, work, f"track_{tag}_16k.wav")
        edges = unit_edges(speech_regions(p16, cfg.vad_backend), placeable)
        p24.unlink(missing_ok=True)
        return edges

    def snapshot() -> list[dict]:
        return [copy.deepcopy({k: r[k] for k in _CLIP_STATE if k in r}) for r in placeable]

    def restore(snap: list[dict]) -> None:
        for r, s in zip(placeable, snap):
            r.update(copy.deepcopy(s))

    edges = measure("r0")
    errs = _unit_errors(edges, placeable, by_id)
    rounds = [{"round": 0, "score": round(_score(errs), 4), **_stats(errs)}]
    best = (_score(errs), snapshot(), edges)
    shifted_all, refit_all = set(), set()
    for rnd in range(1, cfg.sync_rounds + 1):
        shifted, refit = [], []
        for r in placeable:                      # 1. what the detector hears replaces our assumption
            e = edges.get(r["id"])
            if e is None:
                continue
            lead_m, end_m = e["on"] - r["placed_start"], e["off"] - r["placed_start"]
            if abs(lead_m - r["clip_lead"]) > LOOP_TOL or abs(end_m - r["clip_voiced_end"]) > LOOP_TOL:
                r["clip_lead"], r["clip_voiced_end"] = round(lead_m, 3), round(end_m, 3)
                shifted.append(r["id"])
        apply(place())
        for r in placeable:                      # 2. ends outside the window: re-stretch within limits
            s, bud = by_id[r["id"]], budget_for(pos[r["id"]], segs, video_dur)
            heard = r["clip_voiced_end"] - r["clip_lead"]
            end_at, hi, lo, sp = r["onset"] + heard, bud["end_limit"], by_id[r["id"]]["end"] - SHORT_TOL, r["speed"]
            new = None
            if end_at > hi + LOOP_TOL and sp < MAX_SPEED - 1e-3:
                new = min(MAX_SPEED, sp * heard / max(hi - r["onset"], 0.1))
            elif end_at < lo - LOOP_TOL and sp > MIN_SPEED + 1e-3:
                new = max(MIN_SPEED, sp * heard / max(s["end"] - r["onset"], 0.1),
                          sp * heard / max(hi - r["onset"], 0.1))   # never slow it past its end limit
                new = new if new < sp else None
            if new is None or abs(new - sp) < 0.01:
                continue
            corr_l, corr_e = r["clip_lead"] - r["model_lead"], r["clip_voiced_end"] - r["model_voiced_end"]
            r["fit"] = {**r["fit"], "speed": round(new, 4), "loop_refit": rnd}
            render(r, tag=f".r{rnd}")
            k = sp / new                              # the detector's offsets scale with the stretch
            r["clip_lead"] = round(r["model_lead"] + corr_l * k, 3)
            r["clip_voiced_end"] = round(r["model_voiced_end"] + corr_e * k, 3)
            refit.append(r["id"])
        if not shifted and not refit:
            break
        apply(place())
        edges = measure(f"r{rnd}")
        errs = _unit_errors(edges, placeable, by_id)
        score = _score(errs)
        rounds.append({"round": rnd, "score": round(score, 4), "shifted": len(shifted), "refit": len(refit),
                       **_stats(errs)})
        if score > best[0] + 1e-3:               # worse: undo this round and stop
            restore(best[1])
            apply(place())
            edges = best[2]
            rounds[-1]["reverted"] = True
            break
        best = (score, snapshot(), edges)
        shifted_all.update(shifted)
        refit_all.update(refit)
    final = _unit_errors(best[2], placeable, by_id)
    for r in placeable:
        if r["id"] in final:
            r["measured_start_err"], r["measured_end_err"] = (round(x, 3) for x in final[r["id"]])
        if r["id"] in shifted_all or r["id"] in refit_all:
            r["notes"].append("closed loop: corrected to what the detector hears")
    log.info("dub[%s] closed loop: %s", cfg.lang, rounds)
    return {"rounds": rounds, "units_shifted": len(shifted_all), "units_refit": len(refit_all)}


def summarize(recs: list[dict], cfg: DubConfig) -> dict:
    placed = [r for r in recs if "placed_start" in r]
    billed_chars = sum(t["chars"] for r in recs for t in r["tts_log"] if t["ok"] and not t["cached"])
    speeds = [r["speed"] for r in placed]

    def count(flag: str) -> int:
        return sum(flag in r["flags"] for r in placed)

    return {
        "segments": len(recs),
        "dubbed": len(placed),
        "failed": sum(r["status"] == "failed" for r in recs),
        "skipped": sum(r["status"] == "skipped" for r in recs),
        "qa_failed": sum(r["status"] == "qa_failed" for r in recs),
        "condensed": sum(r["condense_rounds"] > 0 for r in recs),
        "recondensed_on_catch_up": sum("recondensed" in r for r in recs),
        "tts_calls": sum(len(r["tts_log"]) for r in recs),
        "tts_chars_billed": billed_chars,
        "tts_cost_inr": round(billed_chars / 10000 * 6, 2),
        "speed_max": max(speeds, default=None), "speed_min": min(speeds, default=None),
        "sped_up": sum(s > 1.005 for s in speeds), "slowed_down": sum(s < 0.995 for s in speeds),
        "pauses_adjusted": sum(abs(r.get("pause_change", 0)) > 0.01 for r in placed),
        "delayed": count("delayed"), "sync_miss": count("sync_miss"),
        "late_end": count("late_end"), "early_end": count("early_end"), "overrun": count("overrun"),
        "max_speed_allowed": MAX_SPEED,
        "mean_cer": round(sum(r["qa"]["cer"] for r in recs if r["qa"]) / max(1, sum(bool(r["qa"]) for r in recs)), 3),
        "voice": cfg.voice, "style": cfg.style,
    }


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if len(sys.argv) < 5:
        sys.exit("usage: python -m bodhan_dub.dub_language <work_dir> <lang> <all|N|until=SEC> <video_seconds>")
    work, lang = Path(sys.argv[1]), sys.argv[2]
    arg = sys.argv[3]
    video_dur = float(sys.argv[4])
    cfg = DubConfig(lang=lang)
    if arg.startswith("until="):
        cfg.until = float(arg.split("=", 1)[1])
    elif arg != "all":
        cfg.limit = int(arg)
    segments = json.loads((work / lang / f"segments.{lang}.json").read_text(encoding="utf-8"))
    dub_language(segments, video_dur, work, cfg)
