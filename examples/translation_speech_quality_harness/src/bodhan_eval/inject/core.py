"""The method table, the per-item choice, and the command.

    python -m bodhan_eval.inject --dataset clean.jsonl --out injected.jsonl [--rate 0.5] [--seed 7]
        [--langs hi,ta] [--categories number,format] [--audio-out dir] [--with-models] [--max-cost-inr 5]

Per item, a random generator seeded from (seed, id, lang) decides clean or not (probability ``--rate``), then
picks a category among those with a usable method, then a method; if the method does not apply to this text
(no number to change...) the next one is tried, then the next category. Each method draws its own random
choices from (seed, id, lang, method), so one method failing never shifts another's choices, and an item's
fate never depends on the order of the file. One error per item; the label carries the method's category and
severity and ``"annotator": "injector:<method>"``.

Items that already have labels (for example Saksham's) are copied through untouched, and so are the items
listed in ``--skip`` (e.g. a review sheet still being filled in), which stay unlabelled. When a translation
changes, the old audio no longer matches it: with ``--with-models`` and ``--audio-out`` it is synthesised
again with Indic-Speak, otherwise it is dropped (counted in the summary).

Outputs: the JSONL (audio paths relative to it) and ``<out>.inject.json`` with counts. Both are deterministic.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import logging
import os
import random
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .. import audio as A
from .. import report
from ..budget import Budget, BudgetExceeded
from ..cache import Cache
from ..cli import _non_negative, _positive_int, parse_langs, setup_logging
from ..clients import BodhanClient, BodhanError, ServiceUnavailable
from ..config import CATEGORIES, Settings, canonical_category, normalize_lang_code
from ..dataset import load_dataset
from ..generate import synthesize
from ..models import Item, Label
from . import english, speech, text
from .base import Edit, InjectContext

log = logging.getLogger("bodhan_eval.inject")


@dataclass(frozen=True)
class Method:
    name: str
    category: str
    severity: str
    needs: str  # "text": the translation · "models": Indic-Translate/-Transliterate · "audio": a waveform
    fn: Callable[[Item, random.Random, InjectContext], Edit | None]


METHODS: list[Method] = [
    Method("drop_phrase", "omission", "minor", "text", text.drop_phrase),
    Method("drop_clause", "omission", "major", "text", text.drop_clause),
    Method("drop_sentence", "omission", "critical", "text", text.drop_sentence),
    Method("extra_clause", "addition", "minor", "models", english.extra_clause),
    Method("repeat_sentence", "addition", "major", "text", text.repeat_sentence),
    Method("foreign_sentence", "addition", "critical", "text", text.foreign_sentence),
    Method("quantifier_swap", "mistranslation", "minor", "models", english.quantifier_swap),
    Method("word_swap", "mistranslation", "major", "text", text.word_swap),
    Method("antonym_swap", "mistranslation", "major", "models", english.antonym_swap),
    Method("relation_swap", "mistranslation", "major", "models", english.relation_swap),
    Method("negation", "mistranslation", "critical", "models", english.negation),
    Method("last_digit", "number", "minor", "text", text.last_digit),
    Method("first_digit", "number", "major", "text", text.first_digit),
    Method("unit_swap", "number", "major", "models", english.unit_swap),
    Method("month_swap", "number", "major", "models", english.month_swap),
    Method("decimal_shift", "number", "critical", "text", text.decimal_shift),
    Method("garble_entity", "term", "minor", "text", text.garble_entity),
    Method("entity_swap", "term", "major", "text", text.entity_swap),
    Method("heading_marker", "format", "minor", "text", text.heading_marker),
    Method("bold_marker", "format", "minor", "text", text.bold_marker),
    Method("table_pipe", "format", "major", "text", text.table_pipe),
    Method("latex_command", "format", "major", "text", text.latex_command),
    Method("table_separator", "format", "critical", "text", text.table_separator),
    Method("math_dollar", "format", "critical", "text", text.math_dollar),
    Method("english_sentence", "wrong_language", "major", "text", text.english_sentence),
    Method("source_english", "wrong_language", "critical", "text", text.source_english),
    Method("other_language", "wrong_language", "critical", "text", text.other_language),
    Method("truncate_tail", "speech_omission", "critical", "audio", speech.truncate_tail),
    Method("cut_middle", "speech_omission", "major", "audio", speech.cut_middle),
    Method("repeat_stretch", "speech_repetition", "major", "audio", speech.repeat_stretch),
    Method("insert_silence", "speech_garbled", "minor", "audio", speech.insert_silence),
    Method("noise_burst", "speech_garbled", "major", "audio", speech.noise_burst),
    Method("clip_region", "speech_garbled", "major", "audio", speech.clip_region),
]


def item_rng(seed: int, item: Item, method: str = "") -> random.Random:
    """A random generator that depends only on the seed, the item and (optionally) the method."""
    digest = hashlib.sha256(f"{seed}|{item.id}|{item.lang}|{method}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def usable(m: Method, item: Item, ctx: InjectContext, categories: set[str] | None) -> bool:
    if categories is not None and m.category not in categories:
        return False
    if not item.translation:
        return False
    if m.needs == "models":
        return ctx.bodhan is not None
    if m.needs == "audio":
        return bool(item.audio) and ctx.audio_out is not None
    return True


@dataclass
class Outcome:
    row: dict
    status: str  # clean | injected | passed_through | not_injectable | skipped
    method: Method | None = None
    audio: str = ""  # edited | resynthesised | dropped | "" (unchanged or none)
    tried: list[str] = field(default_factory=list)


def inject_item(
    item: Item,
    ctx: InjectContext,
    seed: int,
    rate: float,
    categories: set[str] | None,
    out_dir: Path,
    skip: frozenset[str] = frozenset(),
) -> Outcome:
    if item.key in skip:  # under human review: copied as generated, unlabelled until the review's labels arrive
        return Outcome(_row(item, None, out_dir), "skipped")
    if item.labels:
        return Outcome(_row(item, [_label_dict(lab) for lab in item.labels], out_dir), "passed_through")
    rng = item_rng(seed, item)
    if rng.random() >= rate:
        return Outcome(_row(item, [], out_dir), "clean")
    cats = sorted({m.category for m in METHODS if usable(m, item, ctx, categories)})
    rng.shuffle(cats)
    tried: list[str] = []
    for cat in cats:
        methods = [m for m in METHODS if m.category == cat and usable(m, item, ctx, categories)]
        rng.shuffle(methods)
        for m in methods:
            tried.append(m.name)
            try:
                edit = m.fn(item, item_rng(seed, item, m.name), ctx)
            except (BodhanError, ServiceUnavailable, BudgetExceeded) as e:
                log.warning("%s on %s: %s", m.name, item.key, e)
                edit = None
            if edit is not None:
                return _apply(item, m, edit, ctx, out_dir, tried)
    return Outcome(_row(item, [], out_dir), "not_injectable", tried=tried)


def _apply(item: Item, m: Method, edit: Edit, ctx: InjectContext, out_dir: Path, tried: list[str]) -> Outcome:
    new = dataclasses.replace(item, translation=edit.translation or item.translation)
    audio = ""
    if edit.audio is not None:
        assert ctx.audio_out is not None
        path = ctx.audio_out / f"{_safe(item.id)}.{item.lang}.{m.name}.wav"
        A.write_wav(path, edit.audio, edit.sr)
        new.audio, audio = str(path.resolve()), "edited"
    elif edit.translation is not None and item.audio:
        new.audio, audio = _resynthesise(new, m, ctx)
    label = {
        "type": m.category,
        "severity": m.severity,
        "span": list(edit.span),
        "span_on": edit.span_on,
        "annotator": f"injector:{m.name}",
    }
    return Outcome(_row(new, [label], out_dir), "injected", m, audio, tried)


def _resynthesise(item: Item, m: Method, ctx: InjectContext) -> tuple[str | None, str]:
    """New audio for a changed translation (Indic-Speak), or none: stale audio would be a second, unlabelled error."""
    if ctx.bodhan is None or ctx.audio_out is None:
        return None, "dropped"
    try:
        return synthesize(dataclasses.replace(item, id=f"{item.id}.{m.name}"), ctx, ctx.audio_out), "resynthesised"
    except (BodhanError, ServiceUnavailable, BudgetExceeded, ValueError, IndexError) as e:
        log.warning("no new audio for %s: %s", item.key, e)
        return None, "dropped"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


def _label_dict(lab: Label) -> dict:
    return {
        "type": lab.raw_type,
        "severity": lab.severity,
        "span": list(lab.span) if lab.span else None,
        "span_on": lab.span_on,
        "annotator": lab.annotator,
    }


def _rel(path: str | None, base: Path) -> str | None:
    if not path:
        return None
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:  # another drive on Windows
        return Path(path).as_posix()


def _row(item: Item, labels: list[dict] | None, out_dir: Path) -> dict:
    row: dict = {"id": item.id, "lang": item.lang, "source": item.source}
    if item.reference is not None:
        row["reference"] = item.reference
    row.update(translation=item.translation, audio=_rel(item.audio, out_dir))
    if labels is not None:
        row["labels"] = labels
    return row


def read_skip(path: Path) -> frozenset[str]:
    """``id::lang`` keys from a CSV (e.g. a review sheet) or JSONL file with ``id`` and ``lang``."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    else:
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return frozenset(f"{r['id']}::{normalize_lang_code(str(r['lang']))}" for r in rows if r.get("id") and r.get("lang"))


