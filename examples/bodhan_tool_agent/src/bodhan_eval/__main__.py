"""Self-evaluation: run the agent on a suite of tasks and grade every run with deterministic checks.

    python -m bodhan_eval                     # default suite (expensive cases left out), suite budget ₹1
    python -m bodhan_eval --list              # show cases, tags and estimated cost; runs nothing
    python -m bodhan_eval --only telugu_clips french_refused --repeat 3
    python -m bodhan_eval --include-expensive --max-total-inr 3
    python -m bodhan_eval --regrade outputs/eval/<run>   # re-grade finished runs at no cost

Writes <out>/<case>[-<n>]/ (the agent's own out/ folder per run), eval_results.json and eval_report.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .runner import DEFAULT_SUITE, RECIPE, load_suite, run_case, score_case, select, summarize, write_reports


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    p.add_argument("--only", nargs="+", help="case ids to run (runs them even if tagged expensive)")
    p.add_argument("--tags", nargs="+", help="run only cases with any of these tags")
    p.add_argument("--include-expensive", action="store_true", help="also run cases tagged expensive")
    p.add_argument("--repeat", type=int, default=1, help="run each case N times (consistency, pass^N)")
    p.add_argument("--max-total-inr", type=float, default=1.0, help="Bodhan spend limit for the whole suite")
    p.add_argument("--out", type=Path, help="results folder (default outputs/eval/<timestamp>)")
    p.add_argument("--root", type=Path, default=RECIPE, help="folder that case file paths are relative to")
    p.add_argument("--list", action="store_true", help="list the selected cases and exit")
    p.add_argument("--regrade", type=Path, metavar="DIR", help="grade the runs already in DIR; runs no agent")
    return p


def _llm_model() -> str:
    from bodhan_core.config import load_settings

    return load_settings().llm_model or "unset"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeat < 1:
        raise SystemExit("--repeat must be at least 1")
    cases = select(load_suite(args.suite), args.only, args.tags, args.include_expensive)
    if args.list:
        for c in cases:
            print(f"{c.id:22} est ₹{c.est_cost_inr:<5} [{', '.join(c.tags)}]  {c.task}")
        print(f"{len(cases)} case(s); estimated cost ₹{sum(c.est_cost_inr for c in cases) * args.repeat:.2f} "
              "(less when inputs are cached)")
        return 0

    if args.regrade:
        dest = args.regrade
        results = []
        for c in cases:
            for out in sorted(p for p in dest.glob(f"{c.id}*") if p.is_dir() and p.name.split("-")[0] == c.id):
                results.append(score_case(c, out, {"exit_code": "regraded", "wall_s": None, "stderr_tail": ""}))
        summary = summarize(results, [], _llm_model() + " (regraded)", args.repeat)
        write_reports(dest, summary, results)
        return _print(summary, results, dest)

    dest = args.out or RECIPE / "outputs" / "eval" / time.strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    results, skipped, spent = [], [], 0.0
    for n in range(1, args.repeat + 1):
        for c in cases:
            missing = [str(p) for p in c.paths(args.root) if not p.exists()]
            if c.inputs_must_exist and missing:
                skipped.append({"id": c.id, "why": f"missing input {missing[0]}; run scripts/make_demo_inputs.py "
                                                   "and scripts/make_full_demo_inputs.py"})
                continue
            remaining = args.max_total_inr - spent
            if remaining <= 0 or c.est_cost_inr > remaining:
                skipped.append({"id": c.id, "why": f"suite budget: ₹{remaining:.2f} left, case estimate "
                                                   f"₹{c.est_cost_inr}"})
                continue
            out = dest / (c.id if args.repeat == 1 else f"{c.id}-{n}")
            print(f"[{c.id}{'' if args.repeat == 1 else f' #{n}'}] {c.task}", flush=True)
            meta = run_case(c, out, args.root, min(c.max_cost_inr, remaining))
            r = score_case(c, out, meta)
            spent += r["cost_inr"]
            results.append(r)
            print(f"  {'PASS' if r['passed'] else 'FAIL'}  status={r['status']} steps={r['steps']} "
                  f"cost=₹{r['cost_inr']:.3f} time={meta['wall_s']}s", flush=True)
            for chk in r["checks"]:
                if not chk["passed"]:
                    print(f"    ✗ {chk['type']}: {chk['detail']}", flush=True)
    summary = summarize(results, skipped, _llm_model(), args.repeat)
    write_reports(dest, summary, results)
    return _print(summary, results, dest)


def _print(summary: dict, results: list[dict], dest: Path) -> int:
    rate = f"{summary['pass_rate']:.0%}" if summary["pass_rate"] is not None else "n/a"
    print(f"\n{summary['runs_passed']}/{summary['runs']} runs passed ({rate}); "
          f"Bodhan ₹{summary['total_cost_inr']:.3f}; report: {dest / 'eval_report.md'}")
    for s in summary["skipped"]:
        print(f"  skipped {s['id']}: {s['why']}")
    return 0 if results and summary["runs_passed"] == summary["runs"] else 1


if __name__ == "__main__":
    sys.exit(main())
