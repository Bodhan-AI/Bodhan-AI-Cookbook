"""Turn signals into flags. Pure function of (signals, thresholds): re-running it with new
thresholds costs nothing, which is what makes calibration possible.

Policy
 * HARD checks (numbers, format, script, speech tail loss...) flag on their own.
 * SOFT candidates (back-translation coverage, length prior) are checker-noise-prone: they flag
   only if very strong, or if the LLM judge independently confirms; otherwise they are recorded
   in `suppressed` with the reason, so a human can see what was filtered and why.
 * Judge findings flag when enough of the repeated judge runs agree.
"""
from __future__ import annotations

from .align import overlap

DEFAULTS = {
    "script_min_frac": 0.6,
    "len_low": 0.5, "len_high": 1.9,
    "bt_cov_omit": 0.4, "bt_cov_omit_hard": 0.15,
    "bt_extra_add": 0.5, "bt_extra_add_hard": 0.85,
    "bt_cov_mistr": 0.75,
    "term_sent_cov_min": 0.7,
    "judge_min_frac": 0.5,
    "speech_tail_tokens": 3, "speech_del_frac": 0.2, "speech_del_run": 4,
    "silence_max_s": 2.0, "silence_frac": 0.4, "clip_frac": 0.01,
    "cps_min": 2.0, "cps_max": 40.0, "cer_garbled": 0.6,
}

TUNE_GRID = {
    "script_min_frac": [0.3, 0.45, 0.6, 0.75, 0.9],
    "len_low": [0.3, 0.4, 0.5, 0.6, 0.7],
    "len_high": [1.4, 1.6, 1.9, 2.3, 3.0],
    "bt_cov_omit": [0.2, 0.3, 0.4, 0.5, 0.6],
    "bt_cov_omit_hard": [0.05, 0.15, 0.25],
    "bt_extra_add": [0.3, 0.4, 0.5, 0.6, 0.7],
    "bt_extra_add_hard": [0.7, 0.85, 1.01],
    "judge_min_frac": [0.34, 0.5, 0.67, 1.0],
    "speech_del_frac": [0.1, 0.15, 0.2, 0.3],
    "speech_tail_tokens": [2, 3, 4, 6],
    "silence_max_s": [1.0, 1.5, 2.0, 3.0],
    "cer_garbled": [0.4, 0.6, 0.8],
}

SEV_RANK = {"minor": 0, "major": 1, "critical": 2}


def _flag(cat, sev, check, ss=None, st=None, ev="", conf=1.0):
    return {"category": cat, "severity": sev, "checks": [check], "span_source": ss, "span_translation": st,
            "evidence": ev, "confidence": round(conf, 3)}


