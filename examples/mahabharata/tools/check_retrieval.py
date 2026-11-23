"""Run the REAL graph-RAG retrieval (graph_rag.VectorlessGraphRAG via llm.RAG, Mahabharata graph) for voyage questions.

    python tools/check_retrieval.py web/data/voyage_parts/chapters_01_05.json            # every question in the file
    python tools/check_retrieval.py web/data/voyage_parts/chapters_01_05.json --ids q_ch03_vow q_ch04_x
    python tools/check_retrieval.py ... --out report.json

A question passes when retrieval returns >= MIN_CONTEXTS contexts (default 2) without error.
Makes real Groq calls (several per question); runs sequentially to respect rate limits.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KNOWLEDGE_GRAPH", "mahabharata")

import server  # noqa: E402  (applies the graph adapter: llm.GRAPH = Mahabharata graph)
import llm  # noqa: E402


def questions_of(path):
    data = json.load(open(path, encoding="utf-8"))
    for ch in data.get("chapters", []):
        for q in [ch.get("central_question")] + list(ch.get("questions") or []):
            if q:
                yield ch["id"], q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--min", type=int, default=2)
    ap.add_argument("--out")
    a = ap.parse_args()
    if server.GRAPH_INFO["name"] != "mahabharata":
        sys.exit("Mahabharata graph not active")
    report = []
    for cid, q in questions_of(a.file):
        if a.ids and q["id"] not in a.ids:
            continue
        t0 = time.time()
        err, paths = None, []
        for attempt in range(3):
            try:
                r = llm.RAG.retrieve(q["search_query"], llm.GRAPH)
                paths = [c["path"] for c in r["context"]]
                err = None
                break
            except Exception as e:  # rate limit / network
                err = f"{type(e).__name__}: {e}"
                time.sleep(5 * (attempt + 1))
        ok = err is None and len(paths) >= a.min
        row = {"chapter": cid, "id": q["id"], "ok": ok, "contexts": len(paths), "paths": paths,
               "error": err, "secs": round(time.time() - t0, 1), "words": len(q["search_query"].split())}
        report.append(row)
        print(f"{'PASS' if ok else 'FAIL'} {q['id']:<34} ctx={len(paths)} {row['secs']}s  {'; '.join(paths)[:160]}"
              + (f"  ERR {err}" if err else ""), flush=True)
    n_ok = sum(r["ok"] for r in report)
    print(f"\n{n_ok}/{len(report)} passed")
    if a.out:
        json.dump(report, open(a.out, "w"), indent=2)
    sys.exit(0 if n_ok == len(report) else 1)


if __name__ == "__main__":
    main()
