"""Judge model bake-off: the same labelled items, tiers 0-1 once, then only the judge, once per model.

    python -m bodhan_eval.bakeoff --dataset data/bakeoff.jsonl --models a/x,b/y --out outputs/bakeoff --max-usd 4.5

Why a separate command: choosing the judge is an experiment, not part of a scoring run. It reuses the
pipeline (same loading, checks, cache and fusion), so the numbers describe the harness as it really runs.

Cost control, before and during the run:
* Before any paid call it prints a cost estimate per model and refuses to start if the total is above
  ``--max-usd``. Prices come from ``--price model=IN/OUT`` (USD per million tokens) or from the endpoint's
  ``/models`` listing (OpenRouter publishes prices there); a model with no known price is refused.
* While judging, a model stops taking new items once the run's spend reaches ``--max-usd`` (spend = what the
  provider reports, or list price x fresh tokens if that is higher). In-flight items may finish, so the
  overshoot is at most ``--concurrency`` items.
* Bodhan calls for tiers 0-1 are capped by ``--max-cost-inr`` as in a normal run. Everything is cached, so a
  rerun costs nothing and reproduces ``bakeoff.json`` byte for byte.

Judge-alone metrics fuse only the judge's signals; harness metrics fuse everything. Both use the default
thresholds (no tuning), so models are compared on equal terms.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import io
import logging
import math
import sys
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from . import pipeline, report
from .budget import Budget
from .calibrate import evaluate, judge_consistency
from .checks.judge import JudgeCheck, judge_windows, numbered, rubric
from .cli import _json_object, _model_list, _non_negative, _positive_int, _share, parse_langs, setup_logging
from .clients import LLMClient
from .config import Settings, defaults
from .fusion import fuse
from .models import Item, ItemResult
from .normalize import ws_tokens

log = logging.getLogger("bodhan_eval.bakeoff")

TIE_BREAK_SHARE = 0.15  # assumed share of items that need a tie-break replica (LLM_BAKEOFF.md assumed ~10 %)
FINDINGS_CHARS = 300  # allowance for the automatic-findings block in the cost estimate


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    prompt: float
    completion: float

    def usd(self, prompt_tokens: float, completion_tokens: float) -> float:
        return (prompt_tokens * self.prompt + completion_tokens * self.completion) / 1e6


def parse_price(value: str) -> tuple[str, Price]:
    """``model=IN/OUT`` in USD per million tokens, e.g. ``openai/gpt-6-luna=0.4/1.6``."""
    model, sep, rest = value.partition("=")
    parts = rest.split("/")
    try:
        if not sep or not model.strip() or len(parts) != 2:
            raise ValueError
        price = Price(float(parts[0]), float(parts[1]))
    except ValueError as e:
        raise argparse.ArgumentTypeError("--price takes model=IN/OUT in USD per million tokens") from e
    if price.prompt < 0 or price.completion < 0:
        raise argparse.ArgumentTypeError("--price must not be negative")
    return model.strip(), price


def listed_prices(base_url: str, api_key: str | None, timeout: float = 30.0) -> dict[str, Price]:
    """Prices from ``GET {base_url}/models`` in OpenRouter's shape (USD per token as strings). {} if unavailable."""
    try:
        r = requests.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )
        r.raise_for_status()
        rows = r.json().get("data") or []
    except (requests.RequestException, ValueError, AttributeError) as e:
        log.warning("could not read model prices from %s/models: %s", base_url, e)
        return {}
    out = {}
    for m in rows:
        try:
            p = m["pricing"]
            out[str(m["id"])] = Price(float(p["prompt"]) * 1e6, float(p["completion"]) * 1e6)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def token_estimate(text: str) -> int:
    """Rough tokens for a prompt: ~4 characters per token for Latin text, ~2 for Indic scripts."""
    latin = sum(1 for ch in text if ord(ch) < 128)
    return math.ceil(latin / 4 + (len(text) - latin) / 2)


@dataclass
class Estimate:
    model: str
    items: int
    calls: float
    prompt_tokens: float
    completion_tokens: float
    usd: float


