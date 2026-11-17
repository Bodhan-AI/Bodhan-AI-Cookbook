"""Error injector: plant known errors into clean items so the harness can measure itself.

python -m bodhan_eval.inject --dataset clean.jsonl --out injected.jsonl [--rate 0.5] [--seed 7] [--env-file .env]

No LLM is involved. Indian-language errors are made by editing Bodhan output with string and sample operations, or by
editing the English source, translating that with Indic-Translate, and pairing the result with the original source.

Deterministic: the choice of item, category and position depends only on the seed, the item id and the language.
Every injected row gets one label with type, severity, span (source side when known), source_span, translation_span,
"origin": "injected" and the recipe. Rows left alone get "labels": [] (injected-clean: real model errors may remain).
"""

import argparse
import difflib
import hashlib
import json
import os
import random
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np

from .checks_speech import frame_rms, read_samples, to_wav, wav_bytes
from .checks_text import _TABLE_SEP, numbers_in
from .normalize import sentence_spans

TEXT = ["omission", "addition", "number", "term", "mistranslation", "format", "wrong_language"]
SPEECH = ["speech_omission", "speech_repetition", "garbled"]
CATEGORIES = TEXT + SPEECH
# Hardest-to-place categories pick their items first.
PLACEMENT_ORDER = SPEECH + ["format", "term", "number", "mistranslation", "wrong_language", "omission", "addition"]

# English-side meaning flips for the mistranslation recipe (whole words, case kept).
WORD_SWAPS = {
    "increase": "decrease", "increases": "decreases", "increased": "decreased", "decrease": "increase", "more": "less",
    "less": "more", "before": "after", "after": "before", "higher": "lower", "lower": "higher", "first": "last",
    "last": "first", "north": "south", "south": "north", "east": "west", "west": "east", "hot": "cold", "cold": "hot",
    "open": "closed", "closed": "open", "large": "small", "small": "large", "early": "late", "late": "early",
    "always": "never", "never": "always", "rise": "fall", "rises": "falls", "rose": "fell", "fast": "slow", "slow": "fast",
    "strong": "weak", "weak": "strong", "win": "lose", "won": "lost", "lost": "won", "buy": "sell", "sold": "bought",
    "inside": "outside", "outside": "inside", "above": "below", "below": "above", "young": "old", "old": "young",
    "accept": "reject", "accepted": "rejected", "allow": "forbid", "allowed": "banned", "cheap": "expensive",
    "expensive": "cheap", "wet": "dry", "dry": "wet", "day": "night", "night": "day", "morning": "evening",
    "doctor": "teacher", "teacher": "doctor", "water": "milk", "city": "village", "village": "city", "heart": "lungs",
    "boils": "freezes", "freezes": "boils", "happy": "sad", "sad": "happy", "start": "end", "began": "ended",
}
# Replacement names for the English-side term recipe.
NAMES = ["Arjun", "Meera", "Kolkata", "Pune", "Siemens", "UNICEF", "Toyota", "Nairobi", "Oslo", "Fatima"]
UNIT_SWAPS = {"kg": "g", "g": "kg", "km": "m", "m": "km", "cm": "mm", "mm": "cm", "ml": "l", "l": "ml", "°C": "°F",
              "°F": "°C", "%": "°", "h": "min", "min": "h", "s": "ms", "ms": "s", "mg": "g"}


def rng_for(seed, *parts) -> random.Random:
    return random.Random(hashlib.sha256("/".join(map(str, (seed, *parts))).encode()).hexdigest())


def label(category, severity, recipe, source_span=None, translation_span=None, **extra):
    s = list(source_span) if source_span else None
    t = list(translation_span) if translation_span else None
    return {"type": category, "severity": severity, "span": s or t, "source_span": s, "translation_span": t,
            "origin": "injected", "recipe": recipe, **extra}


def _sents(text):
    return [(s, e) for s, e in sentence_spans(text) if any(ch.isalnum() for ch in text[s:e])]


