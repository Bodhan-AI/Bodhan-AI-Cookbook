"""Error injector. Builds labelled test cases from CLEAN items, programmatically only:
deleting spans, changing digits, swapping tokens with tokens from other items, breaking markup,
and cutting / looping / muting audio. No LLM ever writes Indian-language text here.

  python -m bodhan_eval.injector build  --sources data/sources.jsonl --langs hi,ta,bn --out data/demo_injected.jsonl
  python -m bodhan_eval.injector inject --dataset clean.jsonl --out injected.jsonl --rate 0.5 --seed 7
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import unicodedata
from collections import Counter
from pathlib import Path

from . import audio as au
from .align import split_sentences
from .checks_text import extract_numbers
from .normalize import has_letters, script_of, tokenize

TEXT_KINDS = ["omission", "addition", "mistranslation", "number", "term", "format", "wrong_language"]
SPEECH_KINDS = ["speech_omission", "repetition", "garbled"]
LABEL_SRC = "injector"


def _rng(seed: int, key: str) -> random.Random:
    return random.Random(int(hashlib.sha1(f"{seed}:{key}".encode()).hexdigest()[:12], 16))


def _label(cat, sev, span, side, **extra):
    return {"type": cat, "severity": sev, "span": span, "side": side, "label_source": LABEL_SRC, **extra}


# ------------------------------------------------------------------ text injections
def inj_omission(item, tr, rng, pool):
    sents = split_sentences(tr)
    src_sents = split_sentences(item["source"])
    if len(sents) >= 2:
        k = rng.randrange(len(sents))
        a, b = sents[k]
        # swallow the separator that follows so we do not leave a double space
        while b < len(tr) and tr[b].isspace():
            b += 1
        new = tr[:a] + tr[b:]
        if len(src_sents) == len(sents):
            span = list(src_sents[k])
        else:
            span = [int(len(item["source"]) * k / len(sents)), int(len(item["source"]) * (k + 1) / len(sents))]
        return new.strip(), _label("omission", "major", span, "source", span_approx=len(src_sents) != len(sents))
    toks = tokenize(tr)
    if len(toks) < 8:
        return None
    n = max(3, len(toks) // 3)
    i = rng.randrange(1, len(toks) - n)
    new = (tr[:toks[i].start] + tr[toks[i + n].start:]).strip()
    L = len(item["source"])
    return new, _label("omission", "major", [int(L * i / len(toks)), int(L * (i + n) / len(toks))], "source", span_approx=True)


def inj_addition(item, tr, rng, pool):
    cands = [s for s in pool.get(item["lang"], []) if s not in tr]
    if not cands:
        return None
    extra = rng.choice(cands)
    sents = split_sentences(tr)
    cut = sents[rng.randrange(len(sents))][1] if sents else len(tr)
    new = tr[:cut] + " " + extra + tr[cut:]
    return new, _label("addition", "major", [cut + 1, cut + 1 + len(extra)], "translation")


def _shift_digit(ch: str, rng) -> str:
    v = unicodedata.decimal(ch)
    nv = rng.choice([d for d in range(10) if d != v])
    return chr(ord(ch) - v + nv)


def inj_number(item, tr, rng, pool):
    nums = [n for n in extract_numbers(tr) if n["value"].isdigit() or "." in n["value"]]
    if not nums:
        return None
    n = rng.choice(nums)
    a, b = n["span"]
    digits = [i for i in range(a, b) if unicodedata.category(tr[i]) == "Nd"]
    if not digits:
        return None
    i = rng.choice(digits)
    if i == a and len(digits) > 1 and unicodedata.decimal(tr[i]) != 0:
        pass
    new = tr[:i] + _shift_digit(tr[i], rng) + tr[i + 1:]
    src = next((s for s in extract_numbers(item["source"]) if s["value"] == n["value"]), None)
    return new, _label("number", "major", src["span"] if src else [a, b], "source" if src else "translation",
                       span_translation=[a, b])


def inj_mistranslation(item, tr, rng, pool):
    toks = [t for t in tokenize(tr) if len(t.raw) >= 3 and has_letters(t.raw) and script_of(t.raw[0]) != "Latn"]
    cands = [t for t in pool.get(item["lang"] + ":tokens", []) if t not in tr]
    if not toks or not cands:
        return None
    t = rng.choice(toks)
    rep = rng.choice(cands)
    return tr[:t.start] + rep + tr[t.end:], _label("mistranslation", "major", [t.start, t.start + len(rep)], "translation")


def _mutate_word(word: str, rng) -> str | None:
    """Misspell a word: swap two adjacent letters, else drop one inner letter (never a lone mark)."""
    idx = [i for i in range(1, len(word) - 1) if unicodedata.category(word[i])[0] == "L"]
    pairs = [i for i in range(len(word) - 1) if unicodedata.category(word[i])[0] == "L" and unicodedata.category(word[i + 1])[0] == "L" and word[i] != word[i + 1]]
    if pairs and rng.random() < 0.5:
        i = rng.choice(pairs)
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if idx:
        i = rng.choice(idx)
        return word[:i] + word[i + 1:]
    return None


def inj_term(item, tr, rng, pool):
    """A name / technical term rendered wrongly: misspell the translation's rendering of a source entity.
    The token is found by Latin form when kept, else by the entity's relative position in its sentence."""
    stoks = tokenize(item["source"])
    ents = [i for i, t in enumerate(stoks) if t.raw.isascii() and t.raw[:1].isalpha()
            and ((i > 0 and t.raw[0].isupper() and len(t.raw) > 2 and "." not in item["source"][max(0, t.start - 2):t.start]) or (t.raw.isupper() and len(t.raw) >= 2))]
    ttoks = [t for t in tokenize(tr) if has_letters(t.raw) and len(t.raw) >= 3]
    cand = None
    latin = [t for t in ttoks if t.raw.isascii()]
    if latin:
        cand = rng.choice(latin)
    elif ents and ttoks:
        rel = ents[rng.randrange(len(ents))] / max(1, len(stoks) - 1)
        cand = ttoks[min(len(ttoks) - 1, int(round(rel * (len(ttoks) - 1))))]
    elif ttoks:
        cand = max(ttoks, key=lambda x: len(x.raw))  # no entity in the source: longest word as a technical-term proxy
    if cand is None:
        return None
    w2 = _mutate_word(cand.raw, rng)
    if not w2 or w2 == cand.raw:
        return None
    return tr[:cand.start] + w2 + tr[cand.end:], _label("term", "major", [cand.start, cand.start + len(w2)], "translation")