def summarize(outcomes: list[Outcome], args: argparse.Namespace) -> dict:
    injected = [o for o in outcomes if o.method is not None]
    statuses = Counter(o.status for o in outcomes)
    audio = Counter(o.audio for o in outcomes if o.audio)
    return {
        "seed": args.seed,
        "rate": args.rate,
        "with_models": args.with_models,
        "categories": sorted(args.categories) if args.categories else "all",
        "items": len(outcomes),
        "clean": statuses["clean"],
        "injected": statuses["injected"],
        "passed_through": statuses["passed_through"],
        "not_injectable": statuses["not_injectable"],
        "skipped": statuses["skipped"],
        "per_category": dict(Counter(o.method.category for o in injected if o.method)),
        "per_method": dict(Counter(o.method.name for o in injected if o.method)),
        "per_severity": dict(Counter(o.method.severity for o in injected if o.method)),
        "per_language": {
            lang: {"items": n, "injected": sum(1 for o in injected if o.row["lang"] == lang)}
            for lang, n in sorted(Counter(o.row["lang"] for o in outcomes).items())
        },
        "audio_edited": audio["edited"],
        "audio_resynthesised": audio["resynthesised"],
        "audio_dropped": audio["dropped"],
    }


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


def _categories(value: str) -> set[str]:
    out = set()
    for part in (p.strip() for p in value.split(",") if p.strip()):
        cat = canonical_category(part)
        if cat is None:
            raise argparse.ArgumentTypeError(f"unknown category {part!r}; known: {', '.join(CATEGORIES)}")
        out.add(cat)
    return out