def _prose(text, span) -> bool:
    """Not a table row, heading or list item: deleting or pasting one of those is a format error, not a meaning one."""
    return not re.match(r"[|#>*-]|\d+[.)]\s", text[span[0]:span[1]])


def _cut(text, s, e):
    """Remove text[s:e] and tidy the whitespace at the seam."""
    left, right = text[:s].rstrip(" "), text[e:].lstrip(" ")
    joined = left + (" " if left and right and not left.endswith("\n") and not right.startswith("\n") else "") + right
    return joined, len(left) + (1 if joined[len(left):len(left) + 1] == " " else 0)


def _changed_span(old: str, new: str):
    """Span in `new` covering every word that differs from `old`, or None if identical."""
    a, b = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", old)], [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", new)]
    sm = difflib.SequenceMatcher(a=[x[2] for x in a], b=[x[2] for x in b], autojunk=False)
    regions = [(j1, j2) for op, _, _, j1, j2 in sm.get_opcodes() if op != "equal" and j2 > j1]
    if not regions:
        return None
    return b[regions[0][0]][0], b[regions[-1][1] - 1][1]


# ---------------------------------------------------------------- text recipes
# Each takes (row, ctx, rng) and returns (changes, label) or None when it does not apply.

def omission(row, ctx, rng):
    src, tr = row["source"], row["translation"]
    ts, ss = _sents(tr), _sents(src)
    prose = [k for k in range(len(ts)) if _prose(tr, ts[k])] if len(ts) == len(ss) else []
    if len(ts) >= 2 and prose:
        k = rng.choice(prose)
        new, at = _cut(tr, *ts[k])
        return {"translation": new}, label("omission", "major", "drop_sentence", ss[k], (at, at), removed=tr[ts[k][0]:ts[k][1]])
    if len(ts) == 1 and len(ss) == 1:  # one sentence: drop a clause between commas
        commas = [m.start() for m in re.finditer(r"[,،]", tr)]
        if commas:
            c = rng.choice(commas)
            nxt = next((p for p in commas if p > c), None)
            end = nxt if nxt is not None else ts[0][1] - (1 if tr[ts[0][1] - 1] in ".!?।" else 0)
            if end - c > 8:
                new, at = _cut(tr, c, end)
                return {"translation": new}, label("omission", "major", "drop_clause", ss[0], (at, at), removed=tr[c:end])
    return None


def addition(row, ctx, rng):
    others = [r for r in ctx["by_lang"][row["lang"]] if r["id"] != row["id"]]
    pool = [r["translation"][s:e] for r in others for s, e in _sents(r["translation"]) if 15 <= e - s <= 160 and _prose(r["translation"], (s, e))]
    if not pool:
        return None
    extra = rng.choice(sorted(pool))
    tr = row["translation"]
    ts = _sents(tr)
    at = ts[rng.randrange(len(ts))][1] if ts else len(tr)
    new = tr[:at] + " " + extra + tr[at:]
    # The span includes the separator space, so everything outside it is unchanged.
    return {"translation": new}, label("addition", "major", "insert_foreign_sentence", None, (at, at + 1 + len(extra)))


def _with_digit(token: str, pos: int, digit: int) -> str:
    ch = token[pos]
    zero = ord(ch) - unicodedata.digit(ch)
    return token[:pos] + chr(zero + digit) + token[pos + 1:]


