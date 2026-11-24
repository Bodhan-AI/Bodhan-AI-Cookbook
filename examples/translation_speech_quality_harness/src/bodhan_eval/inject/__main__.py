"""Build a labelled demo/test dataset from English sources.

    python -m bodhan_eval.inject --sources data/sources.jsonl --langs hi,ta,bn --out data/demo.jsonl \
        --audio-dir data/audio --per-type 4 --seed 7 --max-cost-inr 9

1. Translate every source with Indic-Translate (clean items, labels == [] marked as assumed clean).
2. Synthesize speech, sentence by sentence, for sources marked "speech": true (exact segment times).
3. Inject translation errors (balanced per type and language) and speech errors into copies.
Injected labels carry "annotator": "injector".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

import numpy as np

from .. import audio as audiolib
from ..budget import Ledger
from ..cache import Cache
from ..clients import BodhanClient, ModelError
from ..config import Settings
from ..langs import get_lang
from ..text.markup import speakable
from ..text.normalize import sentences
from . import text_injector as ti
from .audio_injector import SPEECH_INJECTORS

SIBLING = {"hi": "mr", "mr": "hi", "ta": "ml", "ml": "ta", "bn": "as", "as": "bn", "te": "kn", "kn": "te", "gu": "hi", "pa": "hi", "or": "bn"}
TYPE_ORDER = ["format", "number", "term", "mistranslation", "omission", "wrong_language", "addition"]


async def build(a) -> dict:
    rng = random.Random(a.seed)
    settings = Settings.from_env(a.cache_dir)
    cache = Cache(settings.cache_dir)
    ledger = Ledger(a.max_cost_inr)
    client = BodhanClient(settings, cache, ledger)
    sources = [json.loads(line) for line in open(a.sources, encoding="utf-8") if line.strip()]
    langs = [x.strip() for x in a.langs.split(",") if x.strip()]
    audio_dir = Path(a.audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(a.out).resolve().parent
    sem = asyncio.Semaphore(6)

    async def tr(text, lang):
        async with sem:
            return (await client.translate(text, lang))["text"]

    # 1. clean translations (several passes: a throttled request is retried later instead of failing the build)
    clean: dict[tuple[str, str], str] = {}
    jobs = [(s, lang) for lang in langs for s in sources]

    async def safe_tr(text, lang):
        try:
            return await tr(text, lang)
        except ModelError as e:
            print(f"  retry later ({lang}): {str(e)[:80]}")
            return None

    for attempt in range(6):
        todo = [(s, lang) for s, lang in jobs if (s["sid"], lang) not in clean]
        if not todo:
            break
        res = await asyncio.gather(*(safe_tr(s["text"], lang) for s, lang in todo))
        for (s, lang), t in zip(todo, res, strict=True):
            if t is not None:
                clean[(s["sid"], lang)] = t.strip()
        print(f"pass {attempt + 1}: {len(clean)}/{len(jobs)} translated; spent ₹{ledger.spent_total:.3f}", flush=True)
    missing = [(s["sid"], lang) for s, lang in jobs if (s["sid"], lang) not in clean]
    if missing:
        sources_ok = {sid for sid, _ in clean}
        print(f"giving up on {len(missing)} translations: {missing[:10]}")
        sources = [s for s in sources if all((s["sid"], lang) in clean for lang in langs)]
        print(f"continuing with {len(sources)} of {len(sources_ok)} sources")

    # 2. speech, per sentence
    seg_info: dict[tuple[str, str], tuple[np.ndarray, int, list[dict]]] = {}

    async def synth(sid, lang):
        text = clean[(sid, lang)]
        plain, pidx = speakable(text)
        pcm, segs, sr = [], [], 24000
        for s, e in sentences(plain):
            chunk = plain[s:e].strip()
            if not any(c.isalpha() for c in chunk):
                continue
            async with sem:
                wav = await client.speak(chunk, lang, get_lang(lang).voices[0])
            x, sr = audiolib.decode(wav)
            pcm.append(x)
            segs.append({"text_span": [pidx[s], pidx[e - 1] + 1]})
        x, times = audiolib.concat(pcm, sr)
        for seg, (t0, t1) in zip(segs, times, strict=True):
            seg["start_s"], seg["end_s"] = t0, t1
        seg_info[(sid, lang)] = (x, sr, segs)
        (audio_dir / f"{sid}.{lang}.wav").write_bytes(audiolib.encode(x, sr))

    speech_jobs = [(s["sid"], lang) for lang in langs for s in sources if s.get("speech")]
    if a.speech_limit is not None:
        speech_jobs = speech_jobs[: a.speech_limit]
    async def safe_synth(sid, lang):
        try:
            await synth(sid, lang)
        except Exception as e:  # noqa: BLE001 - a failed clip just means no audio for that item
            print(f"  speech failed for {sid}.{lang}: {type(e).__name__}: {str(e)[:80]}")

    speech_jobs = [(sid, lang) for sid, lang in speech_jobs if (sid, lang) in clean]
    await asyncio.gather(*(safe_synth(sid, lang) for sid, lang in speech_jobs))
    print(f"synthesized {len(speech_jobs)} clips; spent ₹{ledger.spent_total:.3f}")

    def rel(p: Path) -> str:
        try:
            return p.resolve().relative_to(out_dir).as_posix()
        except ValueError:
            return p.resolve().as_posix()

    items: list[dict] = []
    for s in sources:
        for lang in langs:
            it = {"id": f"{s['sid']}.{lang}", "source": s["text"], "lang": lang, "translation": clean[(s["sid"], lang)], "labels": [],
                  "group": s["sid"], "content_type": s["type"], "label_source": "assumed clean: unreviewed Indic-Translate output"}
            if (s["sid"], lang) in seg_info:
                it["audio"] = rel(audio_dir / f"{s['sid']}.{lang}.wav")
                it["audio_segments"] = seg_info[(s["sid"], lang)][2]
            items.append(it)

    # 3a. translation errors, balanced per language and type
    by_sid = {s["sid"]: s for s in sources}
    pending_retranslate = []
    for lang in langs:
        used: set[str] = set()
        sids = [s["sid"] for s in sources]
        for typ in TYPE_ORDER:
            cand = sids[:]
            rng.shuffle(cand)
            made = 0
            for sid in cand:
                if made >= a.per_type or sid in used:
                    continue
                src, t = by_sid[sid]["text"], clean[(sid, lang)]
                ctype = by_sid[sid]["type"]
                result = None
                if typ == "format" and ctype in ("markdown", "latex"):
                    result = ti.format_break(t, rng)
                elif typ == "number" and ctype not in ("markdown", "latex"):
                    result = ti.number(t, src, rng)
                elif typ in ("term", "mistranslation") and ctype not in ("markdown", "latex"):
                    p = ti.perturb_source(src, typ, rng)
                    if p:
                        pending_retranslate.append((sid, lang, typ, p))
                        used.add(sid)
                        made += 1
                    continue
                elif typ == "omission" and ctype in ("prose", "numbers", "entities"):
                    result = ti.omission(t, src, rng)
                elif typ == "wrong_language" and ctype in ("prose", "numbers", "entities"):
                    if rng.random() < 0.5 and lang in SIBLING:
                        pending_retranslate.append((sid, lang, typ, (src, None)))
                        used.add(sid)
                        made += 1
                        continue
                    result = ti.untranslated(t, src, rng)
                elif typ == "addition" and ctype in ("prose", "numbers", "entities"):
                    donor_sid = rng.choice([x for x in sids if x != sid and by_sid[x]["type"] in ("prose", "entities")])
                    result = ti.addition(t, clean[(donor_sid, lang)], rng)
                if result:
                    new, lab = result
                    items.append({"id": f"{sid}.{lang}.inj-{typ}", "source": src, "lang": lang, "translation": new, "labels": [lab],
                                  "group": sid, "content_type": ctype})
                    used.add(sid)
                    made += 1

    async def retranslate(sid, lang, typ, p):
        src = by_sid[sid]["text"]
        if typ == "wrong_language":
            other = SIBLING[lang]
            t = await tr(src, other)
            return {"id": f"{sid}.{lang}.inj-{typ}", "source": src, "lang": lang, "translation": t.strip(), "group": sid,
                    "content_type": by_sid[sid]["type"],
                    "labels": [{"type": "wrong_language", "severity": "critical", "span": [0, len(t.strip())], "annotator": ti.ANNOTATOR,
                                "note": f"translated into {get_lang(other).name} instead"}]}
        perturbed, src_span = p
        t2 = (await tr(perturbed, lang)).strip()
        span = ti.diff_span(clean[(sid, lang)], t2)
        if span is None:
            return None
        return {"id": f"{sid}.{lang}.inj-{typ}", "source": src, "lang": lang, "translation": t2, "group": sid, "content_type": by_sid[sid]["type"],
                "labels": [{"type": typ, "severity": "critical" if typ == "term" else "major", "span": list(span), "source_span": list(src_span),
                            "annotator": ti.ANNOTATOR, "note": f"English source perturbed ({src[src_span[0]:src_span[1]]!r} -> {perturbed[src_span[0]:src_span[0] + 40]!r}...) and re-translated"}]}

    async def safe_retranslate(*x):
        for _ in range(3):
            try:
                return await retranslate(*x)
            except ModelError as e:
                print(f"  retranslate retry {x[0]}.{x[1]}: {str(e)[:60]}")
        return None

    extra = await asyncio.gather(*(safe_retranslate(*x) for x in pending_retranslate))
    items += [x for x in extra if x]

    # 3b. speech errors on copies of clean items with audio
    for lang in langs:
        keys = [k for k in seg_info if k[1] == lang]
        rng.shuffle(keys)
        types = list(SPEECH_INJECTORS)
        for i, key in enumerate(sorted(keys, key=lambda k: k[0])):
            typ = types[i % len(types)]
            x, sr, segs = seg_info[key]
            y, lab = SPEECH_INJECTORS[typ](x, sr, segs, len(clean[key]), rng)
            path = audio_dir / f"{key[0]}.{lang}.inj-{typ}.wav"
            path.write_bytes(audiolib.encode(y, sr))
            items.append({"id": f"{key[0]}.{lang}.inj-{typ}", "source": by_sid[key[0]]["text"], "lang": lang, "translation": clean[key],
                          "audio": rel(path), "labels": [lab], "group": key[0], "content_type": by_sid[key[0]]["type"]})

    # references for a subset (demo of the reference-based path): the clean system output
    for it in items:
        if ".inj-" in it["id"] and rng.random() < 0.15 and not it["labels"][0].get("area"):
            sid, lang = it["group"], it["lang"]
            it["reference"] = clean[(sid, lang)]
            it["reference_note"] = "demo only: clean Indic-Translate output used as the reference"

    items.sort(key=lambda r: r["id"])
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    await client.aclose()
    cache.close()
    counts: dict[str, int] = {}
    for it in items:
        k = it["labels"][0]["type"] if it["labels"] else "clean"
        counts[k] = counts.get(k, 0) + 1
    manifest = {"items": len(items), "by_type": dict(sorted(counts.items())), "langs": langs, "seed": a.seed,
                "spent_inr": round(ledger.spent_total, 4), "cost": ledger.summary()}
    Path(a.out).with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sources", required=True)
    p.add_argument("--langs", default="hi,ta,bn")
    p.add_argument("--out", required=True)
    p.add_argument("--audio-dir", default="data/audio")
    p.add_argument("--per-type", type=int, default=4)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--speech-limit", type=int, default=None)
    p.add_argument("--max-cost-inr", type=float, default=None)
    p.add_argument("--cache-dir", default=None)
    a = p.parse_args()
    print(json.dumps(asyncio.run(build(a)), indent=2))


if __name__ == "__main__":
    main()
