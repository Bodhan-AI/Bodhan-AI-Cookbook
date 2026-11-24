"""Build a demo set from real Bodhan outputs: indic-translate translations, indic-speak audio, then injected errors.

    python scripts/build_bodhan_demo.py --input data/flores_inputs.jsonl --out data/demo.jsonl --max-cost-inr 3

Only `id`, `source`, `lang` and provenance metadata are read from the input. Roughly half of the items get one error:
translation errors through the programmatic injector (or, with --retranslate, by perturbing the
English and re-translating), speech errors by editing the synthesized audio. Audio is written
under --audio-dir. Only a balanced subset of short sentences gets audio;
half of that subset is reserved for clean speech controls. A .build.json sidecar records cost.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
from pathlib import Path

import httpx

from bodhan_eval.audio.io import encode_wav
from bodhan_eval.budget import BudgetExceeded, CostTracker
from bodhan_eval.cache import Cache, atomic_write_bytes, atomic_write_text
from bodhan_eval.clients.bodhan import BodhanClient
from bodhan_eval.clients.http import ServiceError
from bodhan_eval.config import Settings, load_dotenv_if_present
from bodhan_eval.generate import pick_voice, synthesize
from bodhan_eval.inject.audio_ops import AUDIO_OPS
from bodhan_eval.inject.injector import inject_text, item_rng, retranslate
from bodhan_eval.languages import get_language
from bodhan_eval.text.segment import sentences
from bodhan_eval.text.speakable import to_speakable

SPEECH_CATEGORIES = list(AUDIO_OPS)


def audio_subset(rows: list[dict], args: argparse.Namespace) -> set[tuple[str, str]]:
    selected = set()
    counts: dict[str, int] = {}
    for row in sorted(rows, key=lambda r: item_rng(args.seed, r["id"], r["lang"]).random()):
        source, text = row["source"], to_speakable(row["translation"]).text
        content = row.get("meta", {}).get("content", "sentence")
        if (content != "sentence" or "\n" in source or len(source) > args.audio_max_source_chars
                or len(sentences(source)) != 1 or not text or len(text) > args.audio_max_chars):
            continue
        lang = row["lang"]
        if counts.get(lang, 0) < args.audio_per_lang:
            selected.add((row["id"], lang))
            counts[lang] = counts.get(lang, 0) + 1
    return selected


async def build(args: argparse.Namespace) -> int:
    for name in ("rate", "speech_share"):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be a finite fraction between 0 and 1")
    if not math.isfinite(args.max_cost_inr) or args.max_cost_inr < 0:
        raise ValueError("max_cost_inr must be finite and nonnegative")
    for name in ("audio_per_lang", "audio_max_source_chars", "audio_max_chars"):
        if getattr(args, name) < 0:
            raise ValueError(f"{name} must be nonnegative")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("limit must be positive")
    rows = []
    seen = set()
    for line_no, line in enumerate(Path(args.input).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ("id", "source", "lang")):
            raise ValueError(f"line {line_no}: id, source and lang must be nonempty strings")
        if not get_language(row["lang"]).known or not isinstance(row.get("meta", {}), dict):
            raise ValueError(f"line {line_no}: unknown language or invalid metadata")
        key = (row["id"], row["lang"])
        if key in seen:
            raise ValueError(f"line {line_no}: duplicate id/language")
        seen.add(key)
        rows.append({"id": row["id"], "source": row["source"], "lang": row["lang"],
                     "meta": {k: v for k, v in row.get("meta", {}).items() if k not in {"injection", "translation_origin"}}})
    if not rows:
        raise ValueError("input contains no items")
    rows = rows[:args.limit]
    settings = Settings.from_env()
    cache = Cache(args.cache_dir)
    budget = CostTracker(max_inr=args.max_cost_inr)
    try:
        return await _build(args, rows, settings, cache, budget)
    finally:
        cache.close()


async def _build(args: argparse.Namespace, rows: list[dict], settings: Settings, cache: Cache, budget: CostTracker) -> int:
    out_path, audio_dir = Path(args.out), Path(args.audio_dir)

    async with httpx.AsyncClient() as http:
        client = BodhanClient(settings, cache, budget, http)

        async def translate(row: dict) -> None:
            try:
                result = (await client.translate(row["source"], row["lang"])).value
                if result.get("truncated") or not result["translation"].strip():
                    raise ServiceError("empty or truncated translation")
                row["translation"] = result["translation"]
                row["meta"]["clean_translation_sha256"] = hashlib.sha256(row["translation"].encode()).hexdigest()
            except (ServiceError, BudgetExceeded) as exc:
                row["error"] = f"translation: {exc}"

        await asyncio.gather(*(translate(r) for r in rows))
        failures = [{"id": r["id"], "lang": r["lang"], "error": r["error"]} for r in rows if r.get("error")]
        if failures:
            atomic_write_text(out_path.with_suffix(".build.json"), json.dumps({"failures": failures, "budget": budget.snapshot()}, indent=2) + "\n")
            print(f"translation failed for {len(failures)} items; dataset was not replaced (see .build.json)")
            return 1
        audio_keys = set() if args.no_speech else audio_subset(rows, args)
        rng_order = sorted(rows, key=lambda r: item_rng(args.seed, r["id"], r["lang"]).random())
        n_speech = min(round(len(rows) * args.rate * args.speech_share), len(audio_keys) // 2)
        speech_rows = {(r["id"], r["lang"]) for r in rng_order if (r["id"], r["lang"]) in audio_keys}
        speech_rows = set(sorted(speech_rows, key=lambda k: item_rng(args.seed, *k).random())[:n_speech])
        text_rows = [r for r in rows if (r["id"], r["lang"]) not in audio_keys]
        text_rate = min(1.0, (round(len(rows) * args.rate) - n_speech) / max(len(text_rows), 1))
        injected = {(r["id"], r["lang"]): r for r in inject_text(text_rows, rate=text_rate, seed=args.seed)}

        if args.retranslate:
            donors = [r["source"] for r in rows]
            for key, row in list(injected.items()):
                if row["labels"] and row["labels"][0]["type"] in {"omission", "addition", "mistranslation"}:
                    original = next(r for r in rows if (r["id"], r["lang"]) == key)
                    try:
                        redone = await retranslate(original, row["labels"][0]["type"], client, item_rng(args.seed, *key), donors)
                    except (ServiceError, BudgetExceeded):
                        redone = None
                    if redone:
                        injected[key] = redone

        async def speak(row: dict) -> dict:
            key = (row["id"], row["lang"])
            final = dict(injected.get(key, row))
            final.setdefault("labels", [])
            if key not in audio_keys:
                return final
            sp = to_speakable(final["translation"])
            try:
                speech = await synthesize(client, sp, row["lang"], pick_voice(row["lang"], args.voice))
            except (ServiceError, BudgetExceeded, ValueError) as exc:
                final.setdefault("meta", {})["speech_error"] = str(exc)[:200]
                return final
            audio = speech.audio
            if key in speech_rows and not final["labels"]:
                rng = random.Random(f"{args.seed}:{row['id']}:{row['lang']}:speech")
                op = SPEECH_CATEGORIES[sorted(speech_rows).index(key) % len(SPEECH_CATEGORIES)]
                result = AUDIO_OPS[op](audio, len(sp.text), rng, chunks=speech.chunks, to_translation=sp.span_to_translation)
                if result:
                    audio = result.audio
                    final["labels"] = [result.label]
                    final.setdefault("meta", {})["injection"] = result.note
            # IDs are external input; hash them so separators cannot escape audio_dir.
            name = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24] + ".wav"
            await asyncio.to_thread(atomic_write_bytes, audio_dir / name, encode_wav(audio))
            final["audio"] = Path(os.path.relpath((audio_dir / name).resolve(), out_path.parent.resolve())).as_posix()
            return final

        if args.no_speech:
            finals = [dict(injected.get((r["id"], r["lang"]), r)) for r in rows]
        else:
            finals = await asyncio.gather(*(speak(r) for r in rows))

    for row in finals:
        row.pop("alternates", None)
        row.pop("error", None)
        row.setdefault("labels", [])
        row.setdefault("meta", {})["translation_origin"] = "indic-translate (hosted)"
    atomic_write_text(out_path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in finals))
    labelled = sum(1 for r in finals if r["labels"])
    manifest = {"input": str(args.input), "output": str(args.out), "items": len(finals), "injected_items": labelled,
                "audio_items": sum("audio" in r for r in finals), "seed": args.seed,
                "audio_per_lang": args.audio_per_lang, "audio_max_source_chars": args.audio_max_source_chars,
                "audio_max_chars": args.audio_max_chars, "budget": budget.snapshot(),
                "attributed_inr": {k: round(v, 4) for k, v in sorted(budget.attributed.items())},
                "dataset_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
                "speech_failures": [{"id": r["id"], "lang": r["lang"], "error": r["meta"]["speech_error"]}
                                    for r in finals if r.get("meta", {}).get("speech_error")]}
    atomic_write_text(out_path.with_suffix(".build.json"), json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(finals)} items ({labelled} with an injected error) to {out_path}; spent INR {budget.spent:.3f}")
    if budget.exhausted:
        print("budget cap reached: some items have no audio; raise --max-cost-inr to complete them")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--audio-dir", default="data/audio")
    p.add_argument("--rate", type=float, default=0.5, help="share of items with an injected error")
    p.add_argument("--speech-share", type=float, default=0.3, help="share of injected errors that are speech errors")
    p.add_argument("--seed", type=int, default=31)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--voice", default=None)
    p.add_argument("--retranslate", action="store_true", help="omission/addition/mistranslation via perturbed English + indic-translate")
    p.add_argument("--no-speech", action="store_true")
    p.add_argument("--audio-per-lang", type=int, default=6, help="maximum short-sentence audio items per language")
    p.add_argument("--audio-max-source-chars", type=int, default=180)
    p.add_argument("--audio-max-chars", type=int, default=220, help="maximum speakable translation length")
    p.add_argument("--max-cost-inr", type=float, default=3.0)
    p.add_argument("--cache-dir", default=".bodhan_cache")
    args = p.parse_args()
    load_dotenv_if_present()
    try:
        return asyncio.run(build(args))
    except (ValueError, OSError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
