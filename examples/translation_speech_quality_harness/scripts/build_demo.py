"""Build the demo dataset (run from the recipe folder).

    # 1. sources x languages -> harness input (no model calls)
    python scripts/build_demo.py prepare
    # 2. clean translations + audio with the harness itself (paid; cached)
    python -m bodhan_eval --dataset outputs/demo_build/clean_input.jsonl --langs hi,ta,bn \
        --out outputs/demo_build/clean_run --generate --max-cost-inr 5 --lifetime-cap-inr 8
    # 3. inject labelled errors -> sample_data/demo.jsonl + sample_data/audio/*.wav (tiny cost; cached)
    python scripts/build_demo.py inject
    # 4. optional blind review queue (for future human review; not used for calibration)
    python scripts/build_demo.py export-review

Step 3 is deterministic: rerunning it from the cache costs nothing and rebuilds the
same audio files, which are not in git.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

from bodhan_eval.audio import write_wav
from bodhan_eval.clients import BodhanClient, BodhanError
from bodhan_eval.config import load_settings
from bodhan_eval.infra import BudgetExceeded, Cache, CostLedger
from bodhan_eval.inject import SPEECH_CATEGORIES, TEXT_CATEGORIES, CleanItem, Pool, build_labelled_set, english_entities
from bodhan_eval.text import find_protected

SOURCES = [Path("sample_data/fleurs_sources.jsonl"), Path("sample_data/markdown_sources.jsonl")]
BUILD = Path("outputs/demo_build")
KNOWN_ISSUES = Path("sample_data/known_issues.jsonl")
# one error per item; about 8 per category keeps ~48% of the 138 demo items free of injected errors (DECISIONS D-22)
DEFAULT_TARGETS = ("format=8,number_unit=8,omission=8,addition=8,wrong_language_script=8,mistranslation=4,term_entity=4,"
                   "speech_omission_truncation=8,speech_repetition=8,speech_garbled=8")


def parse_targets(spec: str) -> dict[str, int]:
    out = {}
    for part in spec.split(","):
        cat, _, n = part.strip().partition("=")
        if cat not in TEXT_CATEGORIES + SPEECH_CATEGORIES:
            raise SystemExit(f"unknown category in --per-category: {cat!r}")
        out[cat] = int(n)
    return out


def locate(text: str, how: str) -> tuple[int, int] | None:
    """Span of a known pre-existing defect in the FINAL text. ``line:N`` = the N-th line,
    ``code_inline:N`` = the N-th inline code region."""
    kind, _, idx = how.partition(":")
    k = int(idx or 0)
    if kind == "line":
        starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
        if k >= len(starts):
            return None
        end = text.find("\n", starts[k])
        return starts[k], (len(text) if end == -1 else end)
    regions = [r for r in find_protected(text) if r.kind == kind]
    return (regions[k].start, regions[k].end) if k < len(regions) else None


def preexisting_labels(base_id: str, text: str, issues: list[dict]) -> list[dict]:
    """Real defects found in Bodhan's clean output by reviewing Tier-1 flags (see DECISIONS D-20)."""
    out = []
    for iss in issues:
        if iss["base"] != base_id:
            continue
        span = locate(text, iss["locate"])
        if span is None:
            raise SystemExit(f"known issue for {base_id} not found in the final text ({iss['locate']})")
        out.append({"type": iss["type"], "severity": iss["severity"], "span": list(span), "span_unit": "chars", "field": "translation",
                    "source": "reviewed_preexisting", "recipe": None, "note": iss["note"]})
    return out


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


# ---- prepare ------------------------------------------------------------------------


def cmd_prepare(a) -> int:
    rows = []
    for src_file in SOURCES:
        for s in read_jsonl(src_file):
            for lang in a.langs.split(","):
                row = {"id": f"{s['src_id']}-{lang}", "src_id": s["src_id"], "group": s["group"], "source": s["source"], "lang": lang}
                ref = s.get("references", {}).get(lang)
                if ref:
                    row["reference"] = ref
                rows.append(row)
    write_jsonl(a.out, rows)
    print(f"wrote {len(rows)} rows to {a.out}")
    return 0


# ---- inject -------------------------------------------------------------------------


