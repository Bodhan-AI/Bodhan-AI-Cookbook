"""Make a local HTML labelling sheet for hand labels ("annotator": "candidate").

    python scripts/make_label_sheet.py outputs/demo_eval outputs/demo_inj/injected.jsonl \
        --n 30 --out outputs/labeling/label_sheet.html

Picks untouched items (label_scope assumed_clean) from a run: every flagged one first, then
unflagged ones, balanced across languages, deterministically. Open the HTML file in a
browser, review each item, press "Export labels" and pass the downloaded file to
`bodhan-eval --calibration human_labels.jsonl`. Progress is kept in the browser
(localStorage) so labelling can be paused.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from collections import defaultdict
from pathlib import Path

CATS = [
    "omission", "addition", "mistranslation", "number", "term", "format", "wrong_language",
    "speech_omission", "speech_repetition", "speech_garbled",
]  # fmt: skip


def pick(rows: list[dict], scope: dict[str, str], n: int, min_conf: float) -> list[dict]:
    pool = [r for r in rows if scope.get(r["id"]) == "assumed_clean" and r.get("translation")]
    key = lambda r: hashlib.sha256(r["id"].encode()).hexdigest()  # noqa: E731
    flagged = sorted((r for r in pool if shown(r, min_conf)), key=key)
    clean = sorted((r for r in pool if not shown(r, min_conf)), key=key)
    by_lang: dict[str, list] = defaultdict(list)
    for r in clean:
        by_lang[r["lang"]].append(r)
    out = flagged[:n]
    langs = sorted(by_lang)
    while len(out) < n and any(by_lang.values()):
        for lg in langs:
            if by_lang[lg] and len(out) < n:
                out.append(by_lang[lg].pop(0))
    return sorted(out, key=lambda r: r["id"])


def shown(r: dict, min_conf: float) -> list[dict]:
    """Candidates worth reviewing: from `min_conf` up, so flags that calibration hid (or
    would hide) are judged by a person too."""
    return [f for f in r.get("candidates", r["flags"]) if f["confidence"] >= min_conf]


def slim(r: dict, min_conf: float) -> dict:
    return {
        "id": r["id"],
        "lang": r["lang"],
        "source": r["source"],
        "translation": r["translation"],
        "audio": Path(r["audio"]).resolve().as_uri() if r.get("audio") else None,
        "flags": [
            {
                "category": f["category"],
                "severity": f["severity"],
                "span": f["span"],
                "confidence": f["confidence"],
                "evidence": f["evidence"],
                "checks": f.get("details", {}).get("checks", []),
            }
            for f in shown(r, min_conf)
        ],
    }


TEMPLATE = Path(__file__).with_name("label_sheet_template.html")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path, help="run output dir (has items.jsonl)")
    ap.add_argument("dataset", type=Path, help="the dataset that run used (for label_scope)")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--min-conf", type=float, default=0.45, help="show candidates from here up")
    ap.add_argument("--out", type=Path, default=Path("outputs/labeling/label_sheet.html"))
    args = ap.parse_args()
    rows = [json.loads(x) for x in (args.run / "items.jsonl").read_text("utf-8").splitlines() if x]
    scope = {}
    for line in args.dataset.read_text("utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            scope[d["id"]] = d.get("label_scope") or ("complete" if "labels" in d else "")
    items = [slim(r, args.min_conf) for r in pick(rows, scope, args.n, args.min_conf)]
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    key = hashlib.sha256(data.encode()).hexdigest()[:12]
    page = TEMPLATE.read_text("utf-8")
    for name, value in (
        ("__DATA__", data),
        ("__CATS__", json.dumps(CATS)),
        ("__KEY__", json.dumps(key)),
    ):
        page = page.replace(name, value)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, "utf-8")
    flagged = sum(bool(i["flags"]) for i in items)
    print(f"{len(items)} items ({flagged} flagged, {len(items) - flagged} unflagged) -> {args.out}",
          file=sys.stderr)  # fmt: skip
    print(html.escape(str(args.out.resolve())), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