def decide(item: dict, sig: dict, thr: dict, lang_median: float | None):
    flags: list[dict] = []
    supp: list[dict] = []
    tx = sig.get("text") or {}
    judge = sig.get("judge")
    jf = [f for f in (judge or {}).get("findings", []) if f["frac"] >= thr["judge_min_frac"]]
    jlow = [f for f in (judge or {}).get("findings", []) if f["frac"] < thr["judge_min_frac"]]

    bt0 = (tx.get("bt") or {}) if tx else {}
    src_all, tr_all = bt0.get("src_all", []), bt0.get("tr_all", [])

    def sent_id(side, span):
        """Which sentence a span sits in. Source and translation sentence ids are only comparable
        when both have the same number of sentences."""
        if not span:
            return None
        lst = src_all if side == "source" else tr_all
        best, bo = None, 0
        for i, sp in enumerate(lst):
            o = overlap(sp, span)
            if o > bo:
                best, bo = i, o
        if best is None:
            return None
        if src_all and len(src_all) == len(tr_all):
            return f"s{best}"
        return f"{'src' if side == 'source' else 'tr'}{best}"

    def confirmed(cats, ss=None, st=None):
        """A judge finding confirms a candidate only if it points at the same place:
        overlapping span on the same side, or the same sentence when the sides differ."""
        for f in jf:
            if f["category"] not in cats:
                continue
            fside = f["side"]
            same_side = ss if fside == "source" else st
            other_side = st if fside == "source" else ss
            if same_side is not None:
                if overlap(f["span"], same_side) > 0:
                    return f
                continue
            if other_side is not None:
                cid = sent_id("translation" if fside == "source" else "source", other_side)
                fid = sent_id(fside, f["span"])
                if cid is not None and fid is not None and cid != fid:
                    continue
            return f  # candidate has no location of its own, or sentences cannot be compared
        return None

    def candidate(cat, ss, st, ev, strong, check, sev="major", cats=None):
        f = confirmed(cats or (cat,), ss, st)
        if f:
            flags.append(_flag(cat, f["severity"], check, ss, st, ev + f" | judge: {f['explanation']}", 0.5 + f["frac"] / 2))
        elif strong:
            flags.append(_flag(cat, sev, check, ss, st, ev, 0.6))
        else:
            supp.append({"category": cat, "check": check, "evidence": ev, "span_source": ss, "span_translation": st,
                         "reason": "unconfirmed_by_judge" if judge is not None else "weak_signal_no_judge"})

    if tx:
        # ---- hard: numbers / units
        ns = tx.get("numbers") or {}
        for c in ns.get("changed", []):
            flags.append(_flag("number", "major", "numbers", c["src"]["span"], c["tr"]["span"],
                               f"source has {c['src']['value']}, translation has {c['tr']['value']}"))
        for m in ns.get("missing", []):
            flags.append(_flag("number", "major", "numbers", m["span"], None,
                               f"source number {m['value']} not found in translation"))
        for e in ns.get("extra", []):
            flags.append(_flag("number", "major", "numbers", None, e["span"],
                               f"translation number {e['value']} not in source"))
        for u in ns.get("unit_changes", []):
            flags.append(_flag("number", "major", "numbers", u["src"]["span"], u["tr"]["span"],
                               f"unit changed {u['src']['unit']} -> {u['tr']['unit']} for {u['src']['value']}"))
        # ---- hard: format
        for iss in tx.get("format") or []:
            flags.append(_flag("format", "major", "format", None, iss["span"], iss["detail"]))
        # ---- hard: script / untranslated
        sc = tx.get("script") or {}
        fe = sc.get("frac_expected")
        if sc.get("same_as_source"):
            flags.append(_flag("wrong_language", "critical", "script", None, None, "translation identical to source (untranslated)"))
        elif fe is not None and fe < thr["script_min_frac"]:
            flags.append(_flag("wrong_language", "critical" if fe < 0.2 else "major", "script", None, None,
                               f"only {fe:.0%} of letters are in the expected script {sc.get('expected')}"))
        elif sc.get("latin_run"):
            r = sc["latin_run"]
            candidate("wrong_language", None, r["span"], f"{r['words']} consecutive Latin-script words left in translation",
                      False, "script", "major")
        # ---- soft: length prior
        lr = tx.get("length_ratio")
        if lr is not None and lang_median:
            rel = lr / lang_median
            if rel < thr["len_low"]:
                candidate("omission", None, None, f"translation is {rel:.2f}x the typical length for this language", False, "length_prior")
            elif rel > thr["len_high"]:
                candidate("addition", None, None, f"translation is {rel:.2f}x the typical length for this language", False, "length_prior")
        # ---- soft: back-translation
        bt = tx.get("bt")
        if bt:
            for s in bt["src_sents"]:
                cov, ref = s["coverage"], s.get("coverage_ref")
                if cov < thr["bt_cov_omit"]:
                    ev = f"only {cov:.0%} of source content words survive back-translation"
                    if ref is not None and ref < thr["bt_cov_omit"]:
                        supp.append({"category": "omission", "check": "back_translation", "evidence": ev,
                                     "span_source": s["span"], "span_translation": None,
                                     "reason": f"checker_noise: reference back-translation also covers only {ref:.0%}"})
                    else:
                        candidate("omission", s["span"], None, ev, cov < thr["bt_cov_omit_hard"], "back_translation")
                elif cov < thr["bt_cov_mistr"] and s["n_uncovered"] <= 3 and s["uncovered"]:
                    sp = [s["uncovered"][0][0], s["uncovered"][-1][1]]
                    candidate("mistranslation", sp, None, f"source words {s['uncovered_words']} lost in back-translation",
                              False, "back_translation", cats=("mistranslation", "term"))
                if cov >= thr["term_sent_cov_min"]:
                    for e in s["entities_missing"]:
                        candidate("term", e["span"], None, f"entity '{e['word']}' not recoverable from back-translation "
                                  f"although the rest of the sentence is ({cov:.0%})", False, "back_translation")
            for s in bt["tr_sents"]:
                if s["extra_frac"] > thr["bt_extra_add"]:
                    candidate("addition", None, s["span"],
                              f"{s['extra_frac']:.0%} of back-translated content ({s['extra_words']}) has no counterpart in source",
                              s["extra_frac"] > thr["bt_extra_add_hard"], "back_translation")

    # ---- judge findings (those not already explained by a candidate are added; merge dedups)
    for f in jf:
        ss = f["span"] if f["side"] == "source" else None
        st = f["span"] if f["side"] == "translation" else None
        flags.append(_flag(f["category"], f["severity"], "judge", ss, st,
                           f"judge: {f['explanation']} [quote: {f['quote']!r}, agreement {f['frac']:.0%}]", f["frac"]))
    for f in jlow:
        supp.append({"category": f["category"], "check": "judge", "evidence": f["explanation"],
                     "span_source": f["span"] if f["side"] == "source" else None,
                     "span_translation": f["span"] if f["side"] == "translation" else None,
                     "reason": f"judge_low_agreement ({f['frac']:.0%} of runs)"})

    # ---- speech
    sp = sig.get("speech")
    if sp:
        a = sp["audio"]
        if a.get("empty"):
            flags.append(_flag("garbled", "critical", "audio", ev="audio is empty"))
        else:
            if "transcript" in sp:
                tail, run, frac = sp["tail_deleted"], sp["max_deleted_run"], sp["deleted_frac"]
                if tail >= thr["speech_tail_tokens"] or run >= thr["speech_del_run"] or frac >= thr["speech_del_frac"]:
                    r = sp["deleted_runs"][0] if sp["deleted_runs"] else {"span": None}
                    ev = f"{frac:.0%} of input words missing from the transcript; longest missing run {run} words" + \
                         (f", {tail} at the end (truncation)" if tail else "")
                    flags.append(_flag("speech_omission", "major", "transcribe", r["span"], None, ev, 0.8))
                rep = sp.get("repetition")
                if rep:
                    flags.append(_flag("repetition", "major", "transcribe", rep["span"], None,
                                       f"phrase repeated in speech but not in text: '{rep['gram']}'", 0.8))
                if sp["cer"] is not None and sp["cer"] >= thr["cer_garbled"] and frac < thr["speech_del_frac"]:
                    flags.append(_flag("garbled", "major", "transcribe", None, None,
                                       f"character error rate {sp['cer']:.0%} with few deletions: speech unintelligible", 0.7))
            if a["max_internal_silence_s"] >= thr["silence_max_s"]:
                flags.append(_flag("garbled", "major", "audio", None, None, f"{a['max_internal_silence_s']}s of silence mid-utterance", 0.8))
            elif a["silence_frac"] >= thr["silence_frac"]:
                flags.append(_flag("garbled", "major", "audio", None, None, f"{a['silence_frac']:.0%} of the audio is silent", 0.7))
            if a["clip_frac"] >= thr["clip_frac"]:
                flags.append(_flag("garbled", "minor", "audio", None, None, f"{a['clip_frac']:.1%} of samples clipped", 0.8))
            cps = a.get("chars_per_s")
            if cps is not None and "transcript" not in sp:
                if cps < thr["cps_min"]:
                    flags.append(_flag("speech_omission", "major", "audio", None, None, f"{cps} chars/s is far too slow for the text", 0.5))
            if cps is not None and cps > thr["cps_max"]:
                flags.append(_flag("speech_omission", "major", "audio", None, None, f"{cps} chars/s: audio too short for the text (truncated?)", 0.6))

    if judge is None:  # no judge: two independent weak signals for omission/addition corroborate each other
        for cat in ("omission", "addition"):
            lst = [x for x in supp if x["category"] == cat]
            if len({x["check"] for x in lst}) >= 2 and not any(f["category"] == cat for f in flags):
                flags.append(_flag(cat, "major", "corroborated",
                                   next((x["span_source"] for x in lst if x["span_source"]), None),
                                   next((x["span_translation"] for x in lst if x["span_translation"]), None),
                                   "corroborated by independent weak signals: " + " | ".join(x["evidence"] for x in lst), 0.6))
                flags[-1]["checks"] = sorted({x["check"] for x in lst})
                supp[:] = [x for x in supp if x["category"] != cat]
    for f in flags:
        f["sentence"] = sent_id("source", f["span_source"]) or sent_id("translation", f["span_translation"])
    return fold_explained(merge(flags)), supp