async def _inject(a) -> int:
    inputs = {r["id"]: r for r in read_jsonl(a.clean_input)}
    run_items = read_jsonl(a.clean_run / "items.jsonl")
    clean: list[CleanItem] = []
    missing = 0
    for rec in run_items:
        row = inputs.get(rec["id"])
        gen = rec.get("generated", {})
        if row is None or not gen.get("translation"):
            missing += 1
            continue
        audio = str(a.clean_run / gen["audio"]) if gen.get("audio") else None
        clean.append(CleanItem(rec["id"], row["src_id"], row["lang"], row["source"], gen["translation"], row.get("reference"), audio))
    print(f"clean items: {len(clean)} ({sum(1 for c in clean if c.audio_path)} with audio); without a generated translation: {missing}")

    settings = load_settings(None)
    cache = Cache(a.cache_dir / "cache.sqlite")
    lifetime = sum(cache.lifetime_spend().values())
    ledger = CostLedger(
        settings.prices, max_run_inr=a.max_cost_inr, lifetime_cap_inr=a.lifetime_cap_inr, lifetime_spent_at_start=lifetime, spend_sink=cache.add_spend
    )
    client = BodhanClient(settings, cache, ledger, offline=a.offline)

    async def translate(text: str, lang: str) -> str:
        try:
            return (await client.translate(text, lang)).text
        except (BudgetExceeded, BodhanError) as exc:
            print(f"  translate skipped ({type(exc).__name__}): {exc}", file=sys.stderr)
            return ""

    try:
        langs = sorted({c.lang for c in clean})
        entities = sorted({e for c in clean for e in english_entities(c.source)})
        pairs = [(e, lang) for e in entities for lang in langs]
        outs = await asyncio.gather(*(translate(e, lang) for e, lang in pairs))
        pool = Pool(clean, {p: t for p, t in zip(pairs, outs, strict=True) if t.strip()})
        print(f"entity translations: {len(pool.entity_translations)} of {len(pairs)}")
        targets = parse_targets(a.per_category)
        built = await build_labelled_set(clean, pool, seed=a.seed, translate=translate, clean_fraction=a.clean_fraction, targets=targets)
    finally:
        await client.aclose()

    order = sorted(built, key=lambda b: b.base.id)
    random.Random(f"{a.seed}:ids").shuffle(order)
    a.audio_dir.mkdir(parents=True, exist_ok=True)
    issues = read_jsonl(a.known_issues) if a.known_issues and a.known_issues.exists() else []
    rows = []
    for k, b in enumerate(order, start=1):
        rid = f"d{k:03d}"
        labels = sorted(b.labels + preexisting_labels(b.base.id, b.translation, issues), key=lambda x: (x["span"], x["type"]))
        row = {
            "id": rid,
            "lang": b.base.lang,
            "source": b.base.source,
            "translation": b.translation,
            "labels": labels,
            "meta": {"base": b.base.id, "recipe": b.recipe},
        }
        if b.base.reference:
            row["reference"] = b.base.reference
        if b.base.audio_path:
            wav = a.audio_dir / f"{rid}.wav"
            wav.write_bytes(write_wav(b.samples, b.sr) if b.samples is not None else Path(b.base.audio_path).read_bytes())
            row["audio"] = Path(os.path.relpath(wav, a.out.parent)).as_posix()
        rows.append(row)
    write_jsonl(a.out, rows)

    cats = Counter(lab["type"] for r in rows for lab in r["labels"] if lab["source"] == "injector")
    n_strict = sum(1 for r in rows if not any(lab["source"] == "injector" for lab in r["labels"]))
    n_clean = sum(1 for r in rows if not r["labels"])
    print(
        f"wrote {len(rows)} items to {a.out} ({a.out.stat().st_size / 1024:.0f} KB): "
        f"{n_strict} without injected errors ({n_strict / len(rows):.1%}), of which {n_clean} fully clean ({n_clean / len(rows):.1%})"
    )
    print("pre-existing (reviewed) labels:", sum(1 for r in rows for lab in r["labels"] if lab["source"] != "injector"))
    for cat, want in sorted(targets.items()):
        if cats.get(cat, 0) < want:
            print(f"WARNING: {cat}: only {cats.get(cat, 0)} of {want} (not enough eligible items)", file=sys.stderr)
    print("by language:", dict(sorted(Counter(r["lang"] for r in rows).items())))
    print("by category:", dict(sorted(cats.items())))
    cost = ledger.summary()
    print(f"new spend INR {cost['new_inr']:.4f} (cached-equivalent {cost['cached_equivalent_inr']:.4f})")
    cache.close()
    return 0


