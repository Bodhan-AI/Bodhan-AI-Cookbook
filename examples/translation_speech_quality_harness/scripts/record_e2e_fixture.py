"""Record the offline end-to-end fixture: 10 items + only the cache entries they need.

    python scripts/record_e2e_fixture.py            # from outputs/demo_inj + .bodhan_cache

Picks 10 text-only items from the injected demo set (a mix of planted errors and untouched
controls), runs the real pipeline offline against the full local cache while recording
which cache keys it reads, and writes:

  tests/fixtures/e2e/dataset.jsonl   the 10 items (audio fields dropped: text checks only)
  tests/fixtures/e2e/cache.sqlite    just the cache rows those reads hit

Re-run it after changing a prompt or a request shape (the e2e test then fails with steps
skipped as "offline: not in cache"). Recording reads the cache only: it never calls a model.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DATASET = ROOT / "outputs" / "demo_inj" / "injected.jsonl"
SRC_CACHE = ROOT / ".bodhan_cache" / "cache.sqlite"
OUT = ROOT / "tests" / "fixtures" / "e2e"
WANT = {"injected": 6, "assumed_clean": 4}
JUDGE_MODEL = "claude-haiku-5-5"


def choose(rows: list[dict]) -> list[dict]:
    """Deterministic mix: one item per injection kind, then controls, rotating languages."""
    text_only = sorted((r for r in rows if "audio" not in r), key=lambda r: r["id"])
    by_lang: dict[str, list[dict]] = {}
    for r in text_only:
        by_lang.setdefault(r["lang"], []).append(r)
    order = [r for group in zip(*by_lang.values(), strict=False) for r in group]  # round robin
    picked, kinds = [], set()
    for r in order:
        if r.get("label_scope") == "injected" and r["labels"][0]["type"] not in kinds:
            kinds.add(r["labels"][0]["type"])
            picked.append(r)
        if len(kinds) == WANT["injected"]:
            break
    controls = [r for r in order if r.get("label_scope") == "assumed_clean"]
    return picked + controls[: WANT["assumed_clean"]]


def main() -> int:
    rows = [json.loads(x) for x in SRC_DATASET.read_text("utf-8").splitlines() if x]
    items = choose(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    ds = OUT / "dataset.jsonl"
    ds.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in items), "utf-8")

    # Run the real pipeline offline on a copy of the full cache, recording every key it reads.
    from bodhan_eval import cache as cache_mod
    from bodhan_eval import pipeline
    from bodhan_eval.cli import build_parser

    read: set[str] = set()
    orig_get = cache_mod.Cache.get

    def recording_get(self, namespace, payload):
        hit = orig_get(self, namespace, payload)
        if hit is not None:
            read.add(cache_mod.Cache.key(namespace, payload))
        return hit

    cache_mod.Cache.get = recording_get
    os.environ.setdefault("LLM_MODEL", JUDGE_MODEL)
    os.environ.setdefault("ANTHROPIC_API_KEY", "offline-recording")
    with tempfile.TemporaryDirectory() as tmp:
        cdir = Path(tmp) / "cache"
        cdir.mkdir()
        shutil.copy(SRC_CACHE, cdir / "cache.sqlite")
        args = build_parser().parse_args([
            "--dataset", str(ds), "--out", str(Path(tmp) / "out"), "--cache-dir", str(cdir),
            "--offline", "--judge-repeats", "0", "--log-level", "ERROR",
        ])  # fmt: skip
        code = pipeline.run(args)
        summary = json.loads((Path(tmp) / "out" / "summary.json").read_text())
    cache_mod.Cache.get = orig_get
    if code != 0 or summary["skipped_steps"]:
        print(f"recording run incomplete: exit {code}, skipped {summary['skipped_steps']}",
              file=sys.stderr)  # fmt: skip
        return 1

    dst = OUT / "cache.sqlite"
    dst.unlink(missing_ok=True)
    src = sqlite3.connect(SRC_CACHE)
    out = sqlite3.connect(dst)
    out.execute(
        "CREATE TABLE entries (key TEXT PRIMARY KEY, namespace TEXT NOT NULL,"
        " request TEXT NOT NULL, value BLOB NOT NULL, meta TEXT NOT NULL, created REAL NOT NULL)"
    )
    n = 0
    for key in sorted(read):
        row = src.execute("SELECT * FROM entries WHERE key=?", (key,)).fetchone()
        if row:
            out.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)", row)
            n += 1
    out.commit()
    out.execute("VACUUM")
    out.close()
    print(f"{len(items)} items, {n} cache rows -> {OUT.relative_to(ROOT)} "
          f"({dst.stat().st_size // 1024} KB)", file=sys.stderr)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