def _rate(value: str) -> float:
    r = float(value)
    if not 0 <= r <= 1:
        raise argparse.ArgumentTypeError("must be in [0, 1]")
    return r


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m bodhan_eval.inject", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dataset", type=Path, required=True, help="clean JSONL with translations (and audio)")
    ap.add_argument("--out", type=Path, required=True, help="injected JSONL to write")
    ap.add_argument("--rate", type=_rate, default=0.5, help="share of items that get an error (default 0.5)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--langs", type=parse_langs, default=None, help="only these languages (default: all)")
    ap.add_argument("--categories", type=_categories, default=None, help="only these categories (default: all)")
    ap.add_argument("--audio-out", type=Path, help="folder for edited and re-synthesised audio (speech errors)")
    ap.add_argument("--skip", type=Path, help="CSV/JSONL of id+lang to copy through untouched and unlabelled")
    ap.add_argument("--with-models", action="store_true", help="also use Indic-Translate/-Transliterate/-Speak")
    ap.add_argument("--max-cost-inr", type=_non_negative, default=5.0, help="Bodhan spend cap with --with-models")
    ap.add_argument("--cache-dir", type=Path, default=Path(".cache/bodhan_eval"))
    ap.add_argument("--concurrency", type=_positive_int, default=6)
    ap.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    try:
        items, rep = load_dataset(args.dataset, args.langs)
    except (OSError, UnicodeDecodeError) as e:
        log.error("cannot read %s: %s", args.dataset, e)
        return 1
    for bad in rep.bad:
        log.warning("line %s skipped: %s", bad["line"], bad["reason"])
    cache = budget = bodhan = None
    if args.with_models:
        settings = Settings.from_env()
        settings.concurrency = args.concurrency
        cache, budget = Cache(args.cache_dir), Budget(args.max_cost_inr)
        bodhan = BodhanClient(settings, cache, budget)
        if not bodhan.available("translate"):
            log.error("--with-models needs BODHAN_TRANSLATE_KEY (or BODHAN_API_KEY)")
            cache.close()
            return 1
    if args.audio_out:
        args.audio_out.mkdir(parents=True, exist_ok=True)
    ctx = InjectContext(items, bodhan=bodhan, audio_out=args.audio_out)
    skip = read_skip(args.skip) if args.skip else frozenset()
    out_dir = args.out.resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            outcomes = list(
                pool.map(lambda it: inject_item(it, ctx, args.seed, args.rate, args.categories, out_dir, skip), items)
            )
    finally:
        if cache is not None:
            cache.close()
    report.write_jsonl(args.out, [o.row for o in outcomes])
    summary = summarize(outcomes, args)
    report.write_json(args.out.with_suffix(".inject.json"), summary)
    log.info(
        "%d items: %d injected, %d clean, %d already labelled, %d with nothing to inject; per category %s",
        summary["items"],
        summary["injected"],
        summary["clean"],
        summary["passed_through"],
        summary["not_injectable"],
        summary["per_category"],
    )
    if budget is not None:
        log.info("Bodhan spend: ₹%.4f", budget.total_spent)
    return 0
