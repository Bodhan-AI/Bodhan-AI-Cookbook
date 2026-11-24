"""Command line: python -m bodhan_dub --input video.mp4 --langs hi,ta,bn --out ./out"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from . import audio, report, speech, transcript, translate, voices
from .assemble import assemble, english_srt, keep_original
from .bodhan import Bodhan
from .llm import LLM

log = logging.getLogger("bodhan_dub")

# Indic-Speak's production-tier languages: twelve Indian languages, plus English (re-voiced, not translated).
LANGS = ["hi", "bn", "mr", "te", "ta", "gu", "kn", "ml", "or", "pa", "as", "ur", "en"]


def main() -> int:
    parser = argparse.ArgumentParser(prog="bodhan_dub", description="Dub an English video into Indian languages.")
    parser.add_argument("--input", required=True, type=Path, help="MP4, MOV, MKV or WebM video")
    parser.add_argument("--langs", required=True, help="comma-separated target languages, e.g. hi,ta,bn")
    parser.add_argument("--out", required=True, type=Path, help="output folder")
    parser.add_argument("--glossary", type=Path, help="YAML: 'keep' terms stay as written, 'terms' are spelled consistently")
    parser.add_argument("--voices", type=Path, help="YAML: per language a voice name, 'male'/'female', or {female:, male:}")
    parser.add_argument("--code-mixed", action="store_true", help="keep common English words, e.g. Hinglish for tech talks")
    parser.add_argument("--separate", choices=["auto", "on", "off"], default="auto",
                        help="separate voice from music with Demucs to keep the music (auto: only if there is background audio)")
    parser.add_argument("--max-minutes", type=float, default=15.5,
                        help="refuse longer videos before any paid call (inputs are 1-15 minutes; the slack covers rounding)")
    args = parser.parse_args()

    langs = [code.strip() for code in args.langs.split(",") if code.strip()]
    unknown = [code for code in langs if code not in LANGS]
    if unknown or not langs:
        parser.error(f"unsupported language(s) {unknown}; choose from {','.join(LANGS)}")
    if not args.input.exists():
        parser.error(f"input not found: {args.input}")
    try:
        user_terms = translate.load_glossary(args.glossary) if args.glossary else []
        overrides = voices.load_overrides(args.voices) if args.voices else {}
    except (OSError, ValueError) as e:
        parser.error(str(e))

    load_dotenv()
    args.out.mkdir(parents=True, exist_ok=True)
    _setup_logging(args.out / "run.jsonl")

    started = time.monotonic()
    run = {"input": str(args.input), "langs": langs, "llm_model": os.environ.get("LLM_MODEL"),
           "code_mixed": args.code_mixed, "latency_s": {}, "languages": {}}
    bodhan = Bodhan(cache_dir=args.out / ".cache")
    llm = LLM(cache_dir=args.out / ".cache")
    children = {}
    try:
        seconds = audio.duration(args.input)
        if seconds > args.max_minutes * 60:
            raise ValueError(f"the video is {seconds / 60:.1f} minutes long; the limit is {args.max_minutes:g} "
                             "(raise it with --max-minutes)")
        t = time.monotonic()
        heard = transcript.transcribe(args.input, args.out, bodhan, args.separate)
        run["latency_s"]["transcribe"] = round(time.monotonic() - t, 1)
        t = time.monotonic()
        units, glossary = transcript.plan(heard.segments, args.out, llm, user_terms)
        genders, run["speakers"] = voices.speaker_genders(units, heard.wav)
        run["latency_s"]["plan"] = round(time.monotonic() - t, 1)
        english_srt(units, args.out / "subtitles.en.srt")
        run.update({"input_duration_s": round(heard.total, 3), "separated_background": heard.background is not None,
                    "segments": len(heard.segments), "units": len(units), "skipped_regions": heard.skipped})

        def dub(lang: str) -> dict:
            children[lang] = child = bodhan.child()
            return _dub_language(lang, units, glossary, genders, heard, args, overrides, child, llm)

        with ThreadPoolExecutor(len(langs)) as pool:  # languages in parallel; rate limits are shared
            run["languages"] = dict(zip(langs, pool.map(dub, langs), strict=True))
    except Exception as e:  # still write the report, with whatever was finished
        log.exception("run failed")
        run["error"] = f"{type(e).__name__}: {e}"
    finally:
        everyone = [bodhan, *children.values()]
        run["latency_s"]["total"] = round(time.monotonic() - started, 1)
        run["status"] = {lang: run["languages"].get(lang, {}).get("status", "failed: the run stopped before it") for lang in langs}
        run["calls"] = {k: sum(b.calls.get(k, 0) for b in everyone) for k in bodhan.calls} | {"llm": llm.calls}
        run["llm"] = llm.summary()  # available=false: the run used its fallbacks (see run.jsonl for why)
        run["cost_inr"] = {k: round(sum(b.cost[k] for b in everyone), 4) for k in bodhan.cost}
        run["cost_inr"]["total"] = round(sum(run["cost_inr"].values()), 4)
        if run.get("input_duration_s"):
            minutes = run["input_duration_s"] / 60
            run["per_minute_per_language"] = {
                "cost_inr": round(run["cost_inr"]["total"] / minutes / len(langs), 3),
                "runtime_s": round(run["latency_s"]["total"] / minutes / len(langs), 1),
            }
            # The brief's target, 30 minutes for 5 minutes of video in 3 languages, scaled to this run.
            budget = 30 * 60 * (minutes / 5) * (len(langs) / 3)
            run["targets"] = {"runtime": {
                "target": "a 5-minute video in 3 languages in under 30 minutes (scaled to this run)",
                "budget_s": round(budget, 1), "total_s": run["latency_s"]["total"],
                "met": run["latency_s"]["total"] <= budget,
                "note": "a rerun that hits the cache is faster than a cold run",
            }}
        (args.out / "report.json").write_text(json.dumps(run, indent=2, ensure_ascii=False))
    log.info("done in %.0fs: %s", run["latency_s"]["total"], run["status"])
    return 0 if all(status == "ok" for status in run["status"].values()) else 1


def _dub_language(lang, units, glossary, genders, heard, args, overrides, bodhan, llm) -> dict:
    """Translate, speak and assemble one language. Never raises: a failure becomes its status."""
    folder = args.out / lang
    folder.mkdir(exist_ok=True)
    timings, rows = {}, []
    script = "codemix" if args.code_mixed else "native"
    try:
        if not units:  # no speech anywhere: nothing to dub, and silence must stay silence
            output = keep_original(lang, args.input, heard.total, folder)
            (folder / f"segments.{lang}.json").write_text("[]")
            return {"status": "ok", "note": "no speech found; the video is kept as it is", "outputs": output,
                    **report.language_summary([], output, timings, bodhan)}
        t = time.monotonic()
        rows = translate.translate(units, glossary, lang, bodhan, script)
        timings["translate"] = round(time.monotonic() - t, 1)

        t = time.monotonic()
        unit_voices = [voices.voice_for(lang, g, overrides) for g in genders]
        rows = speech.speak(rows, unit_voices, lang, heard.total, folder, bodhan, llm, script,
                            translate.kept_terms(glossary))
        timings["speech"] = round(time.monotonic() - t, 1)
        (folder / f"segments.{lang}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        if not any("clip" in r for r in rows):
            raise RuntimeError("no unit could be dubbed")

        t = time.monotonic()
        regions = [(s["start"], s["end"]) for s in heard.segments]
        output = assemble(rows, lang, args.input, heard.total, regions, folder, heard.background)
        timings["assemble"] = round(time.monotonic() - t, 1)
        return {"status": "ok", "outputs": output, **report.language_summary(rows, output, timings, bodhan)}
    except Exception as e:  # one language failing must not stop the others
        log.exception("[%s] language failed", lang, extra={"lang": lang})
        if rows:
            (folder / f"segments.{lang}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        return {"status": f"failed: {type(e).__name__}: {e}", "latency_s": timings,
                "calls": dict(bodhan.calls), "cost_inr": {k: round(v, 4) for k, v in bodhan.cost.items()}}


class _JsonLines(logging.Formatter):
    """One JSON object per log line: time, level, stage, message, and any lang/unit/data given as extra."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {"time": round(record.created, 3), "level": record.levelname,
                 "stage": record.name.rsplit(".", 1)[-1], "msg": record.getMessage()}
        for key in ("lang", "unit", "data"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging(path: Path) -> None:
    """Readable lines on the console, structured JSON lines in out/run.jsonl."""
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    jsonl = logging.FileHandler(path, mode="w", encoding="utf-8")
    jsonl.setFormatter(_JsonLines())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [console, jsonl]
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


if __name__ == "__main__":
    sys.exit(main())
