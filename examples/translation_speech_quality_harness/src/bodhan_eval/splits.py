"""Split the labelled demo data (DATASET_PLAN step 5).

    python -m bodhan_eval.splits --gold data/demo_gold.jsonl [--heldout-gold data/heldout_gold.jsonl] \\
        [--extra-labels data/saksham_labels.jsonl] --out-dir data [--seed 7]

Writes into ``--out-dir``:
  demo.jsonl         every gold item without its labels: what the harness sees, like the hidden set
  demo_labels.jsonl  id + lang + labels of the calibration subset (pass it as --calibration; the scorecard
                     leaves it out with --exclude, so the scores are measured on items the run did not tune on)
  bakeoff.jsonl      labelled items for the judge bake-off: no speech labels (the judge reads text only)
  splits.json        what went where
  demo_gold_reviewed.jsonl  with ``--known-errors``: the gold plus the errors the model made itself (score with it)
Both subsets are half clean. Error items are taken round-robin over categories, clean items one at a time,
each time from the language least represented so far, in a seeded order: every category and language
appears. Items labelled by ``--extra-labels`` (a human review of real outputs) all go into the bake-off, and
count toward ``--bakeoff-size``: the injected items that fill the rest keep the whole set about half clean
and start with the categories and languages the review covered least. Input files are never rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .cli import _positive_int, setup_logging

log = logging.getLogger("bodhan_eval.splits")

SPEECH = {"speech_omission", "speech_repetition", "speech_garbled"}


def _read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def _key(r: dict) -> tuple[str, str]:
    return str(r["id"]), str(r["lang"])


def _rank(seed: int, r: dict) -> int:
    return int(hashlib.sha256(f"{seed}|{r['id']}::{r['lang']}".encode()).hexdigest()[:12], 16)


def _category(r: dict) -> str:
    return str(r["labels"][0]["type"]) if r["labels"] else "clean"


def _balanced(rows: list[dict], n: int, seed: int, groups: Counter[str], langs: Counter[str]) -> list[dict]:
    """Up to n rows, each from the group (category) least represented so far, ties by name; within it the
    language least represented so far. With empty counters this is a round-robin over the groups."""
    pool: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: _rank(seed, r)):
        pool.setdefault(_category(r), []).append(r)
    out: list[dict] = []
    while len(out) < n and any(pool.values()):
        g = min((g for g in pool if pool[g]), key=lambda g: (groups[g], g))
        r = min(pool[g], key=lambda x: langs[x["lang"]])  # min keeps the seeded order among ties
        pool[g].remove(r)
        out.append(r)
        groups[g] += 1
        langs[r["lang"]] += 1
    return out


def half_clean(rows: list[dict], n: int, seed: int, have: list[dict] | None = None) -> list[dict]:
    """n rows from ``rows`` so that together with ``have`` (rows already chosen) about half are clean, and
    categories and languages ``have`` covers least come first."""
    have = have or []
    clean = [r for r in rows if not r["labels"]]
    errors = [r for r in rows if r["labels"]]
    groups, langs = Counter(_category(r) for r in have), Counter(r["lang"] for r in have)
    want_clean = max(0, min((len(have) + n) // 2 - groups["clean"], len(clean), n))
    picked = _balanced(errors, n - want_clean, seed, groups, langs)
    return picked + _balanced(clean, n - len(picked), seed, groups, langs)


def _audio_to(row: dict, src_dir: Path, out_dir: Path) -> dict:
    audio = row.get("audio")
    if isinstance(audio, str) and audio and not os.path.isabs(audio):
        row = {**row, "audio": Path(os.path.relpath((src_dir / audio).resolve(), out_dir.resolve())).as_posix()}
    return row


def _relocate(v: dict, translation: str) -> list[int] | None:
    """Where the reviewed text is in this translation: the occurrence nearest its old position (an injected
    edit before it shifts it), or None if the edit replaced it."""
    text = str(v.get("text") or "")
    starts = [m.start() for m in re.finditer(re.escape(text), translation)] if text else []
    if not starts:
        return None
    old = (v.get("span") or [0])[0]
    a = min(starts, key=lambda s: abs(s - old))
    return [a, a + len(text)]


def with_known_errors(gold: list[dict], review: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Add the errors a review of the generated outputs confirmed (verdict ``real``) to the gold labels.

    The injector treats every item it leaves alone as clean, but the model's own output has errors too; without
    them, every true detection of one counts as a false alarm. ``false_alarm`` verdicts change nothing; an item
    the gold calls clean whose only findings are ``debatable`` loses its labels (unsure: neither clean nor
    labelled, so it is not scored). Translation-side labels are found again by their text, since the injector
    may have edited the translation; a label whose text the edit replaced is dropped and counted.
    """
    by_key: dict[tuple[str, str], list[dict]] = {}
    for v in review:
        by_key.setdefault(_key(v), []).append(v)
    stats = {"added": 0, "dropped": 0, "unsure_items": 0}
    out = []
    for r in gold:
        found = []
        for v in by_key.get(_key(r), []):
            if v["verdict"] != "real":
                continue
            lab = {"type": v["category"], "severity": v.get("severity") or "major", "annotator": v.get("reviewer")}
            if v.get("text"):
                span = _relocate(v, r.get("translation") or "")
                if span is None:
                    stats["dropped"] += 1
                    continue
                lab.update(span=span, span_on="translation")
            else:
                lab.update(span=v.get("source_span"), span_on="source")
            found.append(lab)
        stats["added"] += len(found)
        if found:
            r = {**r, "labels": (r.get("labels") or []) + found}
        elif r.get("labels") == [] and any(v["verdict"] == "debatable" for v in by_key.get(_key(r), [])):
            r = {k: x for k, x in r.items() if k != "labels"}
            stats["unsure_items"] += 1
        out.append(r)
    return out, stats


