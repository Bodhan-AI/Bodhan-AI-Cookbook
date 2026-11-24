"""Error injector: build labelled test cases from clean items, PROGRAMMATICALLY only.

Never uses an LLM to write Indian-language text. Text errors are made by deleting spans,
changing digits, swapping terms, inserting another item's sentence, or breaking markup. The
mistranslation kind perturbs the ENGLISH source and (optionally) re-translates with Indic-
Translate; without a client it is skipped. See speech_inject.py for separate audio mutations.

Every injection is deterministic given (seed, id) and emits a dataset line with `labels`,
`label_source: "injector"` so it is never confused with human labels.

CLI:  python -m bodhan_eval.inject --in clean.jsonl --out injected.jsonl [--clean-fraction 0.5]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from .align import split_sentences
from .entities import extract_terms
from .models import Span

TEXT_KINDS = ["omission", "addition", "number_unit", "term_entity", "format",
              "wrong_language_script"]


@dataclass
class Injection:
    translation: str
    label: dict  # {type, severity, span, span_space, annotator:"injector"}


def _rng(seed: int, item_id: str) -> random.Random:
    h = hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _label(t: str, span: Span | None, space: str, sev: str) -> dict:
    d = {"type": t, "severity": sev, "span_space": space, "label_source": "injector"}
    if span is not None:
        d["span"] = [span[0], span[1]]
    return d


def inject_omission(item: dict, rng: random.Random) -> Injection | None:
    tr = item.get("translation") or ""
    sents = split_sentences(tr)
    if len(sents) < 2:
        return None
    victim = sents[-1]
    new_tr = tr[: victim.span[0]].rstrip()
    # localize in the SOURCE: the last source sentence is the omitted content
    src_sents = split_sentences(item["source"])
    src_span = src_sents[-1].span if src_sents else (0, len(item["source"]))
    return Injection(new_tr, _label("omission", src_span, "source", "major"))


def inject_addition(item: dict, donor_translation: str, rng: random.Random) -> Injection | None:
    tr = item.get("translation") or ""
    donor_sents = split_sentences(donor_translation)
    if not donor_sents or not tr:
        return None
    add = donor_sents[0].text.strip()
    new_tr = tr.rstrip()
    joiner = " " if not new_tr.endswith((".", "।", "?", "!")) else " "
    start = len(new_tr) + len(joiner)
    new_tr = new_tr + joiner + add
    return Injection(new_tr, _label("addition", (start, len(new_tr)), "target", "major"))


def inject_number_unit(item: dict, rng: random.Random) -> Injection | None:
    tr = item.get("translation") or ""
    matches = list(re.finditer(r"\d+", tr))
    if not matches:
        return None
    m = rng.choice(matches)
    old = m.group()
    new = str((int(old) + rng.randint(1, 9)) if int(old) < 1000 else int(old) // 2 or 1)
    new_tr = tr[: m.start()] + new + tr[m.end():]
    span = (m.start(), m.start() + len(new))
    return Injection(new_tr, _label("number_unit", span, "target", "major"))


def inject_term_entity(item: dict, rng: random.Random) -> Injection | None:
    tr = item.get("translation") or ""
    terms = [t for t in extract_terms(item["source"]) if t.text in tr]
    if not terms:
        return None
    t = rng.choice(terms)
    idx = tr.find(t.text)
    replacement = t.text[::-1] if t.text.isascii() else "XYZ"
    new_tr = tr[:idx] + replacement + tr[idx + len(t.text):]
    return Injection(new_tr, _label("term_entity", (idx, idx + len(replacement)), "target", "major"))


def inject_format(item: dict, rng: random.Random) -> Injection | None:
    tr = item.get("translation") or ""
    # break the first LaTeX/markdown construct we can find
    for pat, _kind in ((r"\$", "math"), (r"\|", "table"), (r"^#{1,6}\s", "heading")):
        m = re.search(pat, tr, re.MULTILINE)
        if m:
            new_tr = tr[: m.start()] + tr[m.end():]  # delete the delimiter
            return Injection(new_tr, _label("format", (max(0, m.start() - 2), m.start() + 2),
                                            "target", "major"))
    return None


def inject_wrong_language(item: dict, rng: random.Random) -> Injection | None:
    # replace the translation with the untranslated English source
    src = item["source"]
    return Injection(src, _label("wrong_language_script", (0, len(src)), "target", "critical"))


_DISPATCH = {
    "omission": inject_omission,
    "number_unit": inject_number_unit,
    "term_entity": inject_term_entity,
    "format": inject_format,
    "wrong_language_script": inject_wrong_language,
}


def build_injected(items: list[dict], seed: int = 0, clean_fraction: float = 0.5) -> list[dict]:
    """Rotate injection kinds across non-clean items deterministically. Returns dataset lines."""
    out: list[dict] = []
    by_lang_translations: dict[str, list[str]] = {}
    for it in items:
        if it.get("translation"):
            by_lang_translations.setdefault(it["lang"], []).append(it["translation"])

    order = sorted(items, key=lambda it: hashlib.sha256(f"{seed}:{it['id']}".encode()).hexdigest())
    n_clean = int(round(len(order) * clean_fraction))
    kinds = TEXT_KINDS
    ki = 0
    for i, it in enumerate(order):
        line = dict(it)
        if i < n_clean or not it.get("translation"):
            out.append(line)
            continue
        rng = _rng(seed, it["id"])
        inj = None
        for _ in range(len(kinds)):
            kind = kinds[ki % len(kinds)]
            ki += 1
            if kind == "addition":
                donors = [t for t in by_lang_translations.get(it["lang"], [])
                          if t != it.get("translation")]
                inj = inject_addition(it, rng.choice(donors), rng) if donors else None
            else:
                inj = _DISPATCH[kind](it, rng)
            if inj is not None:
                break
        if inj is None:
            out.append(line)
            continue
        line["translation"] = inj.translation
        line["labels"] = [inj.label]
        line["label_source"] = "injector"
        out.append(line)
    return sorted(out, key=lambda it: it["id"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bodhan_eval.inject")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--clean-fraction", type=float, default=0.5)
    args = p.parse_args(argv)
    items = [json.loads(ln) for ln in Path(args.inp).read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    injected = build_injected(items, seed=args.seed, clean_fraction=args.clean_fraction)
    with Path(args.out).open("w", encoding="utf-8") as fh:
        for line in injected:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    labelled = sum(1 for x in injected if x.get("label_source") == "injector")
    print(f"Wrote {len(injected)} items ({labelled} injected, {len(injected) - labelled} clean) "
          f"to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
