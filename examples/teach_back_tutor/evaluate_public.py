"""Check the two LLM judgements against public datasets labelled by other people (not by us).

    python evaluate_public.py --scientsbank 40 --gsm8k 30     # writes eval/public.json

1. SciEntsBank (SemEval-2013 Task 7; Dzikovska et al., 2013): real school students' answers to science
   questions, graded by experts as correct / partially correct / contradictory / irrelevant. Each question's
   reference answer becomes a one-idea lesson card, and our grader (tutor.evaluate) judges the student answer.
   Mapping: correct -> explained, partially correct -> partial, contradictory / irrelevant -> not explained.
2. GSM8K (Cobbe et al., 2021): grade-school maths word problems with verified answers, the benchmark the
   self-consistency paper used. Homework mode's answer key: one solve vs a 3-solve majority vote.

Data is downloaded once into outputs/public/ (not committed); a fixed seed picks the same sample every run.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import exercise  # noqa: E402
import tutor  # noqa: E402

CACHE = HERE / "outputs" / "public"
GSM8K_URL = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"
SEB_URL = "https://datasets-server.huggingface.co/rows?dataset=nkazi/SciEntsBank&config=default&split=test_ua"
SEB_LABELS = ["correct", "contradictory", "partially_correct_incomplete", "irrelevant", "non_domain"]
EXPECT = {"correct": "explained", "partially_correct_incomplete": "partial",
          "contradictory": "not_explained", "irrelevant": "not_explained", "non_domain": "not_explained"}


def _cached(name: str, fetch) -> list:
    path = CACHE / name
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fetch(), ensure_ascii=False), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def _seb_rows() -> list[dict]:
    rows, offset = [], 0
    while True:
        page = requests.get(f"{SEB_URL}&offset={offset}&length=100", timeout=60).json()["rows"]
        rows += [r["row"] for r in page]
        if len(page) < 100:
            return rows
        offset += 100


def scientsbank(n: int) -> dict:
    rows = _cached("scientsbank_test_ua.json", _seb_rows)
    by_label: dict[str, list] = {}
    for r in rows:
        by_label.setdefault(SEB_LABELS[r["label"]], []).append(r)
    rng = random.Random(7)
    sample = []
    for label in ("correct", "partially_correct_incomplete", "contradictory", "irrelevant"):  # balanced classes
        pool = by_label.get(label, [])
        sample += rng.sample(pool, min(len(pool), n // 4))
    results, confusion = [], Counter()
    for r in sample:
        card = {"topic": "science question", "summary": r["reference_answer"],
                "concepts": [{"id": "c1", "name": "the answer", "explanation": r["reference_answer"],
                              "evidence": r["reference_answer"]}], "misconceptions": []}
        s = tutor.Session(card=card, band="middle", fact_check=False,
                          page_text=f"Question: {r['question']}\nAnswer: {r['reference_answer']}")
        s.history = [{"role": "assistant", "content": r["question"]}]
        ev = tutor.evaluate(s, r["student_answer"])
        status = next((c.get("status") for c in ev.get("concepts", []) if isinstance(c, dict)), "not_covered")
        got = "explained" if status == "explained" else "partial" if status == "partial" else "not_explained"
        if r["label"] == 1 and ev.get("student_errors"):  # contradictory answers should also be flagged as errors
            got = "not_explained"
        label = SEB_LABELS[r["label"]]
        confusion[(EXPECT[label], got)] += 1
        results.append({"id": r["id"], "label": label, "expected": EXPECT[label], "got": got,
                        "student_answer": r["student_answer"]})
        print(f"  {label:30} -> {got:14} {'ok ' if got == EXPECT[label] else 'DIFF'} {r['student_answer'][:50]}")
    ok = sum(x["got"] == x["expected"] for x in results)
    over = sum(x["got"] == "explained" and x["expected"] != "explained" for x in results)
    wrong_credited = sum(x["got"] == "explained" and x["label"] in ("contradictory", "irrelevant") for x in results)
    return {"items": len(results), "accuracy_3way": round(ok / len(results), 3),
            "over_credit": over, "wrong_answer_given_full_credit": wrong_credited,
            "per_label": {lab: f"{sum(x['got'] == x['expected'] for x in results if x['label'] == lab)}/"
                               f"{sum(x['label'] == lab for x in results)}" for lab in by_label if lab != "non_domain"},
            "confusion": {f"{e} -> {g}": c for (e, g), c in sorted(confusion.items())}, "rows": results}


def _number(text: str) -> str | None:
    nums = re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return str(float(nums[-1])) if nums else None


def gsm8k(n: int) -> dict:
    lines = _cached("gsm8k_test.json", lambda: requests.get(GSM8K_URL, timeout=60).text.splitlines())
    sample = random.Random(7).sample([json.loads(x) for x in lines], n)
    rows = []
    for item in sample:
        truth = _number(item["answer"].split("####")[-1])
        ref = exercise.solve(item["question"], "(no page: a maths word problem)", "high")
        single = bool(ref["answers"]) and _number(ref["answers"][0]) == truth
        voted = ref["trusted"] and _number(ref["final_answer"]) == truth
        rows.append({"question": item["question"], "truth": truth, "answers": ref["answers"],
                     "agreement": ref["agreement"], "trusted": ref["trusted"], "single_correct": single,
                     "voted_correct": voted, "wrong_but_trusted": ref["trusted"] and not voted})
        print(f"  {ref['agreement']} {'ok ' if voted else 'DIFF' if ref['trusted'] else 'ABST'} "
              f"single={single!s:5} truth={truth} {item['question'][:50]}")
    trusted = [r for r in rows if r["trusted"]]
    return {"items": len(rows), "single_solve_accuracy": round(sum(r["single_correct"] for r in rows) / len(rows), 3),
            "answered": len(trusted),
            "accuracy_when_answered": round(sum(r["voted_correct"] for r in trusted) / len(trusted), 3) if trusted else None,
            "abstained": len(rows) - len(trusted), "wrong_but_trusted": sum(r["wrong_but_trusted"] for r in rows),
            "rows": rows}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--scientsbank", type=int, default=0, help="student answers to grade (balanced over 4 labels)")
    ap.add_argument("--gsm8k", type=int, default=0, help="maths word problems for the homework answer key")
    args = ap.parse_args()
    path = HERE / "eval" / "public.json"
    out = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    meta = {"date": date.today().isoformat(), "model": os.environ.get("LLM_MODEL", "")}
    if args.scientsbank:
        print("SciEntsBank (expert-graded student answers) vs our grader:")
        out["scientsbank"] = {**scientsbank(args.scientsbank), **meta}
        s = out["scientsbank"]
        print(f"Grader: {s['accuracy_3way']:.0%} agree with experts; per label {s['per_label']}; "
              f"over-credit {s['over_credit']}, wrong answers given full credit {s['wrong_answer_given_full_credit']}")
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.gsm8k:
        print("\nGSM8K (maths word problems) vs Homework mode's answer key:")
        out["gsm8k"] = {**gsm8k(args.gsm8k), **meta}
        g = out["gsm8k"]
        print(f"One solve {g['single_solve_accuracy']:.0%}; self-consistency answered {g['answered']}/{g['items']} "
              f"with {g['accuracy_when_answered']:.0%} correct, abstained {g['abstained']}, "
              f"wrong but trusted {g['wrong_but_trusted']}")
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
