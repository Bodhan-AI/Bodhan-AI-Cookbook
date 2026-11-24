"""Run the golden set and record it on Langfuse.

    python -m evals.run_evals                       # stub mode: real LLM, paid Bodhan tools stubbed (Rs 0)
    python -m evals.run_evals --judge               # + DeepEval LLM metrics (extra Groq calls, no Bodhan cost)
    python -m evals.run_evals --only translate_one_line_to_hindi,injection_in_text_file
    python -m evals.run_evals --mode live --only translate_one_line_to_hindi   # spends Bodhan credit (see below)

Live mode only runs cases marked "live": true whose Bodhan keys are set, checks the credit ledger first and writes every
paid run to outputs/credit_ledger.json. Each case is also capped by its own max_cost_inr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())  # BEFORE importing langfuse, so it sees the keys

from bodhan_agent.config import load_llm_settings  # noqa: E402
from bodhan_core import ledger  # noqa: E402
from evals.harness import ROOT_DEFAULT_LEDGER, CaseRun, expectations, load_golden, make_llm, run_case  # noqa: E402
from evals.scorers import ADVISORY_METRICS, score_case  # noqa: E402

DATASET = "bodhan-agent/golden"
EXPERIMENT = "bodhan-agent-evals"


def select_cases(golden: dict, mode: str, only: list[str] | None) -> tuple[list[dict], list[str]]:
    chosen, skipped = [], []
    for case in golden["cases"]:
        if only and case["id"] not in only:
            continue
        if mode == "live":
            if not case.get("live"):
                skipped.append(f"{case['id']}: not marked live (too costly or needs files we do not have)")
                continue
            missing = [k for k in case.get("live_requires", []) if not os.environ.get(k)]
            if missing:
                skipped.append(f"{case['id']}: needs {', '.join(missing)}")
                continue
        chosen.append(case)
    return chosen, skipped


class Evaluator:
    """Runs cases once and caches the results, so Langfuse evaluators and the console table share one run."""

    def __init__(self, golden: dict, mode: str, judge: bool, work_root: Path):
        self.golden, self.mode, self.work_root = golden, mode, work_root
        self.cases = {c["id"]: c for c in golden["cases"]}
        self.llm = make_llm()
        self.judge = None
        if judge:
            from evals.judge import GroqJudge

            self.judge = GroqJudge()
        self.runs: dict[str, CaseRun] = {}
        self.scores: dict[str, list[dict]] = {}

    async def execute(self, case_id: str, callbacks: list | None = None) -> CaseRun:
        case = self.cases[case_id]
        run = await run_case(case, self.golden["defaults"], self.golden["fixtures"], self.mode, self.work_root / case_id, self.llm, callbacks)
        expect = expectations(case, self.mode)
        scores = [] if run.rate_limited else score_case(run, expect)  # no verdict when the LLM provider refused to answer
        if self.judge is not None and not run.error:
            from evals.judge import deepeval_scores

            scores += await asyncio.to_thread(deepeval_scores, case, run, expect, self.judge)
        if run.cassette_hits or run.cassette_misses:
            total = run.cassette_hits + len(run.cassette_misses)
            scores.append({"name": "cassette_hit_rate", "value": round(run.cassette_hits / total, 3),
                           "comment": f"{run.cassette_hits}/{total} paid-tool calls replayed real recordings" + (f"; missed: {run.cassette_misses[:4]}" if run.cassette_misses else "")})
        self.runs[case_id], self.scores[case_id] = run, scores
        if self.mode == "live" and run.cost_inr > 0:
            remaining = ledger.record(f"eval live run: {case_id}", "mixed (eval)", run.cost_inr, ROOT_DEFAULT_LEDGER)
            print(f"[ledger] spent Rs {run.cost_inr:.4f} on {case_id}; Rs {remaining:.2f} credit left", flush=True)
        return run


def print_table(ev: Evaluator) -> None:
    print(f"\n{'case':34} {'result':7} {'status':16} {'turns':>5} {'cost Rs':>8} {'time s':>7}  failed checks")
    for cid, run in ev.runs.items():
        scores = ev.scores[cid]
        failed = [s["name"] for s in scores if s["value"] == 0.0 and s["name"] != "case_passed" and s["name"] not in ADVISORY_METRICS]
        passed = bool(scores) and next(s["value"] for s in scores if s["name"] == "case_passed") == 1.0
        verdict = "RATELIM" if run.rate_limited else "PASS" if passed else "FAIL"
        print(f"{cid:34} {verdict:7} {run.status:16} {run.llm_turns:>5} {run.cost_inr:>8.4f} {run.elapsed_s:>7.1f}  {', '.join(failed)}")
    n_pass = sum(bool(v) and next(s["value"] for s in v if s["name"] == "case_passed") == 1.0 for v in ev.scores.values())
    limited = [cid for cid, r in ev.runs.items() if r.rate_limited]
    if limited:
        print(f"\n{len(limited)} case(s) INCONCLUSIVE: the LLM provider rate-limited them (e.g. Groq daily token cap). Re-run: --only {','.join(limited)}")
    print(f"\n{n_pass}/{len(ev.runs)} cases passed, total tool cost Rs {sum(r.cost_inr for r in ev.runs.values()):.4f}")
    for cid, scores in ev.scores.items():
        for s in scores:
            if s["value"] == 0.0 and s["name"] != "case_passed" and s["name"] not in ADVISORY_METRICS:
                print(f"  - {cid} / {s['name']}: {s['comment']}")
    for cid, scores in ev.scores.items():
        for s in scores:
            if s["name"].startswith("de_") or s["name"] == "cassette_hit_rate":
                print(f"  [deepeval{' advisory' if s['name'] in ADVISORY_METRICS else ''}] {cid} / {s['name']}: {s['value']} {s['comment'][:120]}")


def langfuse_run(ev: Evaluator, cases: list[dict], run_name: str, model: str) -> str | None:
    """Sync the dataset, run the experiment on it and attach every score to its trace. Returns the dataset-run URL if known."""
    from langfuse import Evaluation, get_client
    from langfuse.langchain import CallbackHandler

    lf = get_client()
    lf.create_dataset(name=DATASET, description="Golden tasks for the Bodhan tool agent (see evals/golden.json)")
    for c in ev.golden["cases"]:
        lf.create_dataset_item(
            dataset_name=DATASET, id=f"golden::{c['id']}",
            input={"task": c["task"], "input_files": [i["name"] for i in c.get("inputs", [])] + list(c.get("extra_paths", []))},
            expected_output=c["expect"], metadata={"case_id": c["id"], "source": c.get("source", ""), "live": bool(c.get("live"))},
        )
    wanted = {f"golden::{c['id']}" for c in cases}
    items = [it for it in lf.get_dataset(DATASET).items if it.id in wanted]

    async def task(*, item, **_):
        cid = item.metadata["case_id"]
        run = await ev.execute(cid, callbacks=[CallbackHandler()])
        return run.summary()

    def checks(*, metadata=None, **_):
        return [Evaluation(name=s["name"], value=s["value"], comment=s["comment"]) for s in ev.scores.get((metadata or {}).get("case_id"), []) if s["value"] is not None]

    def totals(*, item_results, **_):
        out = []
        passed = [e.value for r in item_results for e in r.evaluations if e.name == "case_passed"]
        if passed:
            out.append(Evaluation(name="pass_rate", value=sum(passed) / len(passed), comment=f"{int(sum(passed))}/{len(passed)} cases passed"))
        out.append(Evaluation(name="total_tool_cost_inr", value=round(sum(r.cost_inr for r in ev.runs.values()), 6)))
        return out

    result = lf.run_experiment(name=EXPERIMENT, run_name=run_name, description=f"{ev.mode} mode, agent LLM {model}", data=items, task=task,
                               evaluators=[checks], run_evaluators=[totals], max_concurrency=1,
                               metadata={"mode": ev.mode, "llm_model": model, "judge": str(ev.judge is not None)})
    lf.flush()
    return getattr(result, "dataset_run_url", None)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="evals.run_evals")
    ap.add_argument("--mode", choices=["stub", "live"], default="stub")
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--judge", action="store_true", help="also run the DeepEval LLM metrics (Groq calls)")
    ap.add_argument("--no-langfuse", action="store_true")
    ap.add_argument("--run-name")
    ap.add_argument("--keep", action="store_true", help="keep each case's inputs and out/ folder under outputs/evals/<run>/")
    args = ap.parse_args()

    golden = load_golden()
    cases, skipped = select_cases(golden, args.mode, args.only.split(",") if args.only else None)
    for s in skipped:
        print(f"[skip] {s}")
    if not cases:
        print("No cases to run.")
        return 2
    model = load_llm_settings().model
    if args.mode == "live":
        estimate = sum(c.get("live_cost_est_inr", 0.0) for c in cases)
        left = ledger.remaining(ROOT_DEFAULT_LEDGER)
        print(f"[live] estimated Bodhan cost Rs {estimate:.3f}; credit left Rs {left:.2f}")
        if left < ledger.LOW_CREDIT_INR:
            print(f"[live] less than Rs {ledger.LOW_CREDIT_INR} of credit left: refusing to run live cases.")
            return 2
        if estimate > left:
            print("[live] estimate exceeds the credit left: refusing.")
            return 2

    run_name = args.run_name or f"{args.mode}-{model.split('/')[-1]}-{datetime.now():%Y%m%d-%H%M%S}"
    work_root = (ROOT_DEFAULT_LEDGER.parent / "evals" / run_name) if args.keep else Path(tempfile.mkdtemp(prefix="bodhan-evals-"))
    ev = Evaluator(golden, args.mode, args.judge, work_root)
    use_langfuse = not args.no_langfuse and bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))
    print(f"[run] {run_name}: {len(cases)} case(s), agent+judge model {model}, langfuse={'on' if use_langfuse else 'off'}")

    t0 = time.perf_counter()
    if use_langfuse:
        langfuse_run(ev, cases, run_name, model)
    else:
        async def go():
            for c in cases:
                print(f"[case] {c['id']} ...", flush=True)
                await ev.execute(c["id"])
        asyncio.run(go())
    print_table(ev)
    print(f"[done] {time.perf_counter() - t0:.0f}s" + (f"; results in Langfuse dataset '{DATASET}', experiment '{EXPERIMENT}', run '{run_name}'" if use_langfuse else ""))
    out = ROOT_DEFAULT_LEDGER.parent / "evals" / f"{run_name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({cid: {"summary": ev.runs[cid].summary(), "scores": ev.scores[cid]} for cid in ev.runs}, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if all(v and next(s["value"] for s in v if s["name"] == "case_passed") == 1.0 for v in ev.scores.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
