"""Report writers. Everything here is a pure function of the evaluated items, so a cached
rerun produces byte-identical files. Wall-clock time and money spent *this run* go to
run.json only."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from bodhan_eval.flags import CATEGORIES

MQM_WEIGHTS = {"minor": 1, "major": 5, "critical": 10}
REPORTED_METRICS = ("chrf_ref", "chrf_backtranslation", "speech_cer", "back_translation", "transcript", "audio", "audio_segments", "judge_verdict")
HEADLINE_METRICS = ("detection_recall", "false_alarm_rate", "localization", "category_accuracy", "targets")


def dump_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _snippet(text: str | None, span, width: int = 60) -> str:
    if not text or not span:
        return ""
    a, b = span
    s = text[a:b] if b > a else text[max(0, a - 20):a + 20]
    s = re.sub(r"\s+", " ", s)
    return (s[:width] + "…") if len(s) > width else s


def _evidence_line(flag: dict) -> str:
    ev = flag["evidence"][0] if flag["evidence"] else {}
    parts = [f"{k}={v}" for k, v in ev.items() if k not in {"check", "back_translation"} and v not in (None, "", [], {})]
    s = f"[{ev.get('check', '?')}] " + "; ".join(str(p) for p in parts)
    s = re.sub(r"\s+", " ", s)
    return s[:220] + ("…" if len(s) > 220 else "")


def item_record(st) -> dict:
    it = st.item
    m = st.metrics
    return {
        "id": it["id"],
        "lang": it["lang"],
        "status": st.status,
        "checks_run": st.checks_run,
        "skipped": st.skipped,
        "generated_translation": bool(it.get("generated_translation")),
        "translation": it.get("translation"),
        "audio": st.audio_out or it.get("audio"),
        "metrics": {k: m[k] for k in REPORTED_METRICS if k in m},
        "flags": m.get("flags", []),
        "labels": [{**lab, "span": list(lab["span"]) if lab["span"] else None} for lab in it["labels"]] if it.get("labels") is not None else None,
        "model_cost_inr": round(st.cost_inr, 5),
    }


def mqm(records: list[dict]) -> float | None:
    words = sum(len(r["source"].split()) for r in records)
    if not words:
        return None
    penalty = sum(MQM_WEIGHTS.get(f["severity"], 5) for r in records for f in r["flags"])
    return round(max(0.0, 100.0 - 100.0 * penalty / words), 2)


def build_summary(records: list[dict], sources: dict, load_stats: dict, calibration: dict | None) -> dict:
    by_lang = defaultdict(list)
    for r in records:
        by_lang[r["lang"]].append({**r, "source": sources[(r["id"], r["lang"])]})
    langs = {}
    for lang, rows in sorted(by_lang.items()):
        cats = Counter(f["category"] for r in rows for f in r["flags"])
        chrf = [r["metrics"]["chrf_backtranslation"] for r in rows if "chrf_backtranslation" in r["metrics"]]
        chrf_ref = [r["metrics"]["chrf_ref"] for r in rows if "chrf_ref" in r["metrics"]]
        cer = [r["metrics"]["speech_cer"] for r in rows if "speech_cer" in r["metrics"]]
        langs[lang] = {
            "items": len(rows),
            "flagged_items": sum(1 for r in rows if r["flags"]),
            "flags_by_category": dict(sorted(cats.items())),
            "flags_by_severity": dict(sorted(Counter(f["severity"] for r in rows for f in r["flags"]).items())),
            "speech_checked": len(cer),
            "mean_backtranslation_chrf": round(sum(chrf) / len(chrf), 2) if chrf else None,
            "items_with_reference": len(chrf_ref),
            "mean_reference_chrf": round(sum(chrf_ref) / len(chrf_ref), 2) if chrf_ref else None,
            "mean_speech_cer": round(sum(cer) / len(cer), 4) if cer else None,
            "mqm_score": mqm(rows),
            "model_cost_inr": round(sum(r["model_cost_inr"] for r in rows), 4),
        }
    skipped = Counter(k for r in records for k in r["skipped"])
    return {
        "items_in_dataset": load_stats["lines"],
        "items_evaluated": len(records),
        "bad_items": len(load_stats["bad"]),
        "bad_item_reasons": load_stats["bad"][:50],
        "filtered_by_lang": load_stats["filtered_lang"],
        "item_errors": sum(1 for r in records if r["status"] == "error"),
        "skipped_checks": dict(sorted(skipped.items())),
        "flagged_items": sum(1 for r in records if r["flags"]),
        "flags_by_category": dict(sorted(Counter(f["category"] for r in records for f in r["flags"]).items())),
        "per_language": langs,
        "model_cost_inr": round(sum(r["model_cost_inr"] for r in records), 4),
        "calibration": {k: calibration[k] for k in HEADLINE_METRICS} if calibration and calibration.get("labelled_items") else None,
    }


def write_summary_md(path: Path, summary: dict, records: list[dict], calibration: dict | None, diff: dict | None) -> None:
    L = ["# Quality report", ""]
    L += [f"- Items evaluated: **{summary['items_evaluated']}** (bad lines skipped: {summary['bad_items']}, "
          f"other languages filtered: {summary['filtered_by_lang']}, item errors: {summary['item_errors']})",
          f"- Items flagged: **{summary['flagged_items']}**",
          f"- Model cost of all outputs evaluated: ₹{summary['model_cost_inr']:.2f} (see run.json for money spent in this run)"]
    if summary["skipped_checks"]:
        L.append(f"- Checks skipped: {', '.join(f'{k} ×{v}' for k, v in summary['skipped_checks'].items())}")
    L += ["", "## Per language", "",
          "| lang | items | flagged | MQM | ref chrF++ | BT chrF++ | speech checked | speech CER | top categories |",
          "|---|---|---|---|---|---|---|---|---|"]
    for lang, s in summary["per_language"].items():
        top = ", ".join(f"{c} {n}" for c, n in Counter(s["flags_by_category"]).most_common(3)) or "–"
        ref = f"{s['mean_reference_chrf']} (n={s['items_with_reference']})" if s["items_with_reference"] else "–"
        L.append(f"| {lang} | {s['items']} | {s['flagged_items']} | {s['mqm_score']} | {ref} | {s['mean_backtranslation_chrf']} | "
                 f"{s['speech_checked']} | {s['mean_speech_cer']} | {top} |")
    L += ["", "## Flags per category", "", "| category | " + " | ".join(summary["per_language"]) + " |",
          "|---|" + "---|" * len(summary["per_language"])]
    for c in CATEGORIES:
        counts = [summary["per_language"][lang]["flags_by_category"].get(c, 0) for lang in summary["per_language"]]
        if any(counts):
            L.append(f"| {c} | " + " | ".join(map(str, counts)) + " |")

    if calibration and calibration.get("labelled_items"):
        notes = calibration.get("target_notes", {})
        L += ["", "## Quality targets", "", f"Measured on {calibration.get('targets_measured_on', 'labelled items')}.", "",
              "| metric | value | target | met | why missed |", "|---|---|---|---|---|"]
        for t in calibration["targets"]:
            met = "–" if t["met"] is None else ("yes" if t["met"] else "**no**")
            L.append(f"| {t['metric']} | {t['value']} | {t['target']} | {met} | {notes.get(t['metric'], '')} |")
        L += ["", "Per language (all labelled items):", "",
              "| lang | seeded errors | clean | recall | false alarms | localization | category accuracy |", "|---|---|---|---|---|---|---|"]
        for lang, m in calibration["per_language"].items():
            L.append(f"| {lang} | {m['seeded_errors']} | {m['clean_items']} | {m['detection_recall']} | {m['false_alarm_rate']} | "
                     f"{m['localization']} | {m['category_accuracy']} |")
        L += ["", "Per category (item level, all labelled items):", "", "| category | precision | recall | threshold |", "|---|---|---|---|"]
        for c, m in calibration["per_category"].items():
            L.append(f"| {c} | {m['precision']} | {m['recall']} | {calibration['thresholds'][c]} |")
        hr = calibration.get("human_review")
        if hr:
            who = ", ".join(f"{a} ({n})" for a, n in hr["annotators"].items())
            L += ["", "## Human-reviewed flags", "",
                  f"{hr['judgments']} flags were judged by a person ({who}): {hr['errors_confirmed']} real errors, "
                  f"{hr['false_alarms_confirmed']} false alarms. These come from the review queue, i.e. flags near their threshold, "
                  "so this sample is harder than average.", "",
                  f"Harness flag precision **{hr['precision']}**, recall **{hr['recall']}**, agreement with the person **{hr['agreement']}**"
                  + (f" ({hr['skipped_not_in_run']} verdicts were for items not in this run)." if hr["skipped_not_in_run"] else "."),
                  f"Of the {hr['fn']} real errors not flagged in their own category, {hr['missed_but_flagged_as_other_category']} "
                  "were flagged under another category (for example a dropped number caught as an omission).", "",
                  "| category | real errors flagged | false alarms | missed | cleared correctly |", "|---|---|---|---|---|"]
            for c, m in hr["per_category"].items():
                L.append(f"| {c} | {m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} |")
        per_check = calibration.get("per_check") or {}
        if per_check:
            L += ["", "Each check on its own (raw candidates, before merging and thresholds):", "",
                  "| check | category | precision | recall |", "|---|---|---|---|"]
            for check, cats in per_check.items():
                for c, m in cats.items():
                    L.append(f"| {check} | {c} | {m['precision']} | {m['recall']} |")

    if diff:
        L += ["", "## Against baseline", "", f"**{diff['regressed_items']} items regressed, {diff['improved_items']} improved.**"]
        worse = {c: d for c, d in diff["category_delta"].items() if d > 0}
        if worse:
            L.append("New problems by category: " + ", ".join(f"{c} +{d}" for c, d in sorted(worse.items(), key=lambda x: -x[1])) + ".")
        L.append("Examples in diff.md.")

    by_cat = defaultdict(list)
    for r in records:
        for f in r["flags"]:
            by_cat[f["category"]].append((r, f))
    if by_cat:
        L += ["", "## Findings by category", "", "What to look at first: count, languages affected, and two examples each.", "",
              "| category | flags | languages | examples |", "|---|---|---|---|"]
        for c in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c)):
            rows = sorted(by_cat[c], key=lambda rf: (-MQM_WEIGHTS[rf[1]["severity"]], -rf[1]["score"], rf[0]["lang"], rf[0]["id"]))
            langs = ", ".join(f"{lang} {n}" for lang, n in Counter(r["lang"] for r, _ in rows).most_common())
            examples = "<br>".join(f"`{r['id']}`: {_evidence_line(f).replace('|', '/')[:140]}" for r, f in rows[:2])
            L.append(f"| {c} | {len(rows)} | {langs} | {examples} |")

    top = sorted(((r, f) for r in records for f in r["flags"]),
                 key=lambda rf: (-MQM_WEIGHTS[rf[1]["severity"]], -rf[1]["score"], rf[0]["lang"], rf[0]["id"]))[:15]
    if top:
        L += ["", "## Top failures", "", "| item | lang | category | severity | where | evidence |", "|---|---|---|---|---|---|"]
        for r, f in top:
            where = _snippet(r["translation"], f["span"]).replace("|", "\\|")
            L.append(f"| {r['id']} | {r['lang']} | {f['category']} | {f['severity']} | {where} | {_evidence_line(f).replace('|', '/')} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def compare(records: list[dict], baseline_dir: Path) -> dict:
    base = {}
    for line in (baseline_dir / "items.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            base[(r["id"], r["lang"])] = r
    cur = {(r["id"], r["lang"]): r for r in records}
    common = sorted(set(base) & set(cur))
    regressed, improved = [], []
    cat_delta = Counter()
    for key in common:
        b = {f["category"] for f in base[key]["flags"]}
        c = {f["category"] for f in cur[key]["flags"]}
        for cat in c - b:
            cat_delta[cat] += 1
        for cat in b - c:
            cat_delta[cat] -= 1
        if c - b:
            regressed.append((key, sorted(c - b), cur[key]))
        if b - c:
            improved.append((key, sorted(b - c), base[key]))
    lang_rates = {}
    for lang in sorted({k[1] for k in common}):
        keys = [k for k in common if k[1] == lang]
        lang_rates[lang] = {
            "baseline_flagged": sum(1 for k in keys if base[k]["flags"]),
            "current_flagged": sum(1 for k in keys if cur[k]["flags"]),
            "items": len(keys),
        }
    return {
        "common_items": len(common),
        "only_in_baseline": len(set(base) - set(cur)),
        "only_in_current": len(set(cur) - set(base)),
        "regressed_items": len(regressed),
        "improved_items": len(improved),
        "category_delta": dict(sorted(cat_delta.items())),
        "per_language": lang_rates,
        "regressed": regressed,
        "improved": improved,
    }


def write_diff_md(path: Path, diff: dict) -> None:
    L = ["# Diff against baseline", "",
         f"{diff['common_items']} items in both runs ({diff['only_in_baseline']} only in baseline, {diff['only_in_current']} only in this run).",
         f"**{diff['regressed_items']} regressed**, **{diff['improved_items']} improved**.", "",
         "| lang | items | flagged (baseline) | flagged (now) | change | MQM (baseline → now) |", "|---|---|---|---|---|---|"]
    for lang, m in diff["per_language"].items():
        mqm = f"{m.get('baseline_mqm')} → {m.get('current_mqm')}" if m.get("baseline_mqm") is not None else "–"
        L.append(f"| {lang} | {m['items']} | {m['baseline_flagged']} | {m['current_flagged']} | {m['current_flagged'] - m['baseline_flagged']:+d} | {mqm} |")
    if diff["category_delta"]:
        L += ["", "| category | change in flagged items |", "|---|---|"]
        L += [f"| {c} | {d:+d} |" for c, d in diff["category_delta"].items() if d]
    for title, rows in (("Regressions (new problems)", diff["regressed"]), ("Improvements (problems gone)", diff["improved"])):
        L += ["", f"## {title}", ""]
        if not rows:
            L.append("None.")
            continue
        L += ["| item | lang | categories | example evidence |", "|---|---|---|---|"]
        for (item_id, lang), cats, rec in rows[:25]:
            flag = next((f for f in rec["flags"] if f["category"] in cats), None)
            ev = _evidence_line(flag).replace("|", "/") if flag else ""
            L.append(f"| {item_id} | {lang} | {', '.join(cats)} | {ev} |")
        if len(rows) > 25:
            L.append(f"\n…and {len(rows) - 25} more (see items.jsonl).")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def write_review_queue(path: Path, states, thresholds: dict[str, float], margin: float = 0.15) -> int:
    """Clusters close to their threshold, for human labelling; answers feed back via --calibration."""
    rows = []
    for st in states:
        for c in st.metrics.get("clusters", []):
            t = thresholds.get(c["category"], 0.5)
            if abs(c["score"] - t) > margin:
                continue
            text = st.item.get("translation") or ""
            rows.append({"id": st.item["id"], "lang": st.item["lang"], "category": c["category"], "score": c["score"], "threshold": t,
                         "flagged": c["score"] >= t, "flagged_text": _snippet(text, c["span"], 80), "source": st.item["source"],
                         "translation": text, "span": json.dumps(c["span"]), "source_span": json.dumps(c["source_span"]),
                         "evidence": _evidence_line(c), "human_label (error|clean)": ""})
    rows.sort(key=lambda r: (abs(r["score"] - r["threshold"]), r["lang"], r["id"]))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def write_items(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
