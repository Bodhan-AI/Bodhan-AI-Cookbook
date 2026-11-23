"""Command-line entry point: python -m bodhan_eval --dataset ... --langs ... --out ..."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import sys
import threading
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .calibrate import apply, calibrate, load_labels, write
from .clients import Clients
from .config import Config, env_settings, load_dotenv
from .dataset import load_dataset, normalize_lang
from .fuse import load_thresholds
from .generate import MAX_SPEECH_ITEMS, generate, generate_audio, mark_supplied, status_counts
from .pipeline import run_pipeline, summarize
from .report import write_reports

log = logging.getLogger("bodhan_eval")

# Outside --out, so deleting a report never deletes paid results.
CACHE_DIR = Path(".bodhan_cache")


def _langs(value: str) -> tuple[str, ...]:
    # No fixed list of languages, so ones we never demoed still pass.
    codes = [normalize_lang(c) for c in value.split(",") if c.strip()]
    langs = tuple(dict.fromkeys(c for c in codes if c))
    if not codes or len(langs) != len(set(codes)):
        raise argparse.ArgumentTypeError(f"expected comma-separated language codes like hi,ta,bn, got {value!r}")
    return langs


def _file(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file not found: {value}")
    return path


def _dir(value: str) -> Path:
    path = Path(value)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"directory not found: {value}")
    return path


def _cost(value: str) -> float:
    cost = float(value)
    if cost < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return cost


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bodhan_eval",
        description="Score Indic-Translate translations and Indic-Speak audio, flag errors with evidence, "
        "and report what changed against a baseline run.",
    )
    p.add_argument("--dataset", required=True, type=_file, help="JSONL dataset, one item per line (id, source, lang required)")
    p.add_argument("--langs", required=True, type=_langs, help="comma-separated language codes to score, e.g. hi,ta,bn")
    p.add_argument("--out", required=True, type=Path, help="report directory, created if missing")
    p.add_argument("--generate", action="store_true", help="create missing translations and audio with Bodhan models")
    p.add_argument("--baseline", type=_dir, help="previous report directory to diff against")
    p.add_argument("--calibration", type=_file, nargs="+", default=[],
                   help="labelled JSONL file(s) used to measure and tune the fusion weights and flag threshold")
    p.add_argument("--thresholds", type=_file, help="fusion weights and thresholds (default: built-in design defaults; "
                   "a thresholds.tuned.json from an earlier --calibration run reuses that calibration)")
    p.add_argument("--max-cost-inr", type=_cost, help="stop paid Bodhan calls once this much is spent (default: no cap)")
    p.add_argument("--skip-speech", action="store_true", help="no audio generation and no speech checks (saves the costly step)")
    p.add_argument("--max-speech-items", type=int, default=MAX_SPEECH_ITEMS,
                   help=f"with --generate, voice at most this many items, riskiest and random alternately "
                        f"(default {MAX_SPEECH_ITEMS}; -1 = as many as --max-cost-inr allows). Supplied audio is always checked")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def parse_config(argv: list[str] | None = None, env: dict[str, str] | None = None) -> Config:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.baseline and args.baseline.resolve() == args.out.resolve():
        parser.error("--baseline and --out must be different directories")
    return Config(
        dataset=args.dataset,
        langs=args.langs,
        out=args.out,
        generate=args.generate,
        baseline=args.baseline,
        calibration=tuple(args.calibration),
        thresholds=args.thresholds,
        max_cost_inr=args.max_cost_inr,
        skip_speech=args.skip_speech,
        max_speech_items=None if args.max_speech_items < 0 else args.max_speech_items,
        log_level=args.log_level,
        **env_settings(os.environ if env is None else env),
    )


def _version() -> str:
    try:
        return version("bodhan-eval")
    except PackageNotFoundError:
        return "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_metadata(cfg: Config, argv: list[str]) -> dict:
    """Facts about this run. Kept out of the report files so cached reruns stay byte-identical.

    Never contains secret values: only whether each credential was present.
    """
    return {
        "harness_version": _version(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "argv": argv,
        "args": {
            "dataset": str(cfg.dataset),
            "dataset_sha256": _sha256(cfg.dataset),
            "langs": list(cfg.langs),
            "out": str(cfg.out),
            "generate": cfg.generate,
            "baseline": str(cfg.baseline) if cfg.baseline else None,
            "calibration": [str(p) for p in cfg.calibration],
            "thresholds": str(cfg.thresholds) if cfg.thresholds else None,
            "max_cost_inr": cfg.max_cost_inr,
            "skip_speech": cfg.skip_speech,
            "max_speech_items": cfg.max_speech_items,
        },
        "endpoints": {
            "bodhan_base_url": cfg.bodhan_base_url,
            "llm_base_url": cfg.llm_base_url,
            "llm_model": cfg.llm_model,
        },
        "credentials_present": {
            **{m: cfg.has_key(m) for m in ("translate", "speak", "transcribe")},
            "llm": cfg.llm_enabled,
        },
        "warnings": cfg.warnings(),
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    load_dotenv()
    cfg = parse_config(argv)
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    started = time.monotonic()
    meta = run_metadata(cfg, argv)
    meta["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for warning in meta["warnings"]:
        log.warning(warning)

    cfg.out.mkdir(parents=True, exist_ok=True)
    log.info("scoring %s for langs=%s into %s", cfg.dataset, ",".join(cfg.langs), cfg.out)
    data = load_dataset(cfg.dataset, cfg.langs)
    meta["dataset"] = {
        "items": len(data.items),
        "skipped": len(data.skipped),
        "other_langs": data.filtered_out,
        "items_with_warnings": sum(1 for i in data.items if i.warnings),
    }
    log.info(
        "loaded %d items (%d bad lines skipped, %d in other languages)",
        len(data.items), len(data.skipped), data.filtered_out,
    )
    if not data.items:
        log.warning("no items to score for langs=%s", ",".join(cfg.langs))
    mark_supplied(data.items)
    clients = Clients(cfg, CACHE_DIR)
    try:
        audio = None
        if cfg.generate:
            generate(data.items, clients, cfg.out / "audio", cfg.max_cost_inr, speech=False)  # translations first
            if not cfg.skip_speech:  # speech synthesis is slow: run it alongside the text checks
                audio = threading.Thread(target=generate_audio, daemon=True, args=(
                    data.items, clients, cfg.out / "audio", cfg.max_cost_inr, cfg.max_speech_items))
                audio.start()
        thresholds = load_thresholds(cfg.thresholds)
        records = run_pipeline(data.items, clients, thresholds, speech=not cfg.skip_speech,
                               audio_ready=audio.join if audio else None)
        if cfg.generate:
            meta["generation"] = status_counts(data.items)
            log.info("generation: %s", meta["generation"])
        # Labels from --calibration, else any the dataset carries inline (items with a "labels" field).
        labels = load_labels(cfg.calibration or [cfg.dataset])
        if labels:
            if not cfg.calibration:
                log.info("calibrating from %d labelled items in the dataset itself", len(labels))
            # The tuned weights and threshold replace the defaults for this run's flags too.
            report, tuned = calibrate(records, labels, thresholds)
            apply(records, tuned)
            write(cfg.out, report, tuned)
            meta["calibration"] = {**report["labelled_items"], **report["thresholds"]["tuned"]}
            log.info("calibration: %s", meta["calibration"])
        with (cfg.out / "items.jsonl").open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        meta["results"] = summarize(records)
        for lang, n in meta["results"].items():
            log.info("%s: %d items, %d scored, %d with candidates, %d flagged, %d uncertain",
                     lang, n["items"], n["scored"], n["with_candidates"], n["flagged"], n["uncertain"])
    finally:
        meta["cost"] = clients.ledger.summary()
        clients.close()

    meta["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta["duration_s"] = round(time.monotonic() - started, 3)
    meta["status"] = "ok"
    (cfg.out / "run.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_reports(cfg.out, cfg.baseline)
    log.info("wrote %s", ", ".join(str(cfg.out / f) for f in ("summary.md", "diff.md")[: 2 if cfg.baseline else 1]))
    return 0
