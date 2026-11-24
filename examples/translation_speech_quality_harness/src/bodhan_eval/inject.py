"""inject.base: Shared types for the error injector.
inject.text_edits: Text recipes: programmatic edits of a real Indic-Translate output.
inject.perturb: Mistranslation recipe: perturb the ENGLISH source, re-translate with Indic-Translate.
inject.audio_edits: Audio recipes: edits of an EXISTING clean Indic-Speak clip (numpy only, no ffmpeg).
inject.build: Turn clean items into a labelled test set (about half stays clean).
"""

from __future__ import annotations

import difflib
import random
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .audio import frame_rms as _frame_rms
from .audio import read_audio, snap
from .checks import english_number_words, extract_numbers
from .text import Region, clauses, english_entities, find_protected, normalize, normalize_with_map, sentences

# -------------------- base
SEVERITY = {
    "omission.sentence": "critical",
    "omission.clause": "major",
    "addition.sentence": "major",
    "mistranslation.antonym": "major",
    "number_unit.change_digit": "major",
    "number_unit.swap_digits": "major",
    "number_unit.add_zero": "critical",
    "number_unit.drop_zero": "critical",
    "number_unit.unit_swap": "major",
    "term_entity.replace": "major",
    "term_entity.inconsistent": "major",
    "format.math_dollar": "major",
    "format.table_pipe": "minor",
    "format.code_fence": "major",
    "format.link_target": "major",
    "wrong_language_script.untranslated": "critical",
    "wrong_language_script.other_language": "critical",
    "speech_omission_truncation.tail_cut": "major",  # "critical" when >= 40% of the clip is cut
    "speech_repetition.loop": "major",
    "speech_garbled.silence": "major",  # "minor" when shorter than 1.5 s
    "speech_garbled.noise_burst": "major",
    "speech_garbled.noise_bed": "minor",
}


@dataclass(frozen=True)
class CleanItem:
    """One clean (unedited) model output that recipes can start from."""

    id: str
    src_id: str  # groups the same English source across languages
    lang: str
    source: str
    translation: str
    reference: str | None = None
    audio_path: str | None = None  # clean clip of ``translation`` (if any)


@dataclass
class Pool:
    """Real model outputs that recipes may splice from (never invented text)."""

    items: list[CleanItem]
    # (english entity, lang) -> Indic-Translate output for the entity alone
    entity_translations: dict[tuple[str, str], str] = field(default_factory=dict)

    def same_lang_others(self, item: CleanItem) -> list[CleanItem]:
        return sorted((x for x in self.items if x.lang == item.lang and x.src_id != item.src_id), key=lambda x: x.id)

    def siblings(self, item: CleanItem) -> list[CleanItem]:
        """The same source translated into the other languages."""
        return sorted((x for x in self.items if x.src_id == item.src_id and x.lang != item.lang), key=lambda x: x.id)


@dataclass
class Injection:
    recipe: str
    labels: list[dict]
    text: str | None = None  # new translation (text recipes)
    samples: np.ndarray | None = None  # new audio (audio recipes)
    sr: int | None = None


def rng_for(seed: int, item_id: str, recipe: str) -> random.Random:
    """Per (item, recipe) RNG: results do not depend on processing order.
    ``random.Random`` seeded with a str is stable across runs and machines."""
    return random.Random(f"{seed}:{item_id}:{recipe}")


def make_label(
    recipe: str, span: tuple[float, float], note: str, *, severity: str | None = None, unit: str = "chars", fld: str = "translation", **extra
) -> dict:
    category = recipe.split(".", 1)[0]
    if unit == "seconds":
        # 6 decimals keeps spans exact to the sample at 16–48 kHz
        span_out = [round(float(span[0]), 6), round(float(span[1]), 6)]
    else:
        span_out = [int(span[0]), int(span[1])]
    label = {
        "type": category,
        "severity": severity or SEVERITY[recipe],
        "span": span_out,
        "span_unit": unit,
        "field": fld,
        "source": "injector",
        "recipe": recipe,
        "note": note,
    }
    label.update(extra)
    return label