def split(args: argparse.Namespace) -> dict[str, Any]:
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    gold = [_audio_to(r, args.gold.parent, out_dir) for r in _read(args.gold)]
    held = [_audio_to(r, args.heldout_gold.parent, out_dir) for r in _read(args.heldout_gold)] if args.heldout_gold else []
    extra: dict[tuple[str, str], list] = {}
    for path in args.extra_labels or []:
        extra.update({_key(r): r["labels"] for r in _read(path) if r.get("labels") is not None})

    _write(out_dir / "demo.jsonl", [{k: v for k, v in r.items() if k != "labels"} for r in gold])
    known: dict[str, int] | None = None
    if args.known_errors:
        gold, known = with_known_errors(gold, _read(args.known_errors))
        _write(out_dir / "demo_gold_reviewed.jsonl", gold)
    labelled = [r for r in gold if r.get("labels") is not None and _key(r) not in extra]
    calibration = half_clean(labelled, args.calibration_size, args.seed)
    _write(out_dir / "demo_labels.jsonl", [{"id": r["id"], "lang": r["lang"], "labels": r["labels"]} for r in calibration])

    def textual(rows: list[dict]) -> list[dict]:
        return [r for r in rows if r.get("labels") is not None and not any(lab["type"] in SPEECH for lab in r["labels"])]

    reviewed = textual([{**r, "labels": extra[_key(r)]} for r in gold + held if _key(r) in extra])
    main_pool = [r for r in textual(gold) if _key(r) not in extra]
    held_pool = [r for r in textual(held) if r["lang"] == args.bakeoff_heldout_lang and _key(r) not in extra]
    bakeoff = reviewed + half_clean(main_pool, max(0, args.bakeoff_size - len(reviewed)), args.seed, reviewed)
    bakeoff += half_clean(held_pool, args.bakeoff_heldout, args.seed)
    _write(out_dir / "bakeoff.jsonl", bakeoff)

    def counts(rows: list[dict]) -> dict[str, Any]:
        return {
            "items": len(rows),
            "clean": sum(1 for r in rows if r.get("labels") == []),
            "unlabelled": sum(1 for r in rows if r.get("labels") is None),  # e.g. on a review sheet, not yet judged
            "per_language": dict(sorted(Counter(r["lang"] for r in rows).items())),
            "per_category": dict(sorted(Counter(lab["type"] for r in rows for lab in r.get("labels") or []).items())),
        }

    summary = {
        "seed": args.seed,
        "demo": counts(gold),
        "calibration": counts(calibration),
        "bakeoff": {**counts(bakeoff), "reviewed": len(reviewed)},
    }
    if known is not None:
        summary["known_errors"] = known
    (out_dir / "splits.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bodhan_eval.splits", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--gold", type=Path, required=True, help="labelled demo items (the injector's output)")
    ap.add_argument("--heldout-gold", type=Path, help="labelled items in held-out languages")
    ap.add_argument("--extra-labels", type=Path, action="append", help="id + lang + labels lines (e.g. a review)")
    ap.add_argument("--known-errors", type=Path, help="review verdicts on the generated outputs' flags (D45)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--calibration-size", type=_positive_int, default=40)
    ap.add_argument("--bakeoff-size", type=_positive_int, default=40, help="items from the main languages")
    ap.add_argument("--bakeoff-heldout", type=int, default=10, help="items from --bakeoff-heldout-lang")
    ap.add_argument("--bakeoff-heldout-lang", default="te")
    ap.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    args = ap.parse_args(argv)
    setup_logging(args.log_level)
    try:
        summary = split(args)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        log.error("split failed: %s: %s", type(e).__name__, e)
        return 1
    log.info("demo %d items; calibration %d; bake-off %d", summary["demo"]["items"],
             summary["calibration"]["items"], summary["bakeoff"]["items"])  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
