"""Score a run against hand-labelled answers: language, sentiment and themes."""

from __future__ import annotations

import csv
import json
from pathlib import Path


def read_expected(path: str | Path) -> dict[str, dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["id"].strip(): {
                "language": row["language"].strip(),
                "sentiment": row["sentiment"].strip(),
                "themes": {theme for theme in row.get("themes", "").split("|") if theme.strip()},
            }
            for row in csv.DictReader(handle)
        }


def evaluate(records: list[dict], expected: dict[str, dict]) -> dict:
    scored = [record for record in records if record["id"] in expected]
    language_hits = sentiment_hits = true_pos = false_pos = false_neg = failed = 0
    misses: list[dict] = []
    for record in scored:
        gold = expected[record["id"]]
        if record["error"]:
            failed += 1
            false_neg += len(gold["themes"])
            misses.append({"id": record["id"], "problem": f"error: {record['error']}"})
            continue
        themes = set(record["themes"])
        language_ok = record["language"] == gold["language"]
        sentiment_ok = record["sentiment"] == gold["sentiment"]
        language_hits += language_ok
        sentiment_hits += sentiment_ok
        true_pos += len(themes & gold["themes"])
        false_pos += len(themes - gold["themes"])
        false_neg += len(gold["themes"] - themes)
        problems = []
        if not language_ok:
            problems.append(f"language {record['language']} (expected {gold['language']})")
        if not sentiment_ok:
            problems.append(f"sentiment {record['sentiment']} (expected {gold['sentiment']})")
        if themes != gold["themes"]:
            problems.append(f"themes {sorted(themes)} (expected {sorted(gold['themes'])})")
        if problems:
            misses.append({"id": record["id"], "problem": "; ".join(problems), "english": record["english"]})
    total = len(scored)
    precision = true_pos / (true_pos + false_pos) if true_pos + false_pos else 0.0
    recall = true_pos / (true_pos + false_neg) if true_pos + false_neg else 0.0
    return {
        "items": total,
        "failed": failed,
        "language_accuracy": round(language_hits / total, 3) if total else 0.0,
        "sentiment_accuracy": round(sentiment_hits / total, 3) if total else 0.0,
        "theme_precision": round(precision, 3),
        "theme_recall": round(recall, 3),
        "theme_f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
        "misses": misses,
    }


def format_scores(scores: dict) -> str:
    lines = [
        f"items scored        {scores['items']} ({scores['failed']} failed)",
        f"language accuracy   {scores['language_accuracy']:.1%}",
        f"sentiment accuracy  {scores['sentiment_accuracy']:.1%}",
        f"theme precision     {scores['theme_precision']:.1%}",
        f"theme recall        {scores['theme_recall']:.1%}",
        f"theme F1            {scores['theme_f1']:.1%}",
    ]
    for miss in scores["misses"]:
        lines.append(f"  miss {miss['id']}: {miss['problem']}")
        if miss.get("english"):
            lines.append(f"       english: {miss['english']}")
    return "\n".join(lines)


def load_records(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["items"]