def merge(flags: list[dict]) -> list[dict]:
    """Merge flags of the same category that point at the same place (or have no place)."""
    out: list[dict] = []
    for f in flags:
        for o in out:
            if o["category"] != f["category"]:
                continue
            same = (
                (o.get("sentence") is not None and o.get("sentence") == f.get("sentence"))
                or (overlap(o["span_source"], f["span_source"]) > 0) or (overlap(o["span_translation"], f["span_translation"]) > 0)
                or (not o["span_source"] and not o["span_translation"]) or (not f["span_source"] and not f["span_translation"])
            )
            if same:
                o["checks"] = sorted(set(o["checks"]) | set(f["checks"]))
                o["span_source"] = o["span_source"] or f["span_source"]
                o["span_translation"] = o["span_translation"] or f["span_translation"]
                o["sentence"] = o.get("sentence") or f.get("sentence")
                if f["evidence"] not in o["evidence"]:
                    o["evidence"] += " || " + f["evidence"]
                if SEV_RANK[f["severity"]] > SEV_RANK[o["severity"]]:
                    o["severity"] = f["severity"]
                o["confidence"] = max(o["confidence"], f["confidence"])
                break
        else:
            out.append(dict(f))
    return out


def fold_explained(flags: list[dict]) -> list[dict]:
    """A number that is missing only because an omitted sentence held it (or extra only because an added
    sentence held it) is the same defect, not a second one: fold it into the omission/addition flag."""
    out = list(flags)
    for nf in [f for f in flags if f["category"] == "number"]:
        if nf["span_source"] and not nf["span_translation"]:
            host = next((f for f in out if f["category"] == "omission" and overlap(f["span_source"], nf["span_source"]) > 0), None)
        elif nf["span_translation"] and not nf["span_source"]:
            host = next((f for f in out if f["category"] == "addition" and overlap(f["span_translation"], nf["span_translation"]) > 0), None)
        else:
            host = None
        if host is not None:
            host["evidence"] += " || includes: " + nf["evidence"]
            out.remove(nf)
    return out
