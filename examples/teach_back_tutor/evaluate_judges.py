"""Check the two LLM judges against hand labels.

    python evaluate_judges.py           # writes eval/results.json (shown in the app's Evaluation view)

1. The grader (tutor.evaluate), an LLM-as-a-judge: does its credit per concept, its call on a planted mistake,
   its error detection and its 0-3 quality score agree with a careful teacher? (eval/judge_labels.json)
2. The fact-checker (verify.fact_check, Chain-of-Verification): does it flag statements the page contradicts
   and pass statements the page supports? Precision / recall on eval/verifier_labels.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import exercise  # noqa: E402
import tutor  # noqa: E402
import verify  # noqa: E402

EVAL = HERE / "eval"


def load_card() -> tuple[str, dict]:
    data = json.loads((EVAL / "photosynthesis_card.json").read_text(encoding="utf-8"))
    return data["page_text"], data["card"]


def judge_eval(page: str, card: dict) -> dict:
    items = json.loads((EVAL / "judge_labels.json").read_text(encoding="utf-8"))["items"]
    concept_ok = concept_total = over_credit = plant_ok = plant_total = 0
    err_tp = err_fn = err_fp = quality_ok = 0
    rows = []
    for item in items:
        s = tutor.Session(card=card, band="middle", page_text=page, fact_check=False)
        s.history = [{"role": "assistant", "content": item["buddy"]}]
        if item.get("plant"):
            s.pending_plant = item["plant"]
            s.plants[item["plant"]]["status"] = "planted"
        ev = tutor.evaluate(s, item["message"])
        got = {c.get("id"): c.get("status") for c in ev.get("concepts", []) if isinstance(c, dict)}
        wrong = []
        for cid, accepted in item["concepts"].items():
            concept_total += 1
            status = got.get(cid, "not_covered")
            if status in accepted:
                concept_ok += 1
            else:
                wrong.append(f"{cid}: {status} (expected {'/'.join(accepted)})")
        extra = [cid for cid, st in got.items() if st == "explained" and cid not in item["concepts"]]
        over_credit += len(extra)
        wrong += [f"{cid}: explained but not in the message" for cid in extra]
        if "plant_response" in item:
            plant_total += 1
            plant_ok += ev.get("plant_response") == item["plant_response"]
            if ev.get("plant_response") != item["plant_response"]:
                wrong.append(f"plant: {ev.get('plant_response')} (expected {item['plant_response']})")
        # agreeing with a planted mistake is recorded as a missed plant, not as the student's own error (by design)
        flagged = bool(ev.get("student_errors"))
        expected_err = item.get("error", False)
        err_tp += flagged and expected_err
        err_fn += expected_err and not flagged
        err_fp += flagged and not expected_err and item.get("plant_response") != "missed"
        lo, hi = item["quality"]
        q = int(ev.get("quality", 0) or 0)
        quality_ok += lo <= q <= hi
        if not lo <= q <= hi:
            wrong.append(f"quality: {q} (expected {lo}-{hi})")
        rows.append({"id": item["id"], "message": item["message"], "ok": not wrong, "issues": wrong})
        print(f"  judge #{item['id']:2} {'ok ' if not wrong else 'DIFF'} {'; '.join(wrong)}")
    return {
        "items": len(items),
        "concept_accuracy": round(concept_ok / concept_total, 3),
        "concept_labels": concept_total,
        "over_credit": over_credit,
        "plant_accuracy": round(plant_ok / plant_total, 3) if plant_total else None,
        "plant_labels": plant_total,
        "error_detection": {"found": err_tp, "missed": err_fn, "false_alarms": err_fp},
        "quality_in_range": round(quality_ok / len(items), 3),
        "rows": rows,
    }


def _scores(rows: list[dict]) -> dict:
    tp = sum(r["label"] == "false" and r["flagged"] for r in rows)
    fn = sum(r["label"] == "false" and not r["flagged"] for r in rows)
    fp = sum(r["label"] == "true" and r["flagged"] for r in rows)
    tn = sum(r["label"] == "true" and not r["flagged"] for r in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "statements": len(rows), "precision": round(precision, 3), "recall": round(recall, 3),
        "f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
        "accuracy": round((tp + tn) / len(rows), 3) if rows else 0.0,
        "confusion": {"false_flagged": tp, "false_missed": fn, "true_flagged": fp, "true_passed": tn},
    }


def verifier_eval(page: str, card: dict) -> dict:
    labels = json.loads((EVAL / "verifier_labels.json").read_text(encoding="utf-8"))
    s = tutor.Session(card=card, band="middle", page_text=page)
    rows = []
    for group, key, truth in [("main", "true", "true"), ("main", "false", "false"),
                              ("held_out", "held_out_true", "true"), ("held_out", "held_out_false", "false")]:
        for statement in labels.get(key, []):
            result = verify.fact_check(s, {"move": "clarify"}, f"Oh I see! So {statement}")
            flagged = bool(result["false"])
            correct = flagged == (truth == "false")
            rows.append({"group": group, "statement": statement, "label": truth, "flagged": flagged, "ok": correct,
                         "verdicts": [{k: v[k] for k in ("claim", "verdict", "evidence")} for v in result["verdicts"]]})
            print(f"  verify {group:8} [{truth:5}] {'ok ' if correct else 'DIFF'} flagged={flagged!s:5} {statement}")
    return {**_scores(rows), "main": _scores([r for r in rows if r["group"] == "main"]),
            "held_out": _scores([r for r in rows if r["group"] == "held_out"]), "rows": rows}


def _correct(answer: str, any_of: list[str]) -> bool:
    tokens = re.findall(r"[a-z0-9.']+", answer.lower().replace(" ", " "))
    words = {t.strip(".'") for t in tokens}  # "leaves." -> "leaves"; decimals like "2.5" keep their point
    return any(w.lower() in words for w in any_of)


def homework_eval(page: str) -> dict:
    """Homework mode's answer key: one solve vs self-consistency (3 solves, majority vote, abstain otherwise)."""
    items = json.loads((EVAL / "exercise_labels.json").read_text(encoding="utf-8"))["items"]
    rows = []
    for item in items:
        ref = exercise.solve(item["question"], page, "middle")
        single = bool(ref["answers"]) and _correct(ref["answers"][0], item["any_of"])
        voted = ref["trusted"] and _correct(ref["final_answer"], item["any_of"])
        rows.append({"question": item["question"], "agreement": ref["agreement"], "trusted": ref["trusted"],
                     "answer": ref["final_answer"], "all_answers": ref["answers"],
                     "single_correct": single, "voted_correct": voted,
                     "wrong_but_trusted": ref["trusted"] and not voted})
        print(f"  homework {ref['agreement']} {'ok ' if voted else 'DIFF'} single={single!s:5} {item['question'][:60]}")
    trusted = [r for r in rows if r["trusted"]]
    return {
        "items": len(rows),
        "single_solve_accuracy": round(sum(r["single_correct"] for r in rows) / len(rows), 3),
        "answered": len(trusted),
        "accuracy_when_answered": round(sum(r["voted_correct"] for r in trusted) / len(trusted), 3) if trusted else None,
        "abstained": len(rows) - len(trusted),
        "wrong_but_trusted": sum(r["wrong_but_trusted"] for r in rows),
        "rows": rows,
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["judge", "verifier", "homework"], help="run one part; keep the others' results")
    args = ap.parse_args()
    page, card = load_card()
    path = EVAL / "results.json"
    out = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    model = os.environ.get("LLM_MODEL", "")
    t = time.perf_counter()
    if args.only in (None, "judge"):
        print("Grader (LLM-as-a-judge) vs hand labels:")
        out["judge"] = judge = judge_eval(page, card)
        print(f"Grader: concepts {judge['concept_accuracy']:.0%} of {judge['concept_labels']}, "
              f"over-credit {judge['over_credit']}, planted mistakes {judge['plant_accuracy']:.0%}, "
              f"quality in range {judge['quality_in_range']:.0%}, errors {judge['error_detection']}")
    if args.only in (None, "verifier"):
        print("\nFact-checker (Chain-of-Verification) vs hand labels:")
        out["verifier"] = verifier = verifier_eval(page, card)
        print(f"Fact-checker: precision {verifier['precision']:.0%}, recall {verifier['recall']:.0%}, "
              f"F1 {verifier['f1']:.2f}, accuracy {verifier['accuracy']:.0%}")
    if args.only in (None, "homework"):
        print("\nHomework answer key (self-consistency) vs known answers:")
        out["homework"] = hw = {**homework_eval(page), "model": model, "date": date.today().isoformat()}
        print(f"Homework: single solve {hw['single_solve_accuracy']:.0%}; self-consistency answered "
              f"{hw['answered']}/{hw['items']} with {hw['accuracy_when_answered']:.0%} correct, "
              f"abstained {hw['abstained']}, wrong but trusted {hw['wrong_but_trusted']}")
    if args.only in (None, "judge", "verifier"):
        out.update({"date": date.today().isoformat(), "model": model})
    out["seconds"] = round(time.perf_counter() - t)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {path}")
