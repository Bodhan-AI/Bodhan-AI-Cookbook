"""python -m bodhan_dub --input video.mp4 --langs hi,ta,bn --out ./out [--glossary g.yaml] [--voices v.yaml] [--code-mixed]"""
import argparse
import sys
from pathlib import Path

from . import config, logs
from .bodhan import BodhanClient
from .llm import Brain
from .pipeline import Options, run_pipeline


def load_dotenv():
    for p in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if p.exists():
            import os
            for line in p.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="bodhan_dub", description="Dub an English video into Indian languages (AI-dubbed).")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--langs", required=True, help="comma separated codes, e.g. hi,ta,bn")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--glossary", type=Path)
    ap.add_argument("--voices", type=Path)
    ap.add_argument("--code-mixed", action="store_true", help="Hinglish-style code-mixed output where supported")
    ap.add_argument("--concurrency", type=int, default=4, help="max parallel Bodhan API calls")
    ap.add_argument("--style", default=None, help="Indic-Speak style: " + "; ".join(config.STYLES))
    ap.add_argument("--ocr", action="store_true", help="translate on-screen text (slides, captions) with Indic-OCR; needs BODHAN_OCR_KEY")
    ap.add_argument("--ocr-interval", type=float, default=2.0, help="seconds between sampled frames for --ocr")
    ap.add_argument("--ocr-max-frames", type=int, default=40, help="upper bound on frames (Indic-OCR calls) for --ocr")
    ap.add_argument("--force", action="store_true", help="ignore cached English analysis")
    a = ap.parse_args(argv)

    langs = [x.strip().lower() for x in a.langs.split(",") if x.strip()]
    langs = list(dict.fromkeys(langs))   # drop duplicates, keep order
    unknown = [x for x in langs if x not in config.LANGUAGES]
    if unknown:
        print(f"Unknown language code(s): {unknown}. Supported: {', '.join(config.LANGUAGES)}", file=sys.stderr)
        return 2
    if not a.input.exists():
        print(f"Input not found: {a.input}", file=sys.stderr)
        return 2
    keys = config.Keys.from_env()
    if keys.missing():
        print(f"Missing environment variables: {', '.join(keys.missing())}", file=sys.stderr)
        return 2
    if a.ocr and not keys.ocr:
        print("Note: --ocr needs BODHAN_OCR_KEY (an Indic-OCR key); on-screen text will be skipped, the dub continues.", file=sys.stderr)
    a.out.mkdir(parents=True, exist_ok=True)
    logs.setup(a.out / "run.log.jsonl")
    client = BodhanClient(keys, a.out / "work" / "cache", max_concurrency=a.concurrency)
    opt = Options(input=a.input, langs=langs, out=a.out, glossary=a.glossary, voices=a.voices,
                  code_mixed=a.code_mixed, concurrency=a.concurrency, style=a.style, force=a.force,
                  ocr=a.ocr, ocr_interval=a.ocr_interval, ocr_max_frames=a.ocr_max_frames)
    return run_pipeline(opt, client, Brain())


if __name__ == "__main__":
    sys.exit(main())
