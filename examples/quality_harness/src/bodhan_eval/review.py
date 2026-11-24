"""Review queue: export a report's items as a CSV for a human, and turn the answers into labels.

    python -m bodhan_eval.review export --report ./report --lang hi --out review_hi.csv
    python -m bodhan_eval.review import review_hi.csv --out data/labels.jsonl

Export: every item in the language with a candidate, plus a seeded random sample of items with none
(to measure what the harness misses). Each item gets one `item` row (texts, and room for errors the
harness missed) followed by one row per candidate.

Import: one line per reviewed item, {"id", "lang", "labels": [...], "unsure": [...]}. Labels carry
`annotator: "candidate-manual"` (a human, shown the harness's candidates). An item with no labels was
reviewed and found clean. A candidate marked false alarm is any candidate not matching a label.
Re-importing replaces earlier answers for the same items and keeps the rest of the file.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

CATEGORIES = ("omission", "addition", "mistranslation", "number", "term", "format", "wrong_language", "fluency", "other")
SEVERITIES = ("critical", "major", "minor")
VERDICTS = ("real", "false", "unsure")
COLUMNS = ("id", "lang", "row", "check", "category", "text", "why", "verdict", "severity", "note",
           "source", "translation", "back_translation")
GUIDE = [
    "# How to review, with examples: docs/review_guide.md in the harness repository.",
    "# Candidate rows: verdict = real | false | unsure. If real: severity, and fix category if it is wrong. note is optional.",
    "# An error the harness did NOT raise: insert a row under the item with the same id and lang, row = missed, and fill category + severity + note. One error per row.",
    "# Severity: critical = meaning reversed or dangerous / unusable; major = a fact, number, name or part of the meaning wrong or missing; minor = small, meaning intact (style, a dropped heading mark).",
    f"# Categories: {', '.join(CATEGORIES)}",
]


def _short(text, n=60):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def why(c: dict) -> str:
    """One line a reviewer can read instead of the evidence JSON."""
    e, check = c["evidence"], c["check"]
    if check == "format":
        return f"{e.get('kind')}: source has {e.get('expected')}, translation has {e.get('found')}"
    if check == "numbers":
        kind = e.get("kind")
        if kind == "changed":
            return f"source says {e['source']}, translation says {e['translation']}"
        if kind == "unit_missing":
            return f"unit {e['unit']} not found in translation or back-translation"
        return f"number {kind} in translation" + (f" ({e.get('translation') or e.get('source')})" if kind != "missing" else "")
    if check == "entities":
        return f"name/term {e.get('entity')!r} not found in the translation or back-translation"
    if check == "back_translation_phrase":
        has = e.get("back_translation_has")
        return f"back-translation lacks {e.get('missing_words')!r}" + (f", has {has!r}" if has else "")
    if check == "script":
        return f"text in {e.get('found')} script where {'/'.join(e.get('expected') or [])} was expected" \
            if e.get("kind") == "other_script" else f"{e.get('kind')}: {_short(e.get('text'))}"
    if check == "judge":
        return f"LLM judge ({e.get('confidence') or 'no'} confidence): {e.get('explanation')}"
    if check == "back_translation_negation":
        return f"negation count differs: back-translation says {e.get('back_translation')!r} for {e.get('source_text')!r}"
    if check == "length":
        return f"translation {e.get('ratio')}x the source's length, typical {e.get('median_ratio')}x"
    if check == "back_translation":
        changes = [f"{x['source_words']!r} → {x['back_translation_has']!r}" if x.get("back_translation_has")
                   else f"{x['source_words']!r} dropped" for x in e.get("changes", [])]
        return f"{c['category']}: " + ("; ".join(changes) or "sentence not found in back-translation")
    return json.dumps(e, ensure_ascii=False)[:200]


def export(report: Path, lang: str, n_random: int, out: Path, seed: int = 0) -> int:
    records = [json.loads(line) for line in (report / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    records = [r for r in records if r["lang"] == lang and r.get("translation") is not None]
    with_cands = [r for r in records if r["candidates"]]
    without = [r for r in records if not r["candidates"]]
    sample = random.Random(seed).sample(without, min(n_random, len(without)))
    chosen = sorted(with_cands + sample, key=lambda r: records.index(r))
    with out.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel otherwise garbles Indic text
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for line in GUIDE:
            w.writerow([line])
        for r in chosen:
            w.writerow([r["id"], lang, "item", "", "", "", "", "", "", "", r["source"], r["translation"], r.get("back_translation") or ""])
            for i, c in enumerate(r["candidates"]):
                w.writerow([r["id"], lang, i, c["check"], c["category"], c["text"], why(c), "", "", ""])
    return len(chosen)


def read_answers(path: Path, report: Path) -> dict[tuple[str, str], dict]:
    # Numbered as the spreadsheet shows them (header = row 1), so error messages point at the right row.
    rows = [(n, row) for n, row in enumerate(csv.reader(path.open(encoding="utf-8-sig", newline="")), 1)
            if any(cell.strip() for cell in row) and not row[0].startswith("#")]
    header = [h.strip() for h in rows[0][1]]
    items: dict[tuple[str, str], dict] = {}
    candidates = _candidates(report)
    problems = []
    for n, raw in rows[1:]:
        row = {h: (raw[i].strip() if i < len(raw) else "") for i, h in enumerate(header)}
        key = (row["id"], row["lang"])
        entry = items.setdefault(key, {"id": key[0], "lang": key[1], "labels": [], "unsure": []})
        verdict, category, severity = row["verdict"].lower(), row["category"].lower(), row["severity"].lower()
        if row["row"] in ("item", "missed"):
            if not (category or severity or row["note"]):
                continue
            if category not in CATEGORIES or severity not in SEVERITIES:
                problems.append(f"row {n} ({key[0]}): a missed error needs a category and a severity")
                continue
            entry["labels"].append({"type": category, "severity": severity, "note": row["note"], "annotator": "candidate-manual"})
            continue
        if verdict not in VERDICTS:
            problems.append(f"row {n} ({key[0]}): verdict must be one of {', '.join(VERDICTS)}, got {row['verdict']!r}")
            continue
        try:
            cand = candidates[key][int(row["row"])]
        except (KeyError, IndexError, ValueError):
            problems.append(f"row {n} ({key[0]}): no candidate {row['row']!r} for this item in the report")
            continue
        ref = {"candidate": int(row["row"]), "check": cand["check"], "category": cand["category"], "side": cand["side"], "span": cand["span"]}
        if verdict == "unsure":
            entry["unsure"].append(ref)
        elif verdict == "real":
            if category not in CATEGORIES or severity not in SEVERITIES:
                problems.append(f"row {n} ({key[0]}): a real error needs a category and a severity")
                continue
            label = {"type": category, "severity": severity, "note": row["note"], "annotator": "candidate-manual", "from": ref}
            label.update(side=ref["side"], span=ref["span"])
            entry["labels"].append(label)
    if problems:
        raise SystemExit("fix these rows and import again:\n  " + "\n  ".join(problems))
    return items


def _candidates(report: Path) -> dict[tuple[str, str], list[dict]]:
    out = {}
    for line in (report / "items.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[(r["id"], r["lang"])] = r["candidates"]
    return out


def import_answers(path: Path, out: Path, report: Path) -> dict:
    answers = read_answers(path, report)
    kept = []
    if out.is_file():
        kept = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
        kept = [k for k in kept if (k["id"], k["lang"]) not in answers]
    with out.open("w", encoding="utf-8") as f:
        for entry in kept + list(answers.values()):
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    labels = [lab for a in answers.values() for lab in a["labels"]]
    return {"items": len(answers), "clean": sum(1 for a in answers.values() if not a["labels"]),
            "real_from_candidates": sum(1 for lab in labels if "from" in lab),
            "missed": sum(1 for lab in labels if "from" not in lab),
            "unsure": sum(len(a["unsure"]) for a in answers.values())}


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.review")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--report", type=Path, required=True)
    e.add_argument("--lang", required=True)
    e.add_argument("--random", type=int, default=10, help="items with no candidates to add, for measuring misses")
    e.add_argument("--out", type=Path, required=True)
    i = sub.add_parser("import")
    i.add_argument("csv", type=Path)
    i.add_argument("--out", type=Path, required=True)
    i.add_argument("--report", type=Path, required=True, help="the report the sheet was exported from")
    args = p.parse_args(argv)
    if args.cmd == "export":
        print(f"wrote {export(args.report, args.lang, args.random, args.out)} items to {args.out}")
    else:
        print(json.dumps(import_answers(args.csv, args.out, args.report)))


if __name__ == "__main__":
    main()