def estimate(items: list[Item], model: str, price: Price, cfg: dict, out_tokens: int) -> Estimate:
    """Expected judge calls and tokens: windows x (replicas incl. tie-breaks + repeat sample) per item."""
    system = len(rubric("Hindi"))
    calls = prompt = 0.0
    judged = 0
    per_window = max(int(cfg["replicas"]), 1) * (1 + TIE_BREAK_SHARE) + float(cfg["repeat_sample"])
    for it in items:
        if not it.translation:
            continue
        judged += 1
        s_tok, t_tok = ws_tokens(it.source), ws_tokens(it.translation)
        for w in judge_windows(it.source, it.translation, int(cfg["window_tokens"])):
            src = " ".join(t.text for t in s_tok[w.s0 : w.s1])  # stands in for the back-translation
            text = numbered(s_tok[w.s0 : w.s1]) + numbered(t_tok[w.t0 : w.t1]) + src
            calls += per_window
            prompt += per_window * (token_estimate(text) + system / 4 + FINDINGS_CHARS / 4)
    completion = calls * out_tokens
    return Estimate(model, judged, calls, prompt, completion, price.usd(prompt, completion))


# ---------------------------------------------------------------------------
# scoring one model
# ---------------------------------------------------------------------------


def _fuse(results: list[ItemResult], only_check: str | None) -> None:
    d = defaults()
    for r in results:
        signals = [s for s in r.signals if only_check is None or s.check == only_check]
        r.flags, r.uncertain, _ = fuse(r.item, signals, d["flag_threshold"], d["uncertain_margin"])


def score(model: str, results: list[ItemResult], budget: Budget, price: Price, response_format: str | None) -> dict:
    judged = [r for r in results if r.checks.get("judge", {}).get("status") == "ok"]
    judged_ids = {id(r) for r in judged}
    calls = sum(int(r.metrics.get("judge_calls", 0)) for r in judged)
    invalid = sum(int(r.metrics.get("judge_invalid_outputs", 0)) for r in judged)
    _fuse(results, "judge")
    alone = evaluate(results)
    _fuse(results, None)
    harness = evaluate(results)
    cons = judge_consistency(results)
    tokens = budget.llm_tokens
    n = max(1, len(judged))
    return {
        "model": model,
        "response_format": response_format,
        "items": len(results),
        "judged_items": len(judged),
        "not_judged": sorted(
            f"{r.item.key}: {r.checks.get('judge', {}).get('reason') or r.checks.get('judge', {}).get('status')}"
            for r in results
            if id(r) not in judged_ids
        ),
        "judge_alone": {
            "precision": alone["precision"],
            "recall": alone["recall"],
            "per_category": {
                c: {"precision": v["precision"], "recall": v["recall"], "labels": v["labels"]}
                for c, v in alone["per_category"].items()
            },
        },
        "order_consistency": cons["same_categories_rate"],
        "repeat_consistency": cons["repeat"]["same_categories_rate"],
        "repeat_items": cons["repeat"]["items"],
        "tie_break_rate": cons["tie_break_rate"],
        "invalid_output_rate": round(invalid / calls, 4) if calls else None,
        "judge_calls": calls,
        "tokens_per_item": round((tokens["prompt"] + tokens["completion"]) / n, 1),
        "usd_per_100_items": round(100 * price.usd(tokens["prompt"], tokens["completion"]) / n, 4),
        "harness": {
            "recall": harness["recall"],
            "item_recall": harness["item_recall"],
            "false_alarm_rate": harness["false_alarm_rate"],
            "precision": harness["precision"],
        },
    }


class _Spend:
    """Run-wide USD spend across models, for the --max-usd stop (thread-safe)."""

    def __init__(self, max_usd: float):
        self.max_usd = max_usd
        self.before = 0.0  # settled spend of the models already done
        self._lock = threading.Lock()

    def now(self, budget: Budget, price: Price) -> float:
        fresh = budget.llm_fresh_tokens
        return self.before + max(budget.llm_cost_usd, price.usd(fresh["prompt"], fresh["completion"]))

    def over(self, budget: Budget, price: Price) -> bool:
        with self._lock:
            return self.now(budget, price) >= self.max_usd


def judge_model(
    base: list[ItemResult], ctx: pipeline.RunContext, model: str, price: Price, cfg: dict, spend: _Spend, workers: int
) -> tuple[list[ItemResult], Budget, LLMClient]:
    """Copy the tier 0-1 results and run only the judge with ``model`` on them."""
    results = copy.deepcopy(base)
    budget = Budget(None)
    llm = LLMClient(dataclasses.replace(ctx.llm.s, llm_model=model), ctx.cache, budget, 0.0, ctx.llm.extra_body)
    mctx = dataclasses.replace(ctx, llm=llm, budget=budget, judge_cfg=cfg, options={**ctx.options, "judge": True})
    check = JudgeCheck()

    def work(r: ItemResult) -> None:
        if spend.over(budget, price):
            r.checks["judge"] = {"status": "skipped", "reason": "bake-off budget (--max-usd) reached"}
            return
        r.checks["judge"] = pipeline.run_check(check, r, mctx)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, results))
    spend.before = spend.now(budget, price)
    return results, budget, llm


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.0f} %"