def inj_format(item, tr, rng, pool):
    ops = []
    if "|" in tr:
        ops.append(lambda: _drop_char(tr, "|", rng))
    if "$" in tr:
        ops.append(lambda: _drop_char(tr, "$", rng))
    if "```" in tr:
        ops.append(lambda: (lambda i: (tr[:i] + tr[i + 3:], i))(tr.index("```")))
    if re.search(r"^#{1,6}\s", tr, re.MULTILINE):
        ops.append(lambda: (lambda m: (tr[:m.start()] + tr[m.end():], m.start()))(re.search(r"^#{1,6}\s", tr, re.MULTILINE)))
    if not ops:
        toks = [t for t in tokenize(tr) if has_letters(t.raw)]
        if not toks:
            return None
        t = rng.choice(toks)
        new, pos = tr[:t.start] + "**" + tr[t.start:], t.start
    else:
        new, pos = rng.choice(ops)()
    return new, _label("format", "major", [max(0, pos - 1), min(len(new), pos + 3)], "translation")


def _drop_char(tr, ch, rng):
    idx = [i for i, c in enumerate(tr) if c == ch]
    i = rng.choice(idx)
    return tr[:i] + tr[i + 1:], i


def inj_wrong_language(item, tr, rng, pool):
    other = [x for x in pool.get("_by_source", {}).get(item["source"], []) if x[0] != item["lang"]]
    if other and rng.random() < 0.5:
        return rng.choice(other)[1], _label("wrong_language", "critical", [0, len(other[0][1])], "translation")
    return item["source"], _label("wrong_language", "critical", [0, len(item["source"])], "translation")


TEXT_INJ = {"omission": inj_omission, "addition": inj_addition, "mistranslation": inj_mistranslation, "number": inj_number,
            "term": inj_term, "format": inj_format, "wrong_language": inj_wrong_language}


def build_pool(items):
    pool = {"_by_source": {}}
    for it in items:
        tr = it.get("translation")
        if not tr:
            continue
        l = it["lang"]
        pool.setdefault(l, []).extend(tr[a:b] for a, b in split_sentences(tr) if has_letters(tr[a:b]) and "|" not in tr[a:b] and "$" not in tr[a:b])
        pool.setdefault(l + ":tokens", []).extend(t.raw for t in tokenize(tr) if len(t.raw) >= 3 and script_of(t.raw[0]) not in ("Latn", None))
        pool["_by_source"].setdefault(it["source"], []).append((l, tr))
    for k, v in list(pool.items()):
        if isinstance(v, list):
            pool[k] = sorted(set(v)) if k.endswith(":tokens") else sorted(set(v))
    return pool


# ------------------------------------------------------------------ speech injections
def inject_speech(kind, spoken_text, wav: bytes, rng, seed):
    x, sr = au.decode(wav)
    L = len(spoken_text)
    if kind == "speech_omission":
        keep = rng.uniform(0.45, 0.75)
        return au.encode_wav(au.truncate(x, keep), sr), _label("speech_omission", "major", [int(L * keep), L], "source", area="speech", span_approx=True)
    if kind == "repetition":
        start = rng.uniform(0.15, 0.5)
        seg = min(len(x) / sr * 0.25, 3.0)
        return au.encode_wav(au.repeat_segment(x, sr, start, seg), sr), _label(
            "repetition", "major", [int(L * start), int(L * min(1.0, start + 0.25))], "source", area="speech", span_approx=True)
    secs = rng.uniform(2.5, 4.0)
    if rng.random() < 0.5:
        y = au.insert_silence(x, sr, rng.uniform(0.3, 0.7), secs)
    else:
        y = au.add_noise(x, 0.4, 0.4 + min(0.3, secs / max(1.0, len(x) / sr)), seed)
    return au.encode_wav(y, sr), _label("garbled", "major", None, "source", area="speech")