def cmd_inject(a) -> int:
    return asyncio.run(_inject(a))


# ---- export-review -------------------------------------------------------------------

GUIDE = """# Review queue (optional)

`queue.jsonl` holds {n} demo items picked blind (stratified by language, injected vs not, text vs audio; seed {seed}).
Labels are deliberately left out. It is a queue for a possible future human reviewer only: nothing here is
labelled, and calibration does not use it. Calibration uses the injector's labels and the reviewed
pre-existing labels in `demo.jsonl` (`source: "injector"` and `source: "reviewed_preexisting"`).

Audio files are in `sample_data/audio/` (rebuild them with `python scripts/build_demo.py inject --offline`).
"""


def cmd_export_review(a) -> int:
    rows = read_jsonl(a.dataset)
    rng = random.Random(f"{a.seed}:label")
    groups: dict[tuple, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        injected = any(lab["source"] == "injector" for lab in r["labels"])
        groups.setdefault((r["lang"], injected, "audio" in r), []).append(r)
    for g in groups.values():
        rng.shuffle(g)
    chosen: list[dict] = []
    keys = sorted(groups, key=lambda k: (k[1], k[2], k[0]))  # language cycles fastest, so every round covers all languages
    while len(chosen) < min(a.n, len(rows)):
        for key in keys:
            if groups[key] and len(chosen) < a.n:
                chosen.append(groups[key].pop())
    fields = ("id", "lang", "source", "translation", "reference", "audio")
    out = [{k: r[k] for k in fields if k in r} for r in sorted(chosen, key=lambda r: r["id"])]
    write_jsonl(a.out_dir / "queue.jsonl", out)
    (a.out_dir / "REVIEW_QUEUE.md").write_text(GUIDE.format(n=len(out), seed=a.seed), encoding="utf-8")
    inj = sum(1 for r in chosen if any(lab["source"] == "injector" for lab in r["labels"]))
    print(
        f"wrote {len(out)} items to {a.out_dir / 'queue.jsonl'}: {inj} injected, {len(out) - inj} not injected, "
        f"{sum(1 for r in chosen if 'audio' in r)} with audio, langs {dict(sorted(Counter(r['lang'] for r in chosen).items()))}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the demo dataset.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepare")
    s.add_argument("--langs", default="hi,ta,bn")
    s.add_argument("--out", type=Path, default=BUILD / "clean_input.jsonl")
    s.set_defaults(fn=cmd_prepare)
    s = sub.add_parser("inject")
    s.add_argument("--clean-input", type=Path, default=BUILD / "clean_input.jsonl")
    s.add_argument("--clean-run", type=Path, default=BUILD / "clean_run")
    s.add_argument("--out", type=Path, default=Path("sample_data/demo.jsonl"))
    s.add_argument("--audio-dir", type=Path, default=Path("sample_data/audio"))
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--clean-fraction", type=float, default=0.5)
    s.add_argument("--max-cost-inr", type=float, default=0.1)
    s.add_argument("--lifetime-cap-inr", type=float, default=8.0)
    s.add_argument("--cache-dir", type=Path, default=Path(".bodhan_cache"))
    s.add_argument("--offline", action="store_true")
    s.add_argument("--known-issues", type=Path, default=KNOWN_ISSUES, help="reviewed real defects in the clean outputs (labelled as pre-existing)")
    s.add_argument("--per-category", default=DEFAULT_TARGETS, help="items to inject per category, e.g. format=8,omission=8 (one error per item)")
    s.set_defaults(fn=cmd_inject)
    s = sub.add_parser("export-review")
    s.add_argument("--dataset", type=Path, default=Path("sample_data/demo.jsonl"))
    s.add_argument("--n", type=int, default=25)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out-dir", type=Path, default=Path("sample_data/review_queue"))
    s.set_defaults(fn=cmd_export_review)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
