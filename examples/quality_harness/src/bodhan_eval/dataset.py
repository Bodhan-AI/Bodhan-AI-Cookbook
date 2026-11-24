"""Load a JSONL dataset. Bad lines are logged, skipped and counted; they never stop the run.

Text fields are kept exactly as given: label spans and every flag we report are offsets into the
original strings, so normalization happens later, on copies.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Primary code plus an optional script subtag, the form Bodhan uses: hi, mni-Beng, ks-Deva, sd-Arab.
_LANG_RE = re.compile(r"^([a-z]{2,3})(?:-([a-z]{4}))?$", re.IGNORECASE)

# Line-level Markdown (heading, list, quote, table row, code fence) or inline bold, link, code.
_MARKDOWN = re.compile(
    r"^ {0,3}(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|\|.*\||```)|\*\*\S|\[[^\]\n]+\]\([^)\n]+\)|`[^`\n]+`",
    re.MULTILINE,
)
# Display math, \( \[, \command{, or inline $...$ that doesn't start or end with a space (so "$5 and $10" is not math).
_LATEX = re.compile(r"\$\$|\\\(|\\\[|\\[a-zA-Z]+\{|\$[^$\s](?:[^$\n]*[^$\s])?\$(?!\d)")

KNOWN_FIELDS = {"id", "source", "lang", "reference", "translation", "audio", "labels"}


def normalize_lang(code: str) -> str | None:
    """'HI' -> 'hi', 'mni-beng' -> 'mni-Beng'. None if it isn't a language code."""
    m = _LANG_RE.match(code.strip())
    if not m:
        return None
    base, script = m.groups()
    return base.lower() + (f"-{script.title()}" if script else "")


def content_type(text: str) -> str:
    md, tex = bool(_MARKDOWN.search(text)), bool(_LATEX.search(text))
    return "mixed" if md and tex else "markdown" if md else "latex" if tex else "plain"


@dataclass
class Item:
    id: str
    lang: str
    source: str
    line: int
    reference: str | None = None
    translation: str | None = None
    audio: Path | None = None
    labels: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # unknown fields, passed through untouched
    content_type: str = "plain"
    warnings: list[str] = field(default_factory=list)
    # Where each output came from: supplied, generated, missing, error, skipped_budget, not_sampled, empty.
    status: dict[str, str] = field(default_factory=dict)
    # Generated audio only: [{"text", "start_s", "end_s"}] per spoken chunk, since ASR gives no timestamps.
    audio_segments: list[dict] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        # (id, lang), not id alone: a dataset may reuse one id for the same source in several languages.
        return (self.id, self.lang)


@dataclass
class Skipped:
    line: int
    reason: str
    id: str | None = None


@dataclass
class Dataset:
    items: list[Item]
    skipped: list[Skipped]
    filtered_out: int  # valid items in languages not asked for with --langs


def load_dataset(path: Path, langs: Iterable[str] | None = None) -> Dataset:
    wanted = set(langs) if langs else None
    items: list[Item] = []
    skipped: list[Skipped] = []
    filtered_out = 0
    first_line: dict[tuple[str, str], int] = {}

    def skip(line: int, reason: str, item_id: str | None = None) -> None:
        log.warning("skipping line %d%s: %s", line, f" ({item_id})" if item_id else "", reason)
        skipped.append(Skipped(line, reason, item_id))

    with path.open("rb") as f:  # bytes, so one badly encoded line can't sink the whole file
        for n, raw in enumerate(f, 1):
            try:
                text = raw.decode("utf-8-sig" if n == 1 else "utf-8").strip()
            except UnicodeDecodeError:
                skip(n, "not valid UTF-8")
                continue
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as e:
                skip(n, f"invalid JSON: {e.msg}")
                continue
            try:
                item = _parse(obj, n, path.parent)
            except ValueError as e:
                raw_id = obj.get("id") if isinstance(obj, dict) else None
                skip(n, str(e), str(raw_id) if raw_id is not None else None)
                continue
            if wanted is not None and item.lang not in wanted:
                filtered_out += 1
                continue
            if item.key in first_line:
                skip(n, f"duplicate id for lang {item.lang!r} (first seen on line {first_line[item.key]})", item.id)
                continue
            first_line[item.key] = n
            for warning in item.warnings:
                log.warning("line %d (%s): %s", n, item.id, warning)
            items.append(item)

    return Dataset(items, skipped, filtered_out)


def _parse(obj: object, line: int, base_dir: Path) -> Item:
    if not isinstance(obj, dict):
        raise ValueError("line is not a JSON object")
    item_id = obj.get("id")
    if isinstance(item_id, bool) or not isinstance(item_id, (str, int)) or not str(item_id).strip():
        raise ValueError("missing or invalid 'id'")
    source = obj.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("missing or empty 'source'")
    raw_lang = obj.get("lang")
    lang = normalize_lang(raw_lang) if isinstance(raw_lang, str) else None
    if not lang:
        raise ValueError(f"missing or invalid 'lang': {raw_lang!r}")

    item = Item(
        id=str(item_id).strip(),
        lang=lang,
        source=source,
        line=line,
        extra={k: v for k, v in obj.items() if k not in KNOWN_FIELDS},
        content_type=content_type(source),
    )

    # Optional fields: a bad one is dropped with a warning; the item is still scored.
    # An empty translation is kept: it is a (badly broken) system output, not a missing one.
    for name in ("reference", "translation"):
        value = obj.get(name)
        if isinstance(value, str):
            setattr(item, name, value)
        elif value is not None:
            item.warnings.append(f"dropped '{name}': expected a string")

    audio = obj.get("audio")
    if audio is not None:
        if not isinstance(audio, str) or not audio.strip():
            item.warnings.append("dropped 'audio': expected a file path")
        elif not (base_dir / audio).is_file():  # relative paths resolve against the dataset's folder
            item.warnings.append(f"dropped 'audio': file not found: {audio}")
        else:
            item.audio = base_dir / audio

    item.labels = _labels(obj.get("labels"), item)
    return item


def _labels(raw: object, item: Item) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        item.warnings.append("dropped 'labels': expected a list")
        return []
    # Which text a span points into is not fixed yet (plan Q4), so bound-check against the longest one.
    longest = max(len(item.source), len(item.translation or ""), len(item.reference or ""))
    kept = []
    for i, label in enumerate(raw):
        problem = _label_problem(label, longest)
        if problem:
            item.warnings.append(f"dropped label {i}: {problem}")
        else:
            kept.append(label)
    return kept


def _label_problem(label: object, longest: int) -> str | None:
    if not isinstance(label, dict):
        return "expected an object"
    if not isinstance(label.get("type"), str) or not label["type"].strip():
        return "missing 'type'"
    span = label.get("span")
    if span is None:
        return None
    numbers = isinstance(span, list) and len(span) == 2 and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) for x in span
    )
    if not numbers:
        return f"'span' must be [start, end], got {span!r}"
    start, end = span
    if not 0 <= start <= end:
        return f"invalid span {span}"
    # Integer spans are character offsets; float spans may be audio seconds, so they are not bounded by text length.
    if isinstance(start, int) and isinstance(end, int) and end > longest:
        return f"span {span} runs past the end of the text"
    return None