def number(row, ctx, rng):
    src, tr = row["source"], row["translation"]
    s_nums = numbers_in(src)
    t_nums = [n for n in numbers_in(tr) if any(n["value"] == m["value"] for m in s_nums)]
    if not t_nums:
        return None
    n = rng.choice(t_nums)
    s_match = next(m for m in s_nums if m["value"] == n["value"])
    # A unit next to the number: swap it within its family sometimes.
    if n["unit"] in UNIT_SWAPS and s_match["unit"] == n["unit"] and rng.random() < 0.4:
        m = re.compile(r"[ \t]?" + re.escape(n["unit"])).match(tr, n["span"][1])
        if m:
            u0 = m.start() + (m.end() - m.start() - len(n["unit"]))
            new = tr[:u0] + UNIT_SWAPS[n["unit"]] + tr[m.end():]
            return {"translation": new}, label("number", "major", "unit_swap", s_match["span"], (n["span"][0], u0 + len(UNIT_SWAPS[n["unit"]])))
    tok = n["token"]
    op = rng.choice(["digit", "digit", "zero", "swap"])
    others = [m for m in t_nums if m["token"] != tok and m["value"] != n["value"]]
    if op == "swap" and others:  # two numbers trade places
        m = rng.choice(others)
        (a, b), (c, d) = sorted([n["span"], m["span"]])
        new = tr[:a] + tr[c:d] + tr[b:c] + tr[a:b] + tr[d:]
        s_m = next(x for x in s_nums if x["value"] == m["value"])
        s_lo, s_hi = min(s_match["span"][0], s_m["span"][0]), max(s_match["span"][1], s_m["span"][1])
        return {"translation": new}, label("number", "major", "swap_numbers", (s_lo, s_hi), (a, d), was=f"{tr[a:b]} … {tr[c:d]}")
    if op == "zero" and tok.isdigit():  # add or drop a zero: 100 -> 1000 or 10
        new_tok = tok[:-1] if unicodedata.digit(tok[-1]) == 0 and len(tok) > 1 and rng.random() < 0.5 else tok + _with_digit(tok[-1], 0, 0)
        s, e = n["span"]
        return {"translation": tr[:s] + new_tok + tr[e:]}, label("number", "major", "add_or_drop_zero", s_match["span"], (s, s + len(new_tok)),
                                                                  was=tok, now=new_tok)
    digits = [i for i, ch in enumerate(tok) if ch.isdigit()]
    pos = digits[-1] if len(digits) == 1 or rng.random() < 0.5 else digits[0]
    old = unicodedata.digit(tok[pos])
    new_digit = (old + rng.choice([1, 2, 3])) % 10 if pos != digits[0] or len(digits) == 1 else rng.choice([d for d in range(1, 10) if d != old])
    new_tok = _with_digit(tok, pos, new_digit)
    if new_tok == tok:
        return None
    s, e = n["span"]
    return {"translation": tr[:s] + new_tok + tr[e:]}, label("number", "major", "change_digit", s_match["span"], (s, s + len(new_tok)), was=tok, now=new_tok)


def _retranslated(row, ctx, new_source, category, recipe, source_span, rng):
    bodhan = ctx.get("bodhan")
    if bodhan is None:
        return None
    try:
        new_tr = bodhan.translate(new_source, row["lang"], {"item": f"{row['id']}/{row['lang']}", "check": "inject"})["text"]
    except Exception:  # noqa: BLE001 - an unavailable model just means this recipe does not apply
        return None
    span = _changed_span(row["translation"], new_tr)
    if span is None or span[1] - span[0] > 0.6 * len(new_tr):
        return None  # the model ignored the edit, or rewrote everything
    return {"translation": new_tr}, label(category, "major", recipe, source_span, span, english_edit=new_source[source_span[0]:source_span[1] + 20])