# ------------------------------------------------------------------ dataset level
def inject_dataset(items, rate=0.5, seed=0, kinds=None, audio_dir: str | None = None, base_dir: Path | None = None,
                   reference_rate=0.25, speech_share=0.5):
    kinds = kinds or TEXT_KINDS
    pool = build_pool(items)
    out = []
    used: Counter = Counter()
    for it in items:
        new = dict(it)
        tr = it.get("translation")
        rng = _rng(seed, it["id"])
        if tr and rng.random() < reference_rate:
            new["reference"] = tr  # clean text kept as reference for a quarter of items; the rest are reference-free
        labels = []
        if tr and rng.random() < rate:
            has_audio = bool(it.get("audio")) and audio_dir
            if has_audio and rng.random() < speech_share:
                p = Path(it["audio"]) if Path(it["audio"]).is_absolute() else (base_dir or Path(".")) / it["audio"]
                kind = rng.choice(SPEECH_KINDS)
                wav, lab = inject_speech(kind, tr, p.read_bytes(), rng, seed)
                Path(audio_dir).mkdir(parents=True, exist_ok=True)
                ap = Path(audio_dir) / f"{it['id']}.{it['lang']}.{kind}.wav"
                ap.write_bytes(wav)
                new["audio"] = str(ap).replace("\\", "/")
                labels.append(lab)
            else:
                # balance the categories: try the least-used kind first (ties broken randomly), so rare-but-
                # important kinds such as number errors are not crowded out
                order = sorted(kinds, key=lambda k: (used[k], rng.random()))
                for kind in order:
                    r = TEXT_INJ[kind](it, tr, rng, pool)
                    if r and r[0] != tr:
                        used[kind] += 1
                        new["translation"] = r[0]
                        labels.append(r[1])
                        if it.get("audio"):
                            new["speech_text"] = tr  # the audio speaks the ORIGINAL text, not the damaged one
                        break
        new["labels"] = labels
        new["label_source"] = LABEL_SRC
        out.append(new)
    return out


def _write(items, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(it, ensure_ascii=False) + "\n" for it in items)


def main(argv=None):
    p = argparse.ArgumentParser(prog="bodhan_eval.injector")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="translate sources with Indic-Translate, optionally synthesize audio, then inject errors")
    b.add_argument("--sources", required=True)
    b.add_argument("--langs", default="hi,ta,bn")
    b.add_argument("--out", required=True)
    b.add_argument("--audio-dir", default="audio")
    b.add_argument("--audio-fraction", type=float, default=0.0, help="fraction of items to synthesize (costly)")
    b.add_argument("--rate", type=float, default=0.5)
    b.add_argument("--speech-share", type=float, default=0.5, help="share of injected audio items that get an audio error instead of a text error")
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--cache-dir", default=".cache/bodhan_eval")
    b.add_argument("--max-cost-inr", type=float, default=20.0)
    i = sub.add_parser("inject", help="inject errors into an already-translated clean dataset")
    i.add_argument("--dataset", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--rate", type=float, default=0.5)
    i.add_argument("--speech-share", type=float, default=0.5)
    i.add_argument("--seed", type=int, default=7)
    i.add_argument("--audio-dir")
    a = p.parse_args(argv)

    if a.cmd == "inject":
        items = [json.loads(l) for l in open(a.dataset, encoding="utf-8") if l.strip()]
        _write(inject_dataset(items, a.rate, a.seed, audio_dir=a.audio_dir, base_dir=Path(a.dataset).parent, speech_share=a.speech_share), a.out)
        return 0

    from .cache import Cache
    from .cli import load_dotenv
    from .clients import BodhanBackend, Services
    from .cost import CostTracker

    load_dotenv()
    svc = Services(BodhanBackend(), Cache(a.cache_dir), CostTracker(a.max_cost_inr))
    langs = [l.strip() for l in a.langs.split(",") if l.strip()]
    srcs = [json.loads(l) for l in open(a.sources, encoding="utf-8") if l.strip()]
    items = []
    for s in srcs:
        for lang in langs:
            it = {"id": f"{s['id']}-{lang}", "source": s["source"], "lang": lang}
            it["translation"] = svc.translate(s["source"], "en", lang)
            if _rng(a.seed, it["id"]).random() < a.audio_fraction:
                Path(a.audio_dir).mkdir(parents=True, exist_ok=True)
                ap = Path(a.audio_dir) / f"{it['id']}.clean.wav"
                ap.write_bytes(svc.speak(it["translation"], lang))
                it["audio"] = str(ap).replace("\\", "/")
            items.append(it)
    out = inject_dataset(items, a.rate, a.seed, audio_dir=a.audio_dir, base_dir=Path("."), speech_share=a.speech_share)
    _write(out, a.out)
    print(f"wrote {len(out)} items to {a.out}; spent INR {svc.cost.new_spend:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
