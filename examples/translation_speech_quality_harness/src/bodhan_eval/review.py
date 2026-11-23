"""Human review sheet: export items to a CSV a person fills in, import the answers as labels.

    python -m bodhan_eval.review export --report outputs/clean --dataset outputs/clean/generated.jsonl \\
        --out outputs/clean/review.csv [--per-lang 10] [--seed 7]
    python -m bodhan_eval.review import --csv review.csv --dataset outputs/clean/generated.jsonl \\
        --out data/saksham_labels.jsonl

Export picks ``--per-lang`` items per language: first the items the run flagged, then the ones in its review
queue, then (at least a third of the sheet) a seeded random sample of items it did not suspect, because those
show what the harness misses. Each row has the English, the translation, the back-translation (Indic-Translate,
sentence by sentence as the run did, so it comes from the cache), the audio path, what the harness found, and
empty columns for the reviewer. The file is UTF-8 with a BOM so spreadsheet programs show Indian scripts.

Reviewer columns: ``your_verdict`` = ``ok``, or an error category (omission, addition, mistranslation, number,
term, format, wrong_language, speech_omission, speech_repetition, speech_garbled, or any alias in
``taxonomy.json``); ``your_severity`` = minor / major / critical (default major); ``your_error_text`` = the
wrong words copied from the translation (they become the label's span). For a second error in one item, copy
the row. Import writes ``id`` + ``lang`` + ``labels`` lines with ``"annotator": "saksham"``, which the main
command reads with ``--calibration``, plus ``<out>.import.json`` listing what it could not use.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import report
from .budget import Budget, BudgetExceeded
from .cache import Cache
from .checks.roundtrip import translatable_segments
from .cli import _non_negative, _positive_int, setup_logging
from .clients import BodhanClient, BodhanError, ServiceUnavailable
from .config import Settings, canonical_category, canonical_severity
from .dataset import load_dataset
from .models import Item

log = logging.getLogger("bodhan_eval.review")

COLUMNS = [
    "id",
    "lang",
    "source",
    "translation",
    "back_translation",
    "audio",
    "harness_flags",
    "review_queue",
    "suggested",
    "your_verdict",
    "your_severity",
    "your_error_text",
    "notes",
]
CLEAN_VERDICTS = {"ok", "clean", "none", "no error", "no_error", "correct"}


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _describe(flags: list[dict]) -> str:
    parts = []
    for f in flags:
        detail = (f.get("evidence") or [{}])[0].get("detail", "")
        text = f": {f['text']!r}" if f.get("text") else ""
        parts.append(f"{f['category']} ({f.get('severity', 'major')}){text} — {detail}".strip(" —"))
    return " | ".join(parts)


def _rank(seed: int, key: str) -> int:
    return int(hashlib.sha256(f"{seed}|{key}".encode()).hexdigest()[:12], 16)


def choose(items: list[Item], records: dict[str, dict], per_lang: int, seed: int) -> list[Item]:
    """Per language: suspected items first (flags, then review queue), then a sample of the rest.

    Every group is in a seeded random order, so a short sheet is not just the first ids (one subject).
    """
    chosen: list[Item] = []
    for lang in sorted({it.lang for it in items}):
        mine = [it for it in items if it.lang == lang and it.translation]
        rec = {it.key: records.get(it.key, {}) for it in mine}
        flagged = sorted((it for it in mine if rec[it.key].get("flags")), key=lambda i: _rank(seed, i.key))
        queued = sorted(
            (it for it in mine if not rec[it.key].get("flags") and rec[it.key].get("uncertain")),
            key=lambda i: _rank(seed, i.key),
        )
        rest = sorted((it for it in mine if it not in flagged and it not in queued), key=lambda i: _rank(seed, i.key))
        random_share = max(1, per_lang // 3) if per_lang >= 2 else 0
        suspected = (flagged + queued)[: per_lang - min(random_share, len(rest))]
        chosen += suspected + rest[: per_lang - len(suspected)]
    return chosen


def _back_translate(bodhan: BodhanClient | None, it: Item) -> str:
    if bodhan is None:
        return ""
    text = it.translation or ""
    try:
        return " ".join(bodhan.translate(text[a:b], "en")["translation"] for a, b in translatable_segments(text))
    except (BodhanError, ServiceUnavailable, BudgetExceeded) as e:
        return f"(unavailable: {type(e).__name__})"


def _rel(path: str | None, base: Path) -> str:
    if not path:
        return ""
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return Path(path).as_posix()


def export(args: argparse.Namespace) -> int:
    records = {
        f"{r['id']}::{r['lang']}": r
        for r in (
            json.loads(x) for x in (args.report / "items.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
        )
    }
    items, _ = load_dataset(args.dataset, None)
    chosen = choose(items, records, args.per_lang, args.seed)
    cache, bodhan = None, None
    if not args.no_back_translation:
        cache = Cache(args.cache_dir)
        bodhan = BodhanClient(Settings.from_env(), cache, Budget(args.max_cost_inr))
        if not bodhan.available("translate"):
            log.warning("no Indic-Translate key: the back_translation column stays empty")
            bodhan = None
    base = args.out.resolve().parent
    rows = []
    try:
        for it in chosen:
            rec = records.get(it.key, {})
            flags, queue = rec.get("flags", []), rec.get("uncertain", [])
            suggested = flags[0]["category"] if flags else (f"{queue[0]['category']}?" if queue else "ok")
            rows.append(
                {
                    "id": it.id,
                    "lang": it.lang,
                    "source": it.source,
                    "translation": it.translation or "",
                    "back_translation": _back_translate(bodhan, it),
                    "audio": _rel(it.audio, base),
                    "harness_flags": _describe(flags),
                    "review_queue": _describe(queue),
                    "suggested": suggested,
                    **{c: "" for c in COLUMNS if c.startswith("your_") or c == "notes"},
                }
            )
    finally:
        if cache is not None:
            cache.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    log.info("%d items written to %s", len(rows), args.out)
    return 0


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def _find(text: str, needle: str) -> tuple[int, int] | None:
    """Where the reviewer's copied words are in the translation (exact, then ignoring spacing differences)."""
    needle = needle.strip()
    if not needle:
        return None
    k = text.find(needle)
    if k >= 0:
        return k, k + len(needle)
    pattern = r"\s+".join(re.escape(w) for w in needle.split())
    m = re.search(pattern, text)
    return (m.start(), m.end()) if m else None