def term(row, ctx, rng):
    src, tr = row["source"], row["translation"]
    # 1. A Latin-script name kept in the translation: swap it with one from another item.
    latin = [m for m in re.finditer(r"\b[A-Z][A-Za-z]{2,}\b|\b[A-Z]{2,}\b", tr) if m.group() in src]
    others = sorted({m.group() for r in ctx["by_lang"][row["lang"]] if r["id"] != row["id"]
                     for m in re.finditer(r"\b[A-Z][a-z]{2,}\b|\b[A-Z]{2,}\b", r["translation"]) if m.group() not in src})
    if latin and others:
        m = rng.choice(latin)
        swap = rng.choice(others)
        s_pos = src.index(m.group())
        return ({"translation": tr[:m.start()] + swap + tr[m.end():]},
                label("term", "major", "swap_latin_name", (s_pos, s_pos + len(m.group())), (m.start(), m.start() + len(swap))))
    # 2. A capitalised source word that is not sentence-initial: replace it in English and re-translate.
    starts = {s for s, _ in _sents(src)}
    names = [m for m in re.finditer(r"\b[A-Z][a-z]{2,}\b", src) if m.start() not in starts and src[max(0, m.start() - 2):m.start()] not in (". ", "\n")]
    if not names:
        return None
    m = rng.choice(names)
    swap = rng.choice([n for n in NAMES if n not in src])
    return _retranslated(row, ctx, src[:m.start()] + swap + src[m.end():], "term", "retranslate_name", (m.start(), m.end()), rng)


def mistranslation(row, ctx, rng):
    src = row["source"]
    hits = [m for m in re.finditer(r"[A-Za-z]+", src) if m.group().lower() in WORD_SWAPS]
    if not hits:
        return None
    m = rng.choice(hits)
    rep = WORD_SWAPS[m.group().lower()]
    rep = rep.capitalize() if m.group()[0].isupper() else rep
    return _retranslated(row, ctx, src[:m.start()] + rep + src[m.end():], "mistranslation", "retranslate_word_swap", (m.start(), m.end()), rng)


def format_error(row, ctx, rng):
    src, tr = row["source"], row["translation"]
    options = []
    t_sep, s_sep = _TABLE_SEP.search(tr), _TABLE_SEP.search(src)
    if t_sep and s_sep:
        options.append(("drop_table_separator", lambda: (tr[:t_sep.start()] + tr[t_sep.end() + 1:], (s_sep.start(), s_sep.end()),
                                                         (max(0, t_sep.start() - 1), t_sep.start() + 1))))
    t_math = [m for m in re.finditer(r"(?<![\\$])\$([^$\n]+)\$(?!\$)", tr)]
    s_math = {m.group(1): m.span() for m in re.finditer(r"(?<![\\$])\$([^$\n]+)\$(?!\$)", src)}
    t_math = [m for m in t_math if m.group(1) in s_math]
    if t_math:
        m = rng.choice(t_math)
        options.append(("drop_closing_dollar", lambda: (tr[:m.end() - 1] + tr[m.end():], s_math[m.group(1)], (m.start(), m.end() - 1))))
    t_head, s_head = list(re.finditer(r"^(#{1,5})[ \t]", tr, re.M)), list(re.finditer(r"^(#{1,5})[ \t][^\n]*", src, re.M))
    if t_head and len(t_head) == len(s_head):
        k = rng.randrange(len(t_head))
        h = t_head[k]
        options.append(("change_heading_level", lambda: (tr[:h.start()] + "#" + tr[h.start():], s_head[k].span(), (h.start(), h.end() + 1))))
    t_bold = list(re.finditer(r"\*\*[^*\n]+\*\*", tr))
    if t_bold and "**" in src:
        b = rng.choice(t_bold)
        s_b = src.index("**")
        options.append(("drop_bold_marker", lambda: (tr[:b.end() - 2] + tr[b.end():], (s_b, src.index("**", s_b + 2) + 2), (b.start(), b.end() - 2))))
    t_rows = [m for m in re.finditer(r"^\|(?:[^|\n]*\|){3,}[ \t]*$", tr, re.M) if not _TABLE_SEP.match(m.group())]
    s_table = re.search(r"^\|.*\|[ \t]*$", src, re.M)
    if t_rows[1:] and s_table:  # a data row loses one cell
        row_m = rng.choice(t_rows[1:])
        cells = [c.start() + row_m.start() for c in re.finditer(r"\|", row_m.group())]
        ci = rng.randrange(1, len(cells) - 1)  # not `k`: the heading recipe's lambda above reads k
        cut = cells[ci + 1] - cells[ci]
        options.append(("drop_table_cell", lambda: (tr[:cells[ci]] + tr[cells[ci + 1]:], s_table.span(), (row_m.start(), row_m.end() - cut))))
    t_fence, s_fence = list(re.finditer(r"^```[^\n]*$", tr, re.M)), re.search(r"^```", src, re.M)
    if len(t_fence) >= 2 and s_fence:  # the closing fence of a code block is lost
        f = t_fence[1]
        options.append(("drop_code_fence", lambda: (tr[:f.start()] + tr[f.end() + 1:], (s_fence.start(), s_fence.end()), (max(0, f.start() - 1), f.start()))))
    if not options:
        return None
    recipe, make = rng.choice(options)
    new, s_span, t_span = make()
    return {"translation": new}, label("format", "major", recipe, s_span, t_span)