def render_markdown(rows: list[dict], meta: dict) -> str:
    cats = sorted({c for row in rows for c, v in row["judge_alone"]["per_category"].items() if v["labels"]})
    lines = [
        "# Judge bake-off",
        "",
        f"{meta['items']} labelled items ({meta['clean_items']} clean), languages {', '.join(meta['languages'])}; "
        f"replicas {meta['judge']['replicas']} (max {meta['judge']['max_replicas']}), repeat sample "
        f"{meta['judge']['repeat_sample']}. Default thresholds, no tuning. $ at list price.",
        "",
        "| Model | Judge-alone P / R | Order consistency | Repeat consistency | Invalid output | Tokens/item "
        "| $/100 items | Harness recall / false alarms | Format |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        ja, h = row["judge_alone"], row["harness"]
        lines.append(
            f"| `{row['model']}` | {_pct(ja['precision'])} / {_pct(ja['recall'])} | {_pct(row['order_consistency'])} "
            f"| {_pct(row['repeat_consistency'])} (n={row['repeat_items']}) | {_pct(row['invalid_output_rate'])} "
            f"| {row['tokens_per_item']:.0f} | {row['usd_per_100_items']:.3f} "
            f"| {_pct(h['recall'])} / {_pct(h['false_alarm_rate'])} | {row['response_format'] or '—'} |"
        )
    if cats:
        lines += ["", "## Judge alone, per category (precision / recall)", ""]
        lines += ["| Model | " + " | ".join(cats) + " |", "|---|" + "---|" * len(cats)]
        for row in rows:
            pc = row["judge_alone"]["per_category"]
            cells = [f"{_pct(pc.get(c, {}).get('precision'))} / {_pct(pc.get(c, {}).get('recall'))}" for c in cats]
            lines.append(f"| `{row['model']}` | " + " | ".join(cells) + " |")
    skipped = [(row["model"], x) for row in rows for x in row["not_judged"]]
    if skipped:
        lines += ["", "## Items the judge did not score", ""] + [f"- `{m}` {x}" for m, x in skipped[:30]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m bodhan_eval.bakeoff", description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", type=Path, required=True, help="labelled JSONL (labels inline or via --calibration)")
    ap.add_argument("--models", type=_model_list, required=True, help="comma-separated judge models")
    ap.add_argument("--out", type=Path, required=True, help="folder for bakeoff.json / bakeoff.md")
    ap.add_argument("--langs", type=parse_langs, default=None, help="language filter (default: all)")
    ap.add_argument("--calibration", type=Path, help="labels file, as in the main command")
    ap.add_argument("--replicas", type=_positive_int, help="judge replicas per item (default from defaults.json)")
    ap.add_argument("--max-replicas", type=_positive_int, help="cap incl. tie-breaks (default from defaults.json)")
    ap.add_argument("--repeat-sample", type=_share, help="repeat-consistency share (default from defaults.json)")
    ap.add_argument("--max-usd", type=_non_negative, required=True, help="refuse or stop above this LLM spend")
    ap.add_argument("--price", type=parse_price, action="append", default=[], metavar="MODEL=IN/OUT")
    ap.add_argument("--est-output-tokens", type=_positive_int, default=500, help="output tokens per call, estimate")
    ap.add_argument("--llm-extra-body", type=_json_object, metavar="JSON", help="merged into every LLM request")
    ap.add_argument("--max-cost-inr", type=_non_negative, default=5.0, help="Bodhan spend cap for tiers 0-1")
    ap.add_argument("--cache-dir", type=Path, default=Path(".cache/bodhan_eval"))
    ap.add_argument("--concurrency", type=_positive_int, default=6)
    ap.add_argument("--dry-run", action="store_true", help="print the estimate and stop")
    ap.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    return ap


def judge_config(args: argparse.Namespace) -> dict[str, Any]:
    cfg = dict(defaults()["judge"])
    for key, value in (("replicas", args.replicas), ("max_replicas", args.max_replicas)):
        if value is not None:
            cfg[key] = value
    if args.repeat_sample is not None:
        cfg["repeat_sample"] = args.repeat_sample
    cfg["models"] = []  # one model at a time; a jury is a separate experiment
    return cfg


def _print(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()


def _estimate_table(estimates: Iterable[Estimate], prices: dict[str, Price]) -> str:
    lines = [
        "| Model | Items | Calls | Prompt tok | Output tok | $ in/out per 1M | Estimate $ |",
        "|---|---:|---:|---:|---:|---|---:|",
    ]
    total = 0.0
    for e in estimates:
        p = prices[e.model]
        total += e.usd
        lines.append(
            f"| `{e.model}` | {e.items} | {e.calls:.0f} | {e.prompt_tokens:,.0f} | {e.completion_tokens:,.0f} "
            f"| {p.prompt:g} / {p.completion:g} | {e.usd:.3f} |"
        )
    lines.append(f"| **total** | | | | | | **{total:.3f}** |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")
    setup_logging(args.log_level)
    settings = Settings.from_env()
    if not (settings.llm_base_url and settings.llm_api_key):
        log.error("LLM_BASE_URL and LLM_API_KEY must be set (LLM_MODEL is not needed: --models names the judges)")
        return 1
    opts = pipeline.RunOptions(
        dataset=args.dataset,
        out=args.out,
        langs=args.langs,
        calibration=args.calibration,
        max_cost_inr=args.max_cost_inr,
        cache_dir=args.cache_dir,
        concurrency=args.concurrency,
        judge=False,
        speech=False,
        llm_extra_body=args.llm_extra_body,
    )
    try:
        items, rep = pipeline._load(opts)
    except (OSError, UnicodeDecodeError) as e:
        log.error("cannot read dataset %s: %s", args.dataset, e)
        return 1
    items = [it for it in items if it.labels is not None and it.translation]
    if not items:
        log.error("no labelled items with a translation in %s: the bake-off needs labels to score judges", args.dataset)
        return 1

    prices = dict(args.price)
    missing = [m for m in args.models if m not in prices]
    if missing:
        listed = listed_prices(settings.llm_base_url, settings.llm_api_key, settings.request_timeout_s)
        prices.update({m: listed[m] for m in missing if m in listed})
    unknown = [m for m in args.models if m not in prices]
    if unknown:
        log.error("no price for %s: pass --price MODEL=IN/OUT (USD per million tokens)", ", ".join(unknown))
        return 2
    cfg = judge_config(args)
    estimates = [estimate(items, m, prices[m], cfg, args.est_output_tokens) for m in args.models]
    total = sum(e.usd for e in estimates)
    _print(_estimate_table(estimates, prices))
    if total > args.max_usd:
        log.error("estimated $%.3f is above --max-usd %.3f: not starting", total, args.max_usd)
        return 2
    if args.dry_run:
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    ctx = pipeline._context(opts)
    try:
        base = pipeline.execute(items, rep, ctx, opts).results  # tiers 0-1 once (the judge is off here)
        spend = _Spend(args.max_usd)
        rows, spent = [], {}
        for m in args.models:
            log.info("judging %d items with %s", len(base), m)
            results, budget, llm = judge_model(base, ctx, m, prices[m], cfg, spend, args.concurrency)
            rows.append(score(m, results, budget, prices[m], llm.format_for(m)))
            spent[m] = {
                "provider_reported_usd": round(budget.llm_cost_usd, 6),
                "list_price_usd": round(
                    prices[m].usd(budget.llm_fresh_tokens["prompt"], budget.llm_fresh_tokens["completion"]), 6
                ),
                "fresh_calls": budget.calls.get("llm", 0),
                "cache_hits": budget.cache_hits.get("llm", 0),
            }
    finally:
        ctx.events.close()
        ctx.cache.close()
    meta = {
        "items": len(items),
        "clean_items": sum(1 for it in items if not it.labels),
        "languages": sorted({it.lang for it in items}),
        "judge": {k: cfg[k] for k in ("replicas", "max_replicas", "repeat_sample", "max_tokens", "window_tokens")},
        "prices_usd_per_million": {m: dataclasses.asdict(prices[m]) for m in args.models},
        "llm_extra_body": args.llm_extra_body or {},
    }
    report.write_json(args.out / "bakeoff.json", {"meta": meta, "models": rows})
    report.write_json(args.out / "bakeoff_spend.json", {"bodhan_spent_inr": ctx.budget.total_spent, "llm": spent})
    md = render_markdown(rows, meta)
    (args.out / "bakeoff.md").write_text(md, encoding="utf-8", newline="\n")
    _print(md)
    _print(
        f"Spent now: Bodhan ₹{ctx.budget.total_spent:.4f}; LLM "
        + ", ".join(f"{m} ${v['provider_reported_usd'] or v['list_price_usd']:.4f}" for m, v in spent.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
