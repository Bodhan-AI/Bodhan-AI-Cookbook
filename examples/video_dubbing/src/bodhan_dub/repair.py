"""Turn raw ASR segments into clean English dub units.

Indic-Transcribe output has no punctuation, spells numbers out, mishears words and
splits sentences across chunks. The LLM fixes that on the English side, which the
rules allow, and merges fragments into sentence-shaped units. It never hears audio
and never sees Indian-language text.

The LLM proposes; deterministic code decides:
  * `validate_units` checks ids, order, timing and fidelity to the raw words,
  * on failure the LLM is re-asked once with the exact errors,
  * if that fails too, `fallback_units` keeps the raw segmentation with light cleanup.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .llm import LLM, LLMError
from .transcribe import Segment

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepairParams:
    # Merging across a longer gap would make the dub speak over a real pause.
    max_merge_gap_s: float = 1.5
    # Keeps each unit's translation short enough for one or two TTS requests.
    max_unit_s: float = 25.0
    batch_size: int = 30
    # Share of raw words the cleaned text must keep (and vice versa); stops paraphrase.
    min_fidelity: float = 0.7
    min_fidelity_short: float = 0.5  # units under 6 words: one fixed word moves the score a lot


@dataclass
class Unit:
    id: str
    start: float
    end: float
    text: str  # cleaned English: what gets translated
    raw_text: str  # what Indic-Transcribe heard, kept for audit
    source_ids: list[str]
    forced_cut: bool = False
    repaired_by: str = "llm"  # "llm" or "fallback"
    dropped: str | None = None  # reason, when the LLM judged this not to be narration
    fidelity: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


SYSTEM_PROMPT = """You clean up raw English speech-recognition output from one video, so it can be translated and dubbed.

You get consecutive segments, each with an id, its gap in seconds from the previous segment, and the raw text.
The raw text has no punctuation, may be all caps, spells numbers as words and contains some misheard words.

Return units. Each unit covers one or more consecutive segment ids and has cleaned text:
- Add punctuation and normal capitalisation.
- Write numbers as digits ("twenty five percent" -> "25 percent", "nineteen forty seven" -> "1947", "two thousand minus five hundred" -> "2000 minus 500"). Keep units of measure and operators as words.
- Fix a word only when it is clearly misheard and the context makes the intended word obvious (e.g. "right it down" -> "write it down"). Otherwise keep the speaker's words exactly. Never paraphrase, summarise, add or remove content.
- Merge consecutive segments into one unit when a sentence runs across them. Never merge across a gap longer than {max_gap} seconds. A unit may span at most {max_unit} seconds.
- Keep figure labels and symbols as spoken, in capitals (e.g. "triangle XYZ", "point P", "vector v").
- If a segment is clearly not narration (music lyrics, noise transcribed as words, a lone filler), output it as its own unit with "drop": true and a short "reason".
- Use every id exactly once, in order.