# -------------------- text_edits
def line_span(text: str, pos: int) -> tuple[int, int]:
    """[start, end) of the line containing ``pos`` (end excludes the newline)."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return start, (len(text) if end == -1 else end)


def _touches_regions(span: tuple[int, int], regions: list[Region]) -> bool:
    return any(span[0] < r.end and span[1] > r.start for r in regions)


def _is_prose(text: str, span: tuple[int, int], regions: list[Region]) -> bool:
    """A plain sentence: no code/LaTeX/URL inside, not a table row, heading or list item."""
    if _touches_regions(span, regions):
        return False
    ls, le = line_span(text, span[0])
    line = text[ls:le].lstrip()
    return not (line.startswith(("|", "#", "-", "*", ">")) or re.match(r"\d+[.)]\s", line))


def _word_count(s: str) -> int:
    return len(s.split())


def omission(item: CleanItem, pool: Pool, rng: random.Random) -> Injection | None:
    """Delete one sentence (when there are >= 2) or one non-final clause."""
    t = item.translation
    regions = find_protected(t)
    sents = sentences(t, regions)
    prose = [s for s in sents if _is_prose(t, s, regions)]
    options: list[tuple[str, tuple[int, int], int]] = []  # (kind, deletion range, sentence index)
    if len(sents) >= 2:
        for s in prose:
            a, b = s
            j = b
            while j < len(t) and t[j] in " \t":
                j += 1
            if j < len(t) and t[j] != "\n":  # more text follows on the same line
                options.append(("sentence", (a, j), sents.index(s)))
            else:
                i = a
                while i > 0 and t[i - 1] in " \t":
                    i -= 1
                if i > 0 and t[i - 1] != "\n":  # text precedes on the same line
                    options.append(("sentence", (i, b), sents.index(s)))
    for s in prose:
        cl = clauses(t, s, regions)
        for k in range(len(cl) - 1):  # never the final clause (it carries the terminator)
            a, b = cl[k]
            if _word_count(t[a:b]) >= 2:
                options.append(("clause", (a, cl[k + 1][0]), sents.index(s)))
    if not options:
        return None
    kinds = sorted({o[0] for o in options})
    kind = rng.choice(kinds)
    choice = rng.choice([o for o in options if o[0] == kind])
    (a, b), sent_idx = choice[1], choice[2]
    new = t[:a] + t[b:]
    recipe = f"omission.{kind}"
    src_count = len(sentences(item.source))
    extra = {"deleted_chars": b - a}
    if src_count == len(sents):
        extra["source_sentence_index"] = sent_idx
    label = make_label(recipe, (a, a), f"A {kind} was deleted from the translation at this point ({b - a} characters).", **extra)
    return Injection(recipe, [label], text=new)


def addition(item: CleanItem, pool: Pool, rng: random.Random) -> Injection | None:
    """Insert a sentence copied from another item's real translation (same language)."""
    t = item.translation
    regions = find_protected(t)
    sents = sentences(t, regions)
    points = [s[1] for s in sents if _is_prose(t, s, regions)]
    if not points:
        return None
    candidates: list[str] = []
    for other in pool.same_lang_others(item):
        ot = other.translation
        oreg = find_protected(ot)
        for s in sentences(ot, oreg):
            frag = ot[s[0] : s[1]]
            if _is_prose(ot, s, oreg) and 15 <= len(frag) <= 160 and _word_count(frag) >= 3 and frag not in t:
                candidates.append(frag)
    if not candidates:
        return None
    frag = rng.choice(sorted(set(candidates)))
    p = rng.choice(points)
    new = t[:p] + " " + frag + t[p:]
    start = p + 1
    label = make_label("addition.sentence", (start, start + len(frag)), "A sentence from an unrelated item was inserted.")
    return Injection("addition.sentence", [label], text=new)


UNIT_SWAPS = {"°C": "°F", "°F": "°C", "km": "mi", "mi": "km", "kg": "lb", "lb": "kg", "cm": "mm", "mm": "cm", "km/h": "mph", "mph": "km/h"}


def _digit_values(tok: str) -> list[int] | None:
    try:
        return [int(unicodedata.decimal(c)) for c in tok]
    except (TypeError, ValueError):
        return None


def _render_digits(values: list[int], like: str) -> str:
    """Write digit values in the same script as the first character of ``like``."""
    zero = ord(like[0]) - int(unicodedata.decimal(like[0]))
    return "".join(chr(zero + v) for v in values)