def wrong_language(row, ctx, rng):
    src, tr = row["source"], row["translation"]
    other = [r for r in ctx["by_id"].get(row["id"], []) if r["lang"] != row["lang"] and r.get("translation")]
    ts, ss = _sents(tr), _sents(src)
    prose = [k for k in range(len(ts)) if _prose(tr, ts[k])] if len(ts) == len(ss) else []
    same_shape = [o for o in other if len(_sents(o["translation"])) == len(ts)]
    if len(ts) >= 2 and prose and same_shape and rng.random() < 0.34:  # one sentence in another language
        o, k = rng.choice(sorted(same_shape, key=lambda r: r["lang"])), rng.choice(prose)
        s, e = ts[k]
        a, b = _sents(o["translation"])[k]
        piece = o["translation"][a:b]
        return ({"translation": tr[:s] + piece + tr[e:]},
                label("wrong_language", "major", f"sentence_in_{o['lang']}", ss[k], (s, s + len(piece))))
    if len(ts) >= 2 and prose and (not other or rng.random() < 0.5):
        k = rng.choice(prose)  # one sentence left in English
        s, e = ts[k]
        eng = src[ss[k][0]:ss[k][1]]
        return {"translation": tr[:s] + eng + tr[e:]}, label("wrong_language", "major", "sentence_left_in_english", ss[k], (s, s + len(eng)))
    if other:
        o = rng.choice(sorted(other, key=lambda r: r["lang"]))
        return ({"translation": o["translation"]},
                label("wrong_language", "critical", f"whole_text_in_{o['lang']}", (0, len(src)), (0, len(o["translation"]))))
    return None


# ---------------------------------------------------------------- speech recipes

def _audio(row, ctx):
    p = row.get("audio")
    if not p:
        return None
    path = Path(p) if Path(p).is_absolute() else ctx["base"] / p
    if not path.is_file():
        return None
    return read_samples(to_wav(path.read_bytes(), path.name))


def _chars_at(text: str, frac: float) -> int:
    """Character position about `frac` of the way through the text, moved to a word start."""
    pos = int(len(text) * min(max(frac, 0.0), 1.0))
    while 0 < pos < len(text) and not text[pos - 1].isspace():
        pos += 1
    return min(pos, len(text))


def _write_audio(row, ctx, x, sr, recipe):
    ctx["audio_dir"].mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in row["id"])
    path = ctx["audio_dir"] / f"{safe}.{row['lang']}.{recipe}.wav"
    path.write_bytes(wav_bytes(x, sr))
    return os.path.relpath(path, ctx["out_dir"]).replace("\\", "/")


def _quiet_points(x, sr):
    """Sample positions of pauses (quiet 20 ms frames), interior only."""
    rms = frame_rms(x, sr)
    if not len(rms):
        return []
    thr = 0.15 * float(np.median(rms[rms > 0])) if np.any(rms > 0) else 0.0
    hop = int(sr * 0.02)
    return [i * hop for i, v in enumerate(rms) if v <= thr and 0.1 * len(rms) < i < 0.9 * len(rms)]


