"""python -m bodhan_dub --input video.mp4 --langs hi,ta,bn --out ./out [--glossary glossary.yaml]
[--voices voices.yaml] [--code-mixed]"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_env
from .glossary import load_glossary
from .pipeline import run


def _voices(arg: str | None) -> dict:
    if not arg:
        return {}
    if arg.lower().endswith((".yaml", ".yml")):
        import yaml

        return {str(k): str(v) for k, v in (yaml.safe_load(Path(arg).read_text(encoding="utf-8")) or {}).items()}
    return dict(p.split("=", 1) for p in arg.split(",") if "=" in p)


def main() -> int:
    ap = argparse.ArgumentParser(prog="bodhan_dub", description="Dub an English video into Indian languages.")
    ap.add_argument("--input", required=True, help="source video")
    ap.add_argument("--langs", required=True, help="comma-separated language codes, e.g. hi,ta,bn")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--glossary", help="glossary.yaml: English term -> rendering per language (checked, "
                    "retried once, reported)")
    ap.add_argument("--voices", help="voices.yaml, or lang=Voice pairs (hi=Kavya,ta=Anitha)")
    ap.add_argument("--code-mixed", action="store_true",
                    help="code-mixed translation: English technical words stay in English")
    ap.add_argument("--style", default="educational lecture", help="speaking style for Indic-Speak")
    ap.add_argument("--max-duration", type=float, help="dub only the first N seconds (tests, previews)")
    ap.add_argument("--separation", choices=["auto", "on", "off"], default="auto",
                    help="voice/background separation: auto = only when there is music or noise under the voice")
    ap.add_argument("--sync-rounds", type=int, default=2, help="closed-loop sync correction rounds (0 = off)")
    ap.add_argument("--no-qa", action="store_true", help="skip back-transcription QA")
    ap.add_argument("--no-align", action="store_true",
                    help="skip pause-level alignment (sentence breaks inside a chunk are then estimated)")
    ap.add_argument("--lang-workers", type=int, default=3,
                    help="languages translated and dubbed at the same time (1 = one after another)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    load_env()
    try:
        glossary = load_glossary(a.glossary) if a.glossary else None
    except FileNotFoundError as e:
        ap.error(str(e))          # exits with status 2, before any paid call
    rep = run(a.input, [x.strip() for x in a.langs.split(",") if x.strip()], a.out, voices=_voices(a.voices),
              style=a.style, max_duration=a.max_duration, separation=a.separation, sync_rounds=a.sync_rounds,
              qa=not a.no_qa, align=not a.no_align, lang_workers=a.lang_workers, glossary=glossary,
              code_mixed=a.code_mixed)
    failed = [lang for lang, r in rep["per_language"].items() if r.get("status") == "failed"]
    print(json.dumps({"report": str(Path(a.out) / "report.json"), "log": str(Path(a.out) / rep.get("log", "run.log.jsonl")),
                      "seconds": rep["seconds_total"],
                      "failed_languages": failed, "quality_targets": rep["quality_targets"]},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