def _mutations(vals: list[int]) -> list[tuple[str, list[int]]]:
    out: list[tuple[str, list[int]]] = []
    for i, v in enumerate(vals):
        lo = 1 if i == 0 else 0  # never create a leading zero (or the number 0)
        for d in range(lo, 10):
            if d != v:
                out.append(("change_digit", vals[:i] + [d] + vals[i + 1 :]))
    for i in range(len(vals) - 1):
        if vals[i] != vals[i + 1] and not (i == 0 and vals[1] == 0):
            out.append(("swap_digits", vals[:i] + [vals[i + 1], vals[i]] + vals[i + 2 :]))
    out.append(("add_zero", vals + [0]))
    if len(vals) >= 2 and vals[-1] == 0:
        out.append(("drop_zero", vals[:-1]))
    return out


def number_unit(item: CleanItem, pool: Pool, rng: random.Random) -> Injection | None:
    """Change a number that also appears in the source, or swap a Latin unit symbol."""
    t = item.translation
    src_values = {n.value for n in extract_numbers(item.source)} | set(english_number_words(item.source))
    seen: set[tuple[int, int]] = set()
    digit_tokens = []
    unit_tokens = []
    for n in extract_numbers(t):
        if (n.start, n.end) in seen:
            continue
        seen.add((n.start, n.end))
        tok = t[n.start : n.end]
        vals = _digit_values(tok)
        if vals is not None and n.value in src_values:
            digit_tokens.append((n.start, n.end, tok, vals))
        if n.unit in UNIT_SWAPS and t[n.unit_end - len(n.unit) : n.unit_end] == n.unit:
            unit_tokens.append((n.unit_end - len(n.unit), n.unit_end, n.unit))
    kinds = (["digits"] if digit_tokens else []) + (["unit"] if unit_tokens else [])
    if not kinds:
        return None
    if rng.choice(kinds) == "unit":
        a, b, unit = rng.choice(unit_tokens)
        repl = UNIT_SWAPS[unit]
        new = t[:a] + repl + t[b:]
        label = make_label("number_unit.unit_swap", (a, a + len(repl)), f"Unit '{unit}' was changed to '{repl}'.")
        return Injection("number_unit.unit_swap", [label], text=new)
    a, b, tok, vals = rng.choice(digit_tokens)
    muts = [m for m in _mutations(vals) if str(int("".join(map(str, m[1])))) not in src_values]
    if not muts:
        return None
    kind = rng.choice(sorted({k for k, _ in muts}))
    new_vals = rng.choice([v for k, v in muts if k == kind])
    repl = _render_digits(new_vals, tok)
    new = t[:a] + repl + t[b:]
    old_ascii, new_ascii = "".join(map(str, vals)), "".join(map(str, new_vals))
    recipe = f"number_unit.{kind}"
    label = make_label(recipe, (a, a + len(repl)), f"The number {old_ascii} was changed to {new_ascii}.")
    return Injection(recipe, [label], text=new)


def _find_all(text: str, needle: str) -> list[tuple[int, int]]:
    nt = normalize_with_map(text)
    target = normalize(needle)
    spans = []
    if not target:
        return spans
    for pos in nt.find_all(target):
        sp = nt.to_orig(pos, pos + len(target))
        if sp is not None:
            spans.append(sp)
    return spans


def term_entity(item: CleanItem, pool: Pool, rng: random.Random) -> Injection | None:
    """Replace the translation of an entity by another entity's translation (real outputs).
    When the entity occurs more than once, replace only one occurrence (inconsistency)."""
    t = item.translation
    found = []
    for ent in english_entities(item.source):
        tr = pool.entity_translations.get((ent, item.lang))
        if tr and tr.strip():
            occ = _find_all(t, tr.strip())
            if occ:
                found.append((ent, occ))
    if not found:
        return None
    ent, occ = rng.choice(found)
    src_low = item.source.lower()
    repls = sorted(
        (e, tr.strip())
        for (e, lang), tr in pool.entity_translations.items()
        if lang == item.lang and e != ent and e.lower() not in src_low and tr.strip() and not _find_all(t, tr.strip())
    )
    if not repls:
        return None
    other, repl = rng.choice(repls)
    a, b = rng.choice(occ)
    new = t[:a] + repl + t[b:]
    recipe = "term_entity.inconsistent" if len(occ) > 1 else "term_entity.replace"
    note = f"The name '{ent}' was replaced by the name '{other}'" + (" in one of its occurrences." if len(occ) > 1 else ".")
    label = make_label(recipe, (a, a + len(repl)), note)
    return Injection(recipe, [label], text=new)