def speech_omission(row, ctx, rng):
    got = _audio(row, ctx)
    if got is None:
        return None
    x, sr = got
    keep = rng.uniform(0.5, 0.7)
    quiet = [q for q in _quiet_points(x, sr) if q < keep * len(x)]
    cut = quiet[-1] if quiet and quiet[-1] > 0.4 * len(x) else int(keep * len(x))
    tr = row["translation"]
    t0 = _chars_at(tr, cut / len(x))
    if len(tr) - t0 < 10:
        return None
    return {"audio": _write_audio(row, ctx, x[:cut], sr, "truncated")}, label("speech_omission", "major", "truncate_tail", None, (t0, len(tr)),
                                                                             audio_span_s=[round(cut / sr, 2), round(len(x) / sr, 2)])


def speech_repetition(row, ctx, rng):
    got = _audio(row, ctx)
    if got is None:
        return None
    x, sr = got
    quiet = _quiet_points(x, sr)
    pairs = [(a, b) for a, b in zip(quiet, quiet[1:], strict=False) if 0.8 * sr <= b - a <= 3.0 * sr]
    if pairs:
        a, b = rng.choice(pairs)
    else:
        a = int(rng.uniform(0.3, 0.5) * len(x))
        b = min(len(x), a + int(1.5 * sr))
    y = np.concatenate([x[:b], x[a:b], x[b:]])
    tr = row["translation"]
    s, e = _chars_at(tr, a / len(x)), _chars_at(tr, b / len(x))
    return {"audio": _write_audio(row, ctx, y, sr, "repeated")}, label("speech_repetition", "major", "splice_segment_twice", None, (s, max(e, s + 1)),
                                                                      audio_span_s=[round(b / sr, 2), round((2 * b - a) / sr, 2)])


def garbled(row, ctx, rng):
    got = _audio(row, ctx)
    if got is None:
        return None
    x, sr = got
    kind = rng.choice(["noise", "silence", "clipping"])
    tr = row["translation"]
    if kind == "clipping":
        y = np.clip(x * 12.0, -1.0, 1.0)
        return {"audio": _write_audio(row, ctx, y, sr, "clipped")}, label("garbled", "major", "amplify_until_clipping", None, (0, len(tr)))
    at = int(rng.uniform(0.3, 0.6) * len(x))
    if kind == "silence":
        y = np.concatenate([x[:at], np.zeros(int(3.5 * sr), dtype=np.float32), x[at:]])
        p = _chars_at(tr, at / len(x))
        return {"audio": _write_audio(row, ctx, y, sr, "silence")}, label("garbled", "major", "insert_long_silence", None, (p, min(len(tr), p + 1)),
                                                                         audio_span_s=[round(at / sr, 2), round(at / sr + 3.5, 2)])
    n = min(len(x) - at, int(2.5 * sr))
    level = float(np.sqrt(np.mean(x ** 2))) or 0.05
    y = x.copy()
    y[at:at + n] = np.random.default_rng(rng.randrange(2**32)).normal(0, 2.0 * level, n).astype(np.float32)  # speech replaced by noise
    s, e = _chars_at(tr, at / len(x)), _chars_at(tr, (at + n) / len(x))
    return {"audio": _write_audio(row, ctx, y, sr, "noise")}, label("garbled", "major", "replace_with_noise", None, (s, max(e, s + 1)),
                                                                   audio_span_s=[round(at / sr, 2), round((at + n) / sr, 2)])


RECIPES = {"omission": omission, "addition": addition, "number": number, "term": term, "mistranslation": mistranslation,
           "format": format_error, "wrong_language": wrong_language, "speech_omission": speech_omission,
           "speech_repetition": speech_repetition, "garbled": garbled}


