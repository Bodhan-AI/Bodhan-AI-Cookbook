"""Error injector: turn clean items into labelled test cases.

    python -m bodhan_eval.inject --sources data/sources.en.jsonl --langs hi,ta,bn \
        --out data/demo.jsonl --truth data/demo.truth.jsonl --audio-dir outputs/demo_audio

Indian-language errors are made only by code (deleting/inserting/swapping spans, changing
digits, breaking Markdown, editing audio) or by perturbing the English source and
re-translating with Indic-Translate. No LLM writes any text here.

Each output item carries at most one seeded error. ``--out`` holds labels for a labelled
subset (``labelled_share``); ``--truth`` holds labels for every item, for measuring the harness.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import os
import random
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf
from dotenv import load_dotenv

from bodhan_eval.cache import Cache
from bodhan_eval.clients import Bodhan, Budget, BudgetExceeded, ModelError
from bodhan_eval.langs import validate_langs
from bodhan_eval.normalize import numbers
from bodhan_eval.pipeline import synthesize

ANTONYMS = {
    "increase": "decrease", "increases": "decreases", "rise": "fall", "rises": "falls", "hot": "cold", "warm": "cool",
    "above": "below", "before": "after", "more": "less", "larger": "smaller", "higher": "lower", "always": "never",
    "north": "south", "east": "west", "inside": "outside", "faster": "slower", "heavy": "light", "early": "late",
    "open": "closed", "positive": "negative", "maximum": "minimum", "accept": "reject", "allowed": "forbidden",
    "safe": "dangerous", "strong": "weak", "add": "subtract", "absorb": "release", "absorbs": "releases",
}
ANTONYMS |= {v: k for k, v in ANTONYMS.items()}
SENTENCE = re.compile(r"[^.!?।\n]+[.!?।]*\s*")
TRANSLATION_OPS = ["omission", "addition", "mistranslation", "number", "term", "format", "wrong_language"]
SPEECH_OPS = ["speech_omission", "speech_repetition", "speech_garbled"]


def _sentences(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in SENTENCE.finditer(text) if m.group(0).strip()]


def _diff_span(old: str, new: str) -> tuple[int, int] | None:
    """Bounding span in ``new`` of everything that differs from ``old``."""
    ops = [op for op in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes() if op[0] != "equal"]
    if not ops:
        return None
    start, end = ops[0][3], ops[-1][4]
    if end - start > 0.6 * len(new):  # re-translation diverged everywhere; not a localized error
        return None
    return (start, max(end, start + 1))


class Injector:
    def __init__(self, bodhan: Bodhan, rng: random.Random, pool: dict[str, list[str]]):
        self.bodhan, self.rng, self.pool = bodhan, rng, pool

    def tr(self, text: str, lang: str) -> str:
        return self.bodhan.translate(text, lang)["translation"]

    # --- translation errors -------------------------------------------------
    def omission(self, it: dict):
        clauses = [m for m in re.finditer(r"[^.;,!?\n]+[.;,!?]?", it["source"]) if len(m.group(0).split()) >= 3]
        if len(clauses) < 2:
            return None
        m = self.rng.choice(clauses)
        reduced = (it["source"][:m.start()] + it["source"][m.end():]).strip()
        return self.tr(reduced, it["lang"]), {"type": "omission", "ref": "source", "span": [m.start(), m.end()]}

    def addition(self, it: dict):
        others = [s for s in self.pool[it["lang"]] if s.strip() and s.strip() not in it["translation"]]
        if not others:
            return None
        extra = self.rng.choice(others).strip()
        cuts = [e for _, e in _sentences(it["translation"])] or [len(it["translation"])]
        at = self.rng.choice(cuts)
        insert = (" " if at and not it["translation"][at - 1].isspace() else "") + extra + " "
        new = it["translation"][:at] + insert + it["translation"][at:]
        start = at + len(insert) - len(insert.lstrip())
        return new, {"type": "addition", "span": [start, start + len(extra)]}

    def mistranslation(self, it: dict):
        words = [m for m in re.finditer(r"[A-Za-z]+", it["source"]) if m.group(0).lower() in ANTONYMS]
        if not words:
            return None
        m = self.rng.choice(words)
        w = m.group(0)
        swap = ANTONYMS[w.lower()]
        swap = swap.capitalize() if w[0].isupper() else swap
        new = self.tr(it["source"][:m.start()] + swap + it["source"][m.end():], it["lang"])
        span = _diff_span(it["translation"], new)
        return (new, {"type": "mistranslation", "span": list(span)}) if span else None

    def number(self, it: dict):
        digits = [i for i, c in enumerate(it["translation"]) if c.isdigit() and unicodedata.decimal(c, None) is not None]
        if not digits:
            return None
        i = self.rng.choice(digits)
        c = it["translation"][i]
        d = unicodedata.decimal(c)
        nd = (d + self.rng.randint(1, 8)) % 10
        new = it["translation"][:i] + chr(ord(c) - d + nd) + it["translation"][i + 1:]
        s, e = next((s, e) for _, s, e in numbers(new) if s <= i < e)  # label the whole number
        return new, {"type": "number", "span": [s, e]}

    def term(self, it: dict):
        names = [m for m in re.finditer(r"(?<=[a-z,;] )[A-Z][a-zA-Z]{2,}", it["source"])]
        candidates = [n for n in self.pool["names"] if n not in it["source"]]
        if not names or not candidates:
            return None
        m = self.rng.choice(names)
        new = self.tr(it["source"][:m.start()] + self.rng.choice(candidates) + it["source"][m.end():], it["lang"])
        span = _diff_span(it["translation"], new)
        return (new, {"type": "term", "span": list(span)}) if span else None

    def format(self, it: dict):
        t = it["translation"]
        options = []
        for m in re.finditer(r"^\|.*\|$", t, re.M):
            bars = [m.start() + j for j, ch in enumerate(m.group(0)) if ch == "|"][1:-1]
            if bars:
                options.append(("drop_cell_bar", self.rng.choice(bars)))
        options += [("drop_dollar", m.start()) for m in re.finditer(r"(?<!\\)\$", t)]
        options += [("drop_bold", m.start()) for m in re.finditer(r"\*\*", t)]
        options += [("heading_level", m.start()) for m in re.finditer(r"^#{2,6} ", t, re.M)]
        if not options:
            return None
        kind, pos = self.rng.choice(options)
        width = 2 if kind == "drop_bold" else 1
        new = t[:pos] + t[pos + width:]
        line_end = new.find("\n", pos)
        return new, {"type": "format", "span": [new.rfind("\n", 0, pos) + 1, len(new) if line_end < 0 else line_end], "op": kind}

    def wrong_language(self, it: dict):
        src_s, tgt_s = _sentences(it["source"]), _sentences(it["translation"])
        if len(src_s) == len(tgt_s) > 1:
            k = self.rng.randrange(len(tgt_s))
            english = it["source"][slice(*src_s[k])].strip()
            a, b = tgt_s[k]
            new = it["translation"][:a] + english + " " + it["translation"][b:]
            return new, {"type": "wrong_language", "span": [a, a + len(english)]}
        other = {"DEVANAGARI": "bn", "BENGALI": "ta", "TAMIL": "hi"}.get(_script(it["translation"]), "hi")
        other = "te" if other == it["lang"] else other
        new = self.tr(it["source"], other)
        return new, {"type": "wrong_language", "span": [0, len(new)]}

    # --- speech errors (on synthesized audio with known sentence segments) ---
    def speech(self, op: str, wav: bytes, segs: list[dict], text: str):
        x, sr = sf.read(io.BytesIO(wav), dtype="float32")
        k = self.rng.randrange(len(segs))
        seg = segs[k]
        a, b = int(seg["start_s"] * sr), int(seg["end_s"] * sr)
        s0, s1 = seg["span"]
        if op == "speech_omission":
            if len(segs) > 1:
                y, span = np.concatenate([x[:a], x[b + int(0.25 * sr):]]), [s0, s1]
            else:  # truncation: cut the last 40% of the only sentence
                cut = a + int((b - a) * 0.6)
                y, span = x[:cut], [s0 + int((s1 - s0) * 0.6), s1]
        elif op == "speech_repetition":
            if b - a > 2 * sr or len(segs) == 1:  # repeat the second half of the sentence
                mid = a + (b - a) // 2
                y, span = np.concatenate([x[:b], x[mid:b], x[b:]]), [s0 + (s1 - s0) // 2, s1]
            else:
                y, span = np.concatenate([x[:b], np.zeros(int(0.2 * sr), "float32"), x[a:b], x[b:]]), [s0, s1]
        else:  # garbled: a noise burst or a long dropout in the middle of the sentence
            mid = a + (b - a) // 2
            n = int(0.7 * sr)
            if self.rng.random() < 0.5:
                rms = float(np.sqrt(np.mean(x[a:b] ** 2))) or 0.05
                y = np.concatenate([x[:mid], self.rng_noise(n, rms), x[mid + n:]])
            else:
                y = np.concatenate([x[:mid], np.zeros(int(2.0 * sr), "float32"), x[mid:]])
            frac = (mid - a) / max(1, b - a)
            c = s0 + int((s1 - s0) * frac)
            span = [max(s0, c - 8), min(s1, c + 8)]
        buf = io.BytesIO()
        sf.write(buf, y, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue(), {"type": op, "span": span}

    def rng_noise(self, n: int, rms: float) -> np.ndarray:
        return np.random.default_rng(self.rng.randrange(2**32)).normal(0, rms * 1.5, n).clip(-1, 1).astype("float32")


def _script(text: str) -> str:
    for c in text:
        if c.isalpha() and ord(c) > 0x7F:
            return unicodedata.name(c, "").split(" ")[0]
    return "LATIN"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.inject", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", required=True, type=Path, help="JSONL with id, source (+ lang, translation to reuse existing outputs)")
    p.add_argument("--langs", required=True)
    p.add_argument("--out", required=True, type=Path, help="dataset with labels on a labelled subset")
    p.add_argument("--truth", required=True, type=Path, help="labels for every item (ground truth for measuring)")
    p.add_argument("--audio-dir", type=Path, default=Path("outputs/demo_audio"))
    p.add_argument("--error-share", type=float, default=0.55, help="share of items that get one seeded error")
    p.add_argument("--speech-share", type=float, default=0.3, help="share of items that get audio")
    p.add_argument("--labelled-share", type=float, default=0.5, help="share of items whose labels go into --out")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--max-cost-inr", type=float, default=8.0)
    p.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    args = p.parse_args(argv)
    load_dotenv()

    langs = validate_langs([x.strip() for x in args.langs.split(",") if x.strip()])
    rng = random.Random(args.seed)
    budget = Budget(args.max_cost_inr)
    bodhan = Bodhan(Cache(args.cache_dir), budget)
    rows = [json.loads(line) for line in args.sources.read_text(encoding="utf-8-sig").splitlines() if line.strip()]

    # Clean items: reuse supplied translations, translate the rest.
    items = []
    for row in rows:
        for lang in ([row["lang"]] if row.get("lang") else langs):
            if lang not in langs:
                continue
            it = {"id": f"{row['id']}" if row.get("lang") else f"{row['id']}.{lang}", "source": row["source"], "lang": lang}
            if row.get("reference"):
                it["reference"] = row["reference"]
            try:
                it["translation"] = row.get("translation") or bodhan.translate(row["source"], lang)["translation"]
            except (ModelError, BudgetExceeded) as e:
                print(f"skip {it['id']}: {e}", file=sys.stderr)
                continue
            items.append(it)
    # Plain sentences only, so an injected addition does not also change Markdown/LaTeX structure.
    pool = {lang: [s for it in items if it["lang"] == lang for s in (it["translation"][a:b] for a, b in _sentences(it["translation"]))
                   if not re.search(r"[*$|#`\\]|^\s*[-\d]", s)] for lang in langs}
    pool["names"] = sorted({m.group(0) for r in rows for m in re.finditer(r"(?<=[a-z,;] )[A-Z][a-zA-Z]{2,}", r["source"])})
    inj = Injector(bodhan, rng, pool)

    # After shuffling, the first n_speech items get audio; within each block the first ones get an error.
    rng.shuffle(items)
    n_speech = round(len(items) * args.speech_share)
    n_speech_err = round(n_speech * 0.6)
    n_text_err = max(0, round(len(items) * args.error_share) - n_speech_err)
    out_rows, truth_rows, counts = [], [], {}
    for idx, it in enumerate(items):
        label = None
        is_speech = idx < n_speech
        wants_error = idx < n_speech_err if is_speech else idx - n_speech < n_text_err
        try:
            if wants_error and not is_speech:
                ops = TRANSLATION_OPS[:]
                rng.shuffle(ops)
                ops.sort(key=lambda o: counts.get(o, 0))  # keep categories balanced
                for op in ops:
                    res = getattr(inj, op)(it)
                    if res:
                        it["translation"], label = res
                        break
            if is_speech:
                wav, segs, _ = synthesize(bodhan, it["translation"], it["lang"])
                if wants_error:
                    op = min(SPEECH_OPS, key=lambda o: (counts.get(o, 0), rng.random()))
                    wav, label = inj.speech(op, wav, segs, it["translation"])
                args.audio_dir.mkdir(parents=True, exist_ok=True)
                path = args.audio_dir / f"{it['id']}.wav"
                path.write_bytes(wav)
                it["audio"] = Path(os.path.relpath(path.resolve(), args.out.parent.resolve())).as_posix()
        except (ModelError, BudgetExceeded) as e:
            print(f"{it['id']}: injection skipped ({e})", file=sys.stderr)
        labels = [label | {"severity": "major", "annotator": "injector"}] if label else []
        if label:
            counts[label["type"]] = counts.get(label["type"], 0) + 1
        truth_rows.append({"id": it["id"], "lang": it["lang"], "labels": labels})
        row = dict(it)
        if rng.random() < args.labelled_share:
            row["labels"] = labels
        out_rows.append(row)

    out_rows.sort(key=lambda r: (r["lang"], r["id"]))
    truth_rows.sort(key=lambda r: (r["lang"], r["id"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out_rows), encoding="utf-8")
    args.truth.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in truth_rows), encoding="utf-8")
    print(json.dumps({"items": len(out_rows), "seeded": counts, "clean": sum(1 for r in truth_rows if not r["labels"]),
                      "with_audio": sum(1 for r in out_rows if r.get("audio")), "spent_inr": round(budget.spent, 3)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
