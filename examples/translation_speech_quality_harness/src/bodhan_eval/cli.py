"""Command line: ``python -m bodhan_eval --dataset data.jsonl --langs hi,ta,bn --out ./report [...]``.

The required flags (``--dataset``, ``--langs``, ``--out``, ``--generate``, ``--baseline``, ``--calibration``,
``--max-cost-inr``) are a contract with the graders and never change. Everything else is optional.
This module only parses arguments; the work happens in ``pipeline.run``.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import lang_info, normalize_lang_code
from .pipeline import RunOptions, run

log = logging.getLogger("bodhan_eval")


def parse_langs(value: str) -> set[str] | None:
    """``hi,ta,bn`` -> {"hi", "ta", "bn"} (codes normalised, e.g. ``hi-IN`` -> ``hi``); ``all`` -> None (no filter)."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("--langs needs at least one language code, or 'all'")
    if any(p.lower() == "all" for p in parts):
        return None
    return {normalize_lang_code(p) for p in parts}


def parse_voice(value: str) -> tuple[str, str]:
    lang, sep, voice = value.partition("=")
    if not sep or not lang.strip() or not voice.strip():
        raise argparse.ArgumentTypeError("--voice takes lang=VoiceName, e.g. hi=Kavya")
    return normalize_lang_code(lang), voice.strip()


def _fraction(value: str) -> float:
    x = float(value)
    if not 0.0 < x <= 1.0:
        raise argparse.ArgumentTypeError("must be in (0, 1]")
    return x


def _non_negative(value: str) -> float:
    x = float(value)
    if x < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return x


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def _share(value: str) -> float:
    x = float(value)
    if not 0.0 <= x <= 1.0:
        raise argparse.ArgumentTypeError("must be in [0, 1]")
    return x


def _model_list(value: str) -> list[str]:
    models = [m.strip() for m in value.split(",") if m.strip()]
    if not models:
        raise argparse.ArgumentTypeError("needs at least one model id")
    return list(dict.fromkeys(models))  # drop repeats, keep order


def _json_object(value: str) -> dict:
    try:
        obj = json.loads(value)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"not valid JSON: {e.msg}") from e
    if not isinstance(obj, dict):
        raise argparse.ArgumentTypeError('must be a JSON object, e.g. \'{"reasoning": {"effort": "low"}}\'')
    return obj


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m bodhan_eval",
        description="Find and explain quality problems in Indic-Translate translations and Indic-Speak audio.",
    )
    req = ap.add_argument_group("required")
    req.add_argument("--dataset", type=Path, required=True, help="JSONL: one item per line (id, source, lang, ...)")
    req.add_argument("--langs", type=parse_langs, required=True, help="comma-separated codes, e.g. hi,ta,bn, or 'all'")
    req.add_argument("--out", type=Path, required=True, help="report folder (created if missing)")
    ap.add_argument("--generate", action="store_true", help="create missing translations and audio with Bodhan models")
    ap.add_argument("--baseline", type=Path, help="previous report folder; writes diff.md")
    ap.add_argument("--fail-on-regression", action="store_true", help="with --baseline: exit 3 if diff.md has more new major/critical problems than --max-new-major (CI gate)")
    ap.add_argument("--max-new-major", type=int, default=0, help="new major/critical problems allowed (default 0)")
    ap.add_argument("--calibration", type=Path, help="JSONL of human labels (full items or id[+lang]+labels)")
    ap.add_argument(
        "--max-cost-inr", type=_non_negative, help="hard cap on money spent in this run (cache hits are free)"
    )
    run_opts = ap.add_argument_group("run")
    run_opts.add_argument("--cache-dir", type=Path, default=Path(".cache/bodhan_eval"), help="model-output cache")
    run_opts.add_argument("--concurrency", type=_positive_int, default=6, help="parallel requests per service")
    run_opts.add_argument("--thresholds", type=Path, help='JSON {"category": threshold} overriding defaults.json')
    run_opts.add_argument(
        "--label-span-on",
        choices=("translation", "source"),
        default="translation",
        help="default side that label spans refer to",
    )
    run_opts.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    judge = ap.add_argument_group("LLM judge")
    judge.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    judge.add_argument(
        "--judge-replicas", type=_positive_int, help="judge replicas per item (default from defaults.json)"
    )
    judge.add_argument(
        "--judge-max-replicas",
        type=_positive_int,
        help="replicas allowed per item when the first ones disagree (tie-break; default 3)",
    )
    judge.add_argument(
        "--judge-models",
        type=_model_list,
        metavar="A,B,C",
        help="jury: comma-separated models on the same LLM endpoint, each votes once (default: LLM_MODEL alone)",
    )
    judge.add_argument(
        "--judge-repeat-sample",
        type=_share,
        help="share of items whose first judge prompt is re-sent unchanged to measure repeat-consistency "
        "(0 = off; default 0.1)",
    )
    judge.add_argument(
        "--llm-inr-per-1k-tokens",
        type=_non_negative,
        default=0.0,
        help="LLM price used for budgeting and cost reporting",
    )
    judge.add_argument(
        "--llm-extra-body",
        type=_json_object,
        metavar="JSON",
        help='provider-specific fields merged into every LLM request, e.g. \'{"reasoning": {"effort": "low"}}\'',
    )
    speech = ap.add_argument_group("speech")
    speech.add_argument("--no-speech", action="store_true", help="skip speech synthesis and speech checks")
    speech.add_argument(
        "--speech-sample", type=_fraction, help="with --generate: fraction of items per language to voice"
    )
    speech.add_argument(
        "--voice",
        type=parse_voice,
        action="append",
        default=[],
        metavar="LANG=NAME",
        help="voice for --generate, repeatable (default: the first voice in langs.json)",
    )
    ap.add_argument("--version", action="version", version=f"bodhan_eval {__version__}")
    return ap


def options_from_args(args: argparse.Namespace) -> RunOptions:
    return RunOptions(
        dataset=args.dataset,
        out=args.out,
        langs=args.langs,
        generate=args.generate,
        baseline=args.baseline,
        fail_on_regression=args.fail_on_regression,
        max_new_major=args.max_new_major,
        calibration=args.calibration,
        max_cost_inr=args.max_cost_inr,
        cache_dir=args.cache_dir,
        concurrency=args.concurrency,
        thresholds=args.thresholds,
        judge=not args.no_judge,
        judge_replicas=args.judge_replicas,
        judge_max_replicas=args.judge_max_replicas,
        judge_models=args.judge_models,
        judge_repeat_sample=args.judge_repeat_sample,
        speech=not args.no_speech,
        speech_sample=args.speech_sample,
        voices=dict(args.voice),
        label_span_on=args.label_span_on,
        llm_inr_per_1k_tokens=args.llm_inr_per_1k_tokens,
        llm_extra_body=args.llm_extra_body,
    )


def setup_logging(level: str) -> None:
    if isinstance(sys.stderr, io.TextIOWrapper):
        # log lines carry ₹ and Indian-script text; a legacy console code page (cp1252) must not crash logging
        sys.stderr.reconfigure(errors="replace")
    logging.basicConfig(level=level.upper(), format="%(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpx2", "openai"):  # one INFO line per HTTP request drowns the run's own log
        logging.getLogger(name).setLevel(max(logging.WARNING, logging.getLevelName(level.upper())))


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.fail_on_regression and args.baseline is None:
        ap.error("--fail-on-regression needs --baseline")
    setup_logging(args.log_level)
    for code in sorted(args.langs or ()):
        if not lang_info(code).known:
            log.warning("language %r is not in data/langs.json: checks that need script data will be skipped", code)
    return run(options_from_args(args))