def inject(rows: list[dict], rate: float, seed, base: Path, out_dir: Path, audio_dir: Path, bodhan=None,
           categories: list[str] | None = None) -> list[dict]:
    rows = [dict(r) for r in rows]
    ctx = {"bodhan": bodhan, "base": base, "out_dir": out_dir, "audio_dir": audio_dir, "by_lang": {}, "by_id": {}}
    for r in rows:
        ctx["by_lang"].setdefault(r["lang"], []).append(r)
        ctx["by_id"].setdefault(r["id"], []).append(r)
    originals = {(r["id"], r["lang"]): dict(r) for r in rows}
    done = {}
    for lang in sorted(ctx["by_lang"]):
        pool = sorted((r for r in ctx["by_lang"][lang] if r.get("translation")), key=lambda r: rng_for(seed, "pick", r["id"], lang).random())
        n = round(rate * len(pool))
        cats = [c for c in CATEGORIES if not categories or c in categories]
        slots = [cats[i % len(cats)] for i in range(n)]
        slots.sort(key=PLACEMENT_ORDER.index)
        used = set()
        for cat in slots:
            for tries in ([cat], ["omission", "addition"]):  # fall back to a recipe that nearly always applies
                placed = False
                for c in tries:
                    # Text edits go to rows without audio first: edited text with the old audio is not a real system output.
                    for r in (pool if c in SPEECH else sorted(pool, key=lambda r: bool(r.get("audio")))):
                        if r["id"] in used:
                            continue
                        got = RECIPES[c](originals[(r["id"], lang)], ctx, rng_for(seed, c, r["id"], lang))
                        if got:
                            done[(r["id"], lang)] = got
                            used.add(r["id"])
                            placed = True
                            break
                    if placed:
                        break
                if placed:
                    break
    out = []
    for r in rows:
        changes, lab = done.get((r["id"], r["lang"]), ({}, None))
        row = {**r, **changes, "labels": [lab] if lab else []}
        if "translation" in changes and "audio" not in changes:
            row.pop("audio", None)  # the audio spoke the clean text; it no longer matches
        out.append(row)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.inject", description="Plant known errors into a clean dataset.")
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--rate", type=float, default=0.5, help="share of items per language that get one error")
    p.add_argument("--seed", default="7")
    p.add_argument("--categories", help=f"comma-separated subset of: {', '.join(CATEGORIES)}")
    p.add_argument("--audio-dir", help="where edited audio goes (default: <out folder>/audio)")
    p.add_argument("--env-file", help="KEY=VALUE file with BODHAN_TRANSLATE_KEY, for the re-translation recipes")
    p.add_argument("--cache-dir", default=".bodhan_eval_cache")
    a = p.parse_args(argv)
    src, out = Path(a.dataset), Path(a.out)
    rows = [json.loads(line) for line in src.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    bodhan = None
    if a.env_file:
        from .__main__ import load_env_file
        load_env_file(a.env_file)
    if os.environ.get("BODHAN_TRANSLATE_KEY"):
        from .cache import Cache
        from .clients import KEY_ENV, Bodhan, Budget, RunLog
        bodhan = Bodhan({s: os.environ.get(n, "") for s, n in KEY_ENV.items()}, Cache(a.cache_dir), Budget(None), RunLog())
    else:
        print("BODHAN_TRANSLATE_KEY not set: term and mistranslation recipes that re-translate are skipped", file=sys.stderr)
    audio_dir = Path(a.audio_dir) if a.audio_dir else out.parent / "audio"
    cats = [c.strip() for c in (a.categories or "").split(",") if c.strip()]
    if set(cats) - set(CATEGORIES):
        p.error(f"unknown categories: {', '.join(sorted(set(cats) - set(CATEGORIES)))}")
    result = inject(rows, a.rate, a.seed, src.parent, out.parent, audio_dir, bodhan, cats or None)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for r in result:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    counts = {}
    for r in result:
        for lab in r["labels"]:
            counts[(r["lang"], lab["type"])] = counts.get((r["lang"], lab["type"]), 0) + 1
    print(f"wrote {len(result)} rows, {sum(counts.values())} injected")
    for (lang, cat), n in sorted(counts.items()):
        print(f"  {lang} {cat}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