def format_break(item: CleanItem, pool: Pool, rng: random.Random) -> Injection | None:
    """Break Markdown/LaTeX structure: a `$`, a table pipe, a code fence or a link target."""
    t = item.translation
    regions = find_protected(t)
    edits: list[tuple[str, int, int, str]] = []  # (kind, start, end, replacement)
    for r in regions:
        if r.kind == "math_inline" and t[r.start] == "$" and t[r.end - 1] == "$":
            edits.append(("math_dollar", r.start, r.start + 1, ""))
            edits.append(("math_dollar", r.end - 1, r.end, ""))
        elif r.kind == "code_block":
            body = t[r.start : r.end].rstrip()
            close = r.start + len(body) - 3
            if t[close : close + 3] in ("```", "~~~"):
                edits.append(("code_fence", close, close + 1, ""))
        elif r.kind == "link_target":
            url = t[r.start : r.end]
            host_start = url.find("://") + 3 if "://" in url else 0
            host_end = url.find("/", host_start)
            host_end = len(url) if host_end == -1 else host_end
            letters = [i for i in range(host_start, host_end) if url[i].isalnum()]
            if len(letters) >= 3:
                i = letters[len(letters) // 2]
                edits.append(("link_target", r.start + i, r.start + i + 1, ""))
    pos = 0
    for line in t.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 3 and not _touches_regions((pos, pos + len(line)), regions):
            pipes = [pos + i for i, c in enumerate(line) if c == "|"]
            for p in pipes[1:-1]:
                edits.append(("table_pipe", p, p + 1, ""))
        pos += len(line) + 1
    if not edits:
        return None
    kind = rng.choice(sorted({e[0] for e in edits}))
    _, a, b, repl = rng.choice([e for e in edits if e[0] == kind])
    new = t[:a] + repl + t[b:]
    span = line_span(new, a if a < len(new) else max(0, len(new) - 1))
    recipe = f"format.{kind}"
    notes = {
        "math_dollar": "A '$' math delimiter was deleted on this line.",
        "table_pipe": "A table cell separator '|' was deleted on this line.",
        "code_fence": "The closing code fence was broken on this line.",
        "link_target": "A character was deleted from the link target on this line.",
    }
    label = make_label(recipe, span, notes[kind])
    return Injection(recipe, [label], text=new)


def wrong_language(item: CleanItem, pool: Pool, rng: random.Random) -> Injection | None:
    """Replace the output with the English source (untranslated) or with the same
    source's real translation into another language."""
    options = []
    if item.source.strip() and item.source != item.translation:
        options.append(("untranslated", item.source, "The translation was replaced by the untranslated English source."))
    for sib in pool.siblings(item):
        if sib.translation != item.translation:
            options.append(("other_language", sib.translation, f"The translation was replaced by the '{sib.lang}' translation of the same source."))
    if not options:
        return None
    kind = rng.choice(sorted({o[0] for o in options}))
    _, new, note = rng.choice([o for o in options if o[0] == kind])
    recipe = f"wrong_language_script.{kind}"
    label = make_label(recipe, (0, len(new)), note)
    return Injection(recipe, [label], text=new)


# -------------------- perturb
Translate = Callable[[str, str], Awaitable[str]]  # (english text, lang) -> translation


_PAIRS = [
    ("increase", "decrease"), ("increases", "decreases"), ("increased", "decreased"),
    ("rise", "fall"), ("rises", "falls"), ("north", "south"), ("east", "west"),
    ("before", "after"), ("above", "below"), ("more", "less"), ("higher", "lower"),
    ("largest", "smallest"), ("large", "small"), ("big", "small"), ("hot", "cold"), ("warm", "cool"),
    ("always", "never"), ("first", "last"), ("early", "late"), ("morning", "evening"),
    ("open", "closed"), ("inside", "outside"), ("wet", "dry"), ("heavy", "light"),
    ("old", "new"), ("young", "old"), ("cheap", "expensive"), ("easy", "difficult"),
    ("long", "short"), ("fast", "slow"), ("safe", "dangerous"), ("popular", "unpopular"),
    ("possible", "impossible"), ("common", "rare"), ("allowed", "forbidden"), ("success", "failure"),
]


ANTONYMS: dict[str, str] = {}


for _a, _b in _PAIRS:
    ANTONYMS.setdefault(_a, _b)
    ANTONYMS.setdefault(_b, _a)


_WORD = re.compile(r"\b(" + "|".join(sorted(map(re.escape, ANTONYMS), key=len, reverse=True)) + r")\b", re.IGNORECASE)


MAX_CHANGED_FRACTION = 0.5  # above this the model rephrased the sentence: no reliable span


MAX_SPAN_FRACTION = 0.5  # the changed span may cover at most half the output (otherwise it localizes nothing)


def _match_case(word: str, like: str) -> str:
    if like.isupper():
        return word.upper()
    if like[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def perturb_source(source: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Swap one antonym in the English source. Returns (perturbed, old word, new word)."""
    matches = list(_WORD.finditer(source))
    if not matches:
        return None
    m = rng.choice(matches)
    old = m.group()
    new = _match_case(ANTONYMS[old.lower()], old)
    return source[: m.start()] + new + source[m.end() :], old, new


def _tokens(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in re.finditer(r"\S+", text)]


def changed_span(old: str, new: str) -> tuple[tuple[int, int], float] | None:
    """Span in ``new`` covering every token that differs from ``old``, and the
    fraction of changed tokens. None when the texts are token-identical."""
    ot, nt = _tokens(old), _tokens(new)
    ow = [old[a:b] for a, b in ot]
    nw = [new[a:b] for a, b in nt]
    sm = difflib.SequenceMatcher(None, ow, nw, autojunk=False)
    starts, ends, changed = [], [], 0
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if j2 > j1:
            starts.append(nt[j1][0])
            ends.append(nt[j2 - 1][1])
            changed += j2 - j1
        else:  # pure deletion: zero-width point in the new text
            p = nt[j1][0] if j1 < len(nt) else len(new)
            starts.append(p)
            ends.append(p)
            changed += 1
    if not starts:
        return None
    return (min(starts), max(ends)), changed / max(1, len(nw))


async def mistranslation(item: CleanItem, translate: Translate, rng: random.Random) -> Injection | None:
    pert = perturb_source(item.source, rng)
    if pert is None:
        return None
    perturbed, old_word, new_word = pert
    new = await translate(perturbed, item.lang)
    if not new or not new.strip() or new == item.translation:
        return None
    diff = changed_span(item.translation, new)
    if diff is None:
        return None
    span, fraction = diff
    if fraction > MAX_CHANGED_FRACTION or (span[1] - span[0]) > MAX_SPAN_FRACTION * len(new):
        return None
    note = f"Meaning changed: the output says '{new_word}' where the source says '{old_word}'."
    label = make_label("mistranslation.antonym", span, note, perturbed_source=perturbed, changed_token_fraction=round(fraction, 3))
    return Injection("mistranslation.antonym", [label], text=new)


# -------------------- audio_edits
def speech_rms(samples: np.ndarray, sr: int) -> float:
    """RMS of the louder half of the frames (≈ the speech level)."""
    rms, _ = _frame_rms(samples, sr)
    if rms.size == 0:
        return 0.0
    loud = rms[rms >= np.median(rms)]
    return float(np.sqrt(np.mean(loud**2))) if loud.size else 0.0


def _noise(n: int, rng: random.Random) -> np.ndarray:
    return np.random.default_rng(rng.randrange(2**32)).standard_normal(n).astype(np.float32)


def truncate(samples: np.ndarray, sr: int, rng: random.Random) -> Injection | None:
    """Cut the tail (keep 45–80% of the clip). Label: zero-width point at the cut."""
    dur = len(samples) / sr
    if dur < 1.5:
        return None
    cut = snap(samples, sr, rng.uniform(0.45, 0.8) * dur)
    removed = (len(samples) - cut) / sr
    severity = "critical" if removed / dur >= 0.4 else "major"
    label = make_label(
        "speech_omission_truncation.tail_cut", (cut / sr, cut / sr), f"The audio was cut off here; the last {removed:.2f} s of speech are missing.",
        severity=severity, unit="seconds", fld="audio", removed_s=round(removed, 4), original_duration_s=round(dur, 4),
    )
    return Injection("speech_omission_truncation.tail_cut", [label], samples=samples[:cut].copy(), sr=sr)


def repeat(samples: np.ndarray, sr: int, rng: random.Random) -> Injection | None:
    """Repeat a 0.6–1.5 s mid segment right after itself. Label: the repeated copy."""
    dur = len(samples) / sr
    if dur < 2.0:
        return None
    length = min(rng.uniform(0.6, 1.5), 0.35 * dur)
    a = snap(samples, sr, rng.uniform(0.25, 0.55) * dur)
    b = snap(samples, sr, a / sr + length)
    if b - a < int(0.3 * sr) or b > len(samples):
        return None
    seg = samples[a:b]
    out = np.concatenate([samples[:b], seg, samples[b:]])
    label = make_label(
        "speech_repetition.loop",
        (b / sr, (b + len(seg)) / sr),
        f"A {len(seg) / sr:.2f} s stretch of speech is repeated here.",
        unit="seconds",
        fld="audio",
        repeats_from_s=round(a / sr, 4),
    )
    return Injection("speech_repetition.loop", [label], samples=out, sr=sr)


def insert_silence(samples: np.ndarray, sr: int, rng: random.Random) -> Injection | None:
    dur = len(samples) / sr
    if dur < 1.0:
        return None
    gap = rng.uniform(1.0, 2.5)
    p = snap(samples, sr, rng.uniform(0.3, 0.7) * dur)
    n = int(round(gap * sr))
    out = np.concatenate([samples[:p], np.zeros(n, dtype=samples.dtype), samples[p:]])
    severity = "minor" if gap < 1.5 else "major"
    label = make_label(
        "speech_garbled.silence", (p / sr, (p + n) / sr), f"A {n / sr:.2f} s silence was inserted mid-speech.", severity=severity, unit="seconds", fld="audio"
    )
    return Injection("speech_garbled.silence", [label], samples=out, sr=sr)


def noise_burst(samples: np.ndarray, sr: int, rng: random.Random) -> Injection | None:
    """Replace 0.3–0.8 s of speech with white noise at speech level."""
    dur = len(samples) / sr
    if dur < 1.5:
        return None
    length = rng.uniform(0.3, 0.8)
    a = int(rng.uniform(0.2, 0.7) * len(samples))
    b = min(len(samples), a + int(round(length * sr)))
    out = samples.copy()
    out[a:b] = np.clip(_noise(b - a, rng) * speech_rms(samples, sr), -1.0, 1.0)
    label = make_label("speech_garbled.noise_burst", (a / sr, b / sr), f"{(b - a) / sr:.2f} s of speech were replaced by noise.", unit="seconds", fld="audio")
    return Injection("speech_garbled.noise_burst", [label], samples=out, sr=sr)


def noise_bed(samples: np.ndarray, sr: int, rng: random.Random) -> Injection | None:
    """Mix white noise under the whole clip at 8–14 dB SNR."""
    if len(samples) < sr // 2:
        return None
    snr_db = rng.uniform(8.0, 14.0)
    level = speech_rms(samples, sr) / (10 ** (snr_db / 20))
    out = np.clip(samples + _noise(len(samples), rng) * level, -1.0, 1.0).astype(samples.dtype)
    label = make_label(
        "speech_garbled.noise_bed",
        (0.0, len(samples) / sr),
        f"Background noise was added to the whole clip (about {snr_db:.0f} dB below the speech).",
        unit="seconds",
        fld="audio",
        snr_db=round(snr_db, 2),
    )
    return Injection("speech_garbled.noise_bed", [label], samples=out, sr=sr)


GARBLED_VARIANTS = (insert_silence, noise_burst, noise_bed)


def garble(samples: np.ndarray, sr: int, rng: random.Random) -> Injection | None:
    """One of the garbling variants, chosen by the RNG."""
    return rng.choice(GARBLED_VARIANTS)(samples, sr, rng)


# -------------------- build
TEXT_CATEGORIES = ("omission", "addition", "mistranslation", "number_unit", "term_entity", "format", "wrong_language_script")


SPEECH_CATEGORIES = ("speech_omission_truncation", "speech_repetition", "speech_garbled")


_TEXT_RECIPES = {
    "omission": omission,
    "addition": addition,
    "number_unit": number_unit,
    "term_entity": term_entity,
    "format": format_break,
    "wrong_language_script": wrong_language,
}


_SPEECH_RECIPES = {
    "speech_omission_truncation": truncate,
    "speech_repetition": repeat,
    "speech_garbled": garble,
}


@dataclass
class BuiltItem:
    base: CleanItem
    translation: str
    labels: list[dict]  # [] = clean
    recipe: str | None = None
    samples: np.ndarray | None = None  # edited audio (None = use the clean clip as is)
    sr: int | None = None


async def _text_injection(category: str, item: CleanItem, pool: Pool, translate: Translate | None, seed: int) -> Injection | None:
    rng = rng_for(seed, item.id, category)
    if category == "mistranslation":
        return await mistranslation(item, translate, rng) if translate else None
    return _TEXT_RECIPES[category](item, pool, rng)


def _caps(n_items: int, categories: tuple[str, ...], clean_fraction: float, targets: dict[str, int] | None) -> tuple[dict[str, int], int]:
    """Per-category caps and the total number of items to inject for one modality.
    Explicit ``targets`` win; otherwise the injected share (1 - clean_fraction) is split evenly."""
    if targets is not None:
        caps = {c: max(0, int(targets.get(c, 0))) for c in categories}
        return caps, sum(caps.values())
    total = round(n_items * (1 - clean_fraction))
    per = -(-total // len(categories))  # ceil
    return {c: per for c in categories}, total


async def build_labelled_set(
    items: list[CleanItem], pool: Pool, *, seed: int, translate: Translate | None = None, clean_fraction: float = 0.5, targets: dict[str, int] | None = None
) -> list[BuiltItem]:
    """``targets`` maps category -> number of items to inject (one error per item).
    Text categories are filled scarcest-first, so a category with few eligible items
    (e.g. format needs Markdown) does not lose its candidates to common categories."""
    order = sorted(items, key=lambda x: x.id)
    random.Random(f"{seed}:order").shuffle(order)
    audio_items = [x for x in order if x.audio_path]
    text_items = [x for x in order if not x.audio_path]
    chosen: dict[str, Injection] = {}

    # speech: least-filled open category first; another category if the clip is not eligible
    caps, total = _caps(len(audio_items), SPEECH_CATEGORIES, clean_fraction, targets)
    counts = {c: 0 for c in SPEECH_CATEGORIES}
    for item in audio_items:
        open_cats = [c for c in SPEECH_CATEGORIES if counts[c] < caps[c]]
        if not open_cats or sum(counts.values()) >= total:
            break
        samples, sr = read_audio(Path(item.audio_path).read_bytes())
        for cat in sorted(open_cats, key=lambda c: (counts[c], SPEECH_CATEGORIES.index(c))):
            inj = _SPEECH_RECIPES[cat](samples, sr, rng_for(seed, item.id, cat))
            if inj is not None:
                chosen[item.id] = inj
                counts[cat] += 1
                break

    # text: find every item's eligible injections once, then fill scarcest categories first
    caps, total = _caps(len(text_items), TEXT_CATEGORIES, clean_fraction, targets)
    eligible: dict[str, list[tuple[CleanItem, Injection]]] = {c: [] for c in TEXT_CATEGORIES}
    for item in text_items:
        for cat in TEXT_CATEGORIES:
            if caps[cat] > 0:
                inj = await _text_injection(cat, item, pool, translate, seed)
                if inj is not None:
                    eligible[cat].append((item, inj))
    done = 0
    for cat in sorted(TEXT_CATEGORIES, key=lambda c: (len(eligible[c]), TEXT_CATEGORIES.index(c))):
        n = 0
        for item, inj in eligible[cat]:
            if n >= caps[cat] or done >= total:
                break
            if item.id not in chosen:
                chosen[item.id] = inj
                n += 1
                done += 1

    built: list[BuiltItem] = []
    for item in order:
        inj = chosen.get(item.id)
        if inj is None:
            built.append(BuiltItem(item, item.translation, []))
        elif inj.samples is not None:
            built.append(BuiltItem(item, item.translation, inj.labels, inj.recipe, inj.samples, inj.sr))
        else:
            built.append(BuiltItem(item, inj.text, inj.labels, inj.recipe))
    return built