Reply with JSON only:
{{"units": [{{"ids": ["s0001", "s0002"], "text": "...", "drop": false, "reason": ""}}]}}"""


# ---------------------------------------------------------------------------
# batching
# ---------------------------------------------------------------------------

def make_batches(segments: Sequence[Segment], params: RepairParams) -> list[list[Segment]]:
    """Split transcribed segments into LLM-sized batches, cutting only where no merge is allowed."""
    batches: list[list[Segment]] = []
    current: list[Segment] = []
    for seg in segments:
        if seg.skipped:
            continue
        if current and len(current) >= params.batch_size:
            # Cut at the last gap the LLM could not merge across; failing that, the widest gap.
            gaps = [(current[i].start - current[i - 1].end, i) for i in range(1, len(current))]
            safe = [i for gap, i in gaps if gap > params.max_merge_gap_s]
            cut = safe[-1] if safe else max(gaps)[1]
            batches.append(current[:cut])
            current = current[cut:]
        current.append(seg)
    if current:
        batches.append(current)
    return batches


def batch_prompt(batch: Sequence[Segment]) -> str:
    rows = []
    for i, seg in enumerate(batch):
        gap = 0.0 if i == 0 else seg.start - batch[i - 1].end
        rows.append({"id": seg.id, "gap_before_s": round(gap, 2), "duration_s": round(seg.end - seg.start, 2),
                     "text": seg.text})
    return json.dumps({"segments": rows}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# fidelity
# ---------------------------------------------------------------------------

_NUMBER_WORDS = set(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
    "million billion point and".split()
)
_TOKEN = re.compile(r"[a-z]+|\d+")


def _content_tokens(text: str, *, drop: set[str]) -> Counter[str]:
    return Counter(t for t in _TOKEN.findall(text.lower()) if t not in drop and not t.isdigit())


def fidelity(raw: str, cleaned: str) -> float:
    """Word overlap between raw ASR and cleaned text, ignoring how numbers are written.

    Symmetric: penalises dropped words and added words alike.
    """
    a = _content_tokens(raw, drop=_NUMBER_WORDS)
    b = _content_tokens(cleaned, drop=_NUMBER_WORDS)
    if not a and not b:
        return 1.0
    return sum((a & b).values()) / max(sum(a.values()), sum(b.values()))


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_units(reply: Any, batch: Sequence[Segment], params: RepairParams) -> tuple[list[Unit], list[str]]:
    """Check an LLM reply against the batch. Returns (units, errors); units are only usable if errors is empty."""
    errors: list[str] = []
    by_id = {s.id: s for s in batch}
    order = [s.id for s in batch]
    raw_units = reply.get("units") if isinstance(reply, dict) else None
    if not isinstance(raw_units, list) or not raw_units:
        return [], ['reply must be {"units": [...]} with at least one unit']

    used: list[str] = []
    units: list[Unit] = []
    for n, item in enumerate(raw_units):
        ids = item.get("ids") if isinstance(item, dict) else None
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            errors.append(f"unit {n}: 'ids' must be a non-empty list of segment ids")
            continue
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            errors.append(f"unit {n}: unknown ids {unknown}")
            continue
        positions = [order.index(i) for i in ids]
        if positions != list(range(positions[0], positions[0] + len(ids))):
            errors.append(f"unit {n}: ids {ids} are not consecutive")
            continue
        used.extend(ids)
        segs = [by_id[i] for i in ids]
        drop = bool(item.get("drop"))
        text = str(item.get("text") or "").strip()
        raw_text = " ".join(s.text.strip() for s in segs)

        for prev, nxt in zip(segs, segs[1:], strict=False):
            gap = nxt.start - prev.end
            if gap > params.max_merge_gap_s:
                errors.append(f"unit {n}: merges {prev.id} and {nxt.id} across a {gap:.1f}s gap "
                              f"(max {params.max_merge_gap_s}s); keep them in separate units")
        span = segs[-1].end - segs[0].start
        if span > params.max_unit_s:
            errors.append(f"unit {n}: spans {span:.1f}s (max {params.max_unit_s}s); split it at a sentence end")
        if not drop and not text:
            errors.append(f"unit {n}: empty text")

        score = fidelity(raw_text, text) if not drop else 1.0
        threshold = params.min_fidelity_short if len(raw_text.split()) < 6 else params.min_fidelity
        if not drop and score < threshold:
            errors.append(f"unit {n} ({ids}): text drifts from what was said (word overlap {score:.2f} < "
                          f"{threshold}); keep the speaker's words and only fix punctuation, numbers and "
                          f"clearly misheard words")

        units.append(Unit(
            id="", start=segs[0].start, end=segs[-1].end, text=text if not drop else "", raw_text=raw_text,
            source_ids=list(ids), forced_cut=any(s.forced_cut for s in segs),
            dropped=(str(item.get("reason") or "not narration") if drop else None), fidelity=round(score, 3),
        ))

    if sorted(used) != sorted(set(used)):
        errors.append(f"ids used more than once: {sorted(i for i in set(used) if used.count(i) > 1)}")
    missing = [i for i in order if i not in used]
    if missing:
        errors.append(f"ids missing from the reply: {missing}")
    if not errors and used != order:
        errors.append("units are out of order")
    return units, errors


# ---------------------------------------------------------------------------
# fallback
# ---------------------------------------------------------------------------

def basic_cleanup(text: str) -> str:
    """What we can do without judgment: tame all-caps, capitalise, end with a full stop."""
    text = " ".join(text.split())
    if text.isupper():
        text = text.lower()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    if text and text[-1] not in ".?!":
        text += "."
    return text


def fallback_units(batch: Sequence[Segment]) -> list[Unit]:
    return [Unit(id="", start=s.start, end=s.end, text=basic_cleanup(s.text), raw_text=s.text,
                 source_ids=[s.id], forced_cut=s.forced_cut, repaired_by="fallback") for s in batch]


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

@dataclass
class RepairReport:
    batches: int = 0
    llm_ok: int = 0
    retried: int = 0
    fell_back: int = 0
    splits: int = 0
    errors: list[str] = field(default_factory=list)


MIN_SPLIT_BATCH = 6


def split_batch(batch: Sequence[Segment]) -> tuple[list[Segment], list[Segment]]:
    """Halve a batch at the widest pause in its middle half, the least likely place to split a sentence."""
    n = len(batch)
    lo, hi = max(1, n // 4), max(2, n - n // 4)
    cut = max(range(lo, hi), key=lambda i: (batch[i].start - batch[i - 1].end, -abs(i - n // 2)))
    return list(batch[:cut]), list(batch[cut:])


def repair_batch(llm: LLM | None, batch: Sequence[Segment], params: RepairParams,
                 report: RepairReport) -> list[Unit]:
    if llm is None:
        report.fell_back += 1
        return fallback_units(batch)
    system = SYSTEM_PROMPT.format(max_gap=params.max_merge_gap_s, max_unit=params.max_unit_s)
    user = batch_prompt(batch)
    ctx = {"stage": "repair", "first": batch[0].id, "last": batch[-1].id}
    for attempt in (1, 2):
        try:
            reply = llm.complete_json(system, user)
        except LLMError as exc:
            log.warning("repair: LLM call failed", extra={**ctx, "attempt": attempt, "error": str(exc)})
            report.errors.append(f"{batch[0].id}..{batch[-1].id}: {exc}")
            if len(batch) >= MIN_SPLIT_BATCH:
                # Most often the reply was cut off at the provider's token limit: halve the work.
                first, second = split_batch(batch)
                log.info("repair: splitting batch", extra={**ctx, "sizes": [len(first), len(second)]})
                report.splits += 1
                return repair_batch(llm, first, params, report) + repair_batch(llm, second, params, report)
            break
        units, errors = validate_units(reply, batch, params)
        if not errors:
            report.llm_ok += 1
            report.retried += attempt - 1
            return units
        log.warning("repair: reply failed validation", extra={**ctx, "attempt": attempt, "errors": errors})
        report.errors.extend(f"{batch[0].id}..{batch[-1].id} attempt {attempt}: {e}" for e in errors)
        # Agentic retry: show the model exactly what was wrong with its answer.
        user = (batch_prompt(batch) + "\n\nYour previous reply was rejected:\n- " + "\n- ".join(errors)
                + "\nReturn the full corrected JSON for all segments.")
    report.fell_back += 1
    log.warning("repair: falling back to raw segmentation", extra=ctx)
    return fallback_units(batch)


def repair_transcript(llm: LLM | None, segments: Sequence[Segment],
                      params: RepairParams | None = None) -> tuple[list[Unit], RepairReport]:
    params = params or RepairParams()
    report = RepairReport()
    units: list[Unit] = []
    for batch in make_batches(segments, params):
        report.batches += 1
        units.extend(repair_batch(llm, batch, params, report))
    for n, unit in enumerate(units):
        unit.id = f"u{n:04d}"
        log.info("unit ready", extra={"stage": "repair", "unit": unit.id, "sources": unit.source_ids,
                                      "start": unit.start, "end": unit.end, "by": unit.repaired_by,
                                      "fidelity": unit.fidelity, "dropped": unit.dropped})
    return units, report
