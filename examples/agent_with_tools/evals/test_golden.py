"""pytest entry point for the golden set.

    pytest evals -m stub                  # real LLM, stubbed Bodhan tools, Rs 0 (needs LLM_* in .env)
    EVAL_JUDGE=1 pytest evals -m stub     # + DeepEval metrics judged by the same Groq model
    EVAL_LIVE=1 pytest evals -m live      # spends Bodhan credit: only cases marked live, each with its own cap

The default `pytest` run (testpaths = tests) never touches this folder.
"""

from __future__ import annotations

import os

import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from bodhan_core import ledger  # noqa: E402
from evals.harness import ROOT_DEFAULT_LEDGER, expectations, load_golden, make_llm, run_case  # noqa: E402
from evals.scorers import ADVISORY_METRICS, score_case  # noqa: E402

GOLDEN = load_golden()
CASES = GOLDEN["cases"]
HAS_LLM = all(os.environ.get(k) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"))
JUDGE = os.environ.get("EVAL_JUDGE") == "1"


def _params(mode: str):
    for c in CASES:
        if mode == "live" and not c.get("live"):
            continue
        yield pytest.param(c, id=c["id"], marks=getattr(pytest.mark, mode))


async def _check(case: dict, mode: str, tmp_path) -> None:
    if not HAS_LLM:
        pytest.skip("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL are not set")
    if mode == "live":
        if os.environ.get("EVAL_LIVE") != "1":
            pytest.skip("live evals spend Bodhan credit: set EVAL_LIVE=1")
        if missing := [k for k in case.get("live_requires", []) if not os.environ.get(k)]:
            pytest.skip(f"needs {', '.join(missing)}")
        if ledger.remaining(ROOT_DEFAULT_LEDGER) < ledger.LOW_CREDIT_INR:
            pytest.skip("less than Rs 2 of Bodhan credit left")
    run = await run_case(case, GOLDEN["defaults"], GOLDEN["fixtures"], mode, tmp_path, make_llm())
    if mode == "live" and run.cost_inr > 0:
        ledger.record(f"pytest live eval: {case['id']}", "mixed (eval)", run.cost_inr, ROOT_DEFAULT_LEDGER)
    if run.rate_limited:
        pytest.skip(f"LLM provider rate limit: {run.error[:120]}")
    expect = expectations(case, mode)
    scores = score_case(run, expect)
    if JUDGE and not run.error:
        from evals.judge import GroqJudge, deepeval_scores

        scores += deepeval_scores(case, run, expect, GroqJudge())
    failed = [s for s in scores if s["value"] == 0.0 and s["name"] != "case_passed"]
    failed += [s for s in scores if s["name"].startswith("de_") and s["name"] not in ADVISORY_METRICS and s["value"] is not None and s["value"] < 0.7]
    assert not failed, "\n".join(f"- {s['name']}: {s['comment']}" for s in failed) + f"\n(status={run.status}, turns={run.llm_turns}, cost=Rs {run.cost_inr}, calls={[c.name for c in run.calls]})"


@pytest.mark.parametrize("case", _params("stub"))
async def test_stub(case, tmp_path):
    await _check(case, "stub", tmp_path)


@pytest.mark.parametrize("case", _params("live"))
async def test_live(case, tmp_path):
    await _check(case, "live", tmp_path)