def import_answers(args: argparse.Namespace) -> int:
    items, _ = load_dataset(args.dataset, None)
    by_key = {it.key: it for it in items}
    with args.csv.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    labels: dict[tuple[str, str], list[dict] | None] = {}
    summary: dict[str, Any] = {"rows": len(rows), "unknown_verdicts": [], "text_not_found": [], "unknown_items": []}
    for row in rows:
        verdict = (row.get("your_verdict") or "").strip()
        if not verdict:
            continue
        key = (row["id"], row["lang"])
        it = by_key.get(f"{row['id']}::{row['lang']}")
        if it is None:
            summary["unknown_items"].append({"id": row["id"], "lang": row["lang"]})
            continue
        if verdict.lower() in CLEAN_VERDICTS:
            labels.setdefault(key, [])
            continue
        category = canonical_category(verdict)
        if category is None:
            summary["unknown_verdicts"].append({"id": row["id"], "lang": row["lang"], "verdict": verdict})
            continue
        error_text = row.get("your_error_text") or ""
        span = _find(it.translation or "", error_text)
        if span is None and error_text.strip():
            summary["text_not_found"].append({"id": row["id"], "lang": row["lang"], "text": error_text.strip()})
        found = labels.get(key) or []  # an error overrides an "ok" row for the same item
        found.append(
            {
                "type": category,
                "severity": canonical_severity(row.get("your_severity")),
                "span": list(span) if span else None,
                "span_on": "translation",
                "annotator": "saksham",
            }
        )
        labels[key] = found
    out_rows = [{"id": i, "lang": lang, "labels": labs} for (i, lang), labs in labels.items()]
    report.write_jsonl(args.out, out_rows)
    summary.update(
        items=len(out_rows),
        clean=sum(1 for r in out_rows if not r["labels"]),
        labels=sum(len(r["labels"] or []) for r in out_rows),
    )
    report.write_json(args.out.with_suffix(".import.json"), summary)
    log.info(
        "%d items labelled (%d clean, %d labels); %d unknown verdicts, %d error texts not found",
        summary["items"],
        summary["clean"],
        summary["labels"],
        len(summary["unknown_verdicts"]),
        len(summary["text_not_found"]),
    )
    return 0


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m bodhan_eval.review", description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export", help="write the review sheet")
    ex.add_argument("--report", type=Path, required=True, help="the run's output folder (reads items.jsonl)")
    ex.add_argument("--dataset", type=Path, required=True, help="the dataset the run checked (e.g. generated.jsonl)")
    ex.add_argument("--out", type=Path, required=True, help="CSV to write")
    ex.add_argument("--per-lang", type=_positive_int, default=10, help="items per language (default 10)")
    ex.add_argument("--seed", type=int, default=7)
    ex.add_argument("--no-back-translation", action="store_true", help="leave the back_translation column empty")
    ex.add_argument("--cache-dir", type=Path, default=Path(".cache/bodhan_eval"))
    ex.add_argument("--max-cost-inr", type=_non_negative, default=1.0, help="cap for back-translations not in cache")
    im = sub.add_parser("import", help="turn the filled sheet into labels")
    im.add_argument("--csv", type=Path, required=True)
    im.add_argument("--dataset", type=Path, required=True, help="the same dataset (to locate the error text)")
    im.add_argument("--out", type=Path, required=True, help="labels JSONL for --calibration")
    for p in (ex, im):
        p.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    try:
        return export(args) if args.command == "export" else import_answers(args)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, csv.Error) as e:
        log.error("%s failed: %s: %s", args.command, type(e).__name__, e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
