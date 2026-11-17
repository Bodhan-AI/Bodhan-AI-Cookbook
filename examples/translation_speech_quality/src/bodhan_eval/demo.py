"""Create labelled perturbations from real Bodhan outputs and publishable controls."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import shutil
import sys
import unicodedata
from array import array
from difflib import SequenceMatcher
from pathlib import Path

from dotenv import load_dotenv

from .__main__ import money, positive
from .audio_checks import SAMPLE_RATE, decode_audio, pcm_wav
from .calibration import review_id
from .providers import MICRO_INR, CallError, Providers, digest
from .report import write_jsonl
from .schema import CATEGORIES, read_jsonl

# Antonym pairs used to flip meaning in the ENGLISH source before Bodhan re-translates it.
# A general-purpose lexicon of opposites, applied in both directions.
ANTONYMS = (
    ("before", "after"), ("north", "south"), ("east", "west"), ("larger", "smaller"),
    ("increase", "decrease"), ("increases", "decreases"), ("parallel", "perpendicular"),
    ("open", "closed"), ("clockwise", "counterclockwise"), ("higher", "lower"),
    ("equal", "unequal"), ("always", "never"), ("more", "less"), ("most", "least"),
    ("maximum", "minimum"), ("left", "right"), ("above", "below"), ("inside", "outside"),
    ("first", "last"), ("hot", "cold"), ("warm", "cool"), ("wet", "dry"), ("add", "subtract"),
    ("multiply", "divide"), ("positive", "negative"), ("heavy", "light"), ("fast", "slow"),
    ("faster", "slower"), ("start", "stop"), ("starts", "stops"), ("early", "late"),
    ("earlier", "later"), ("full", "empty"), ("strong", "weak"), ("long", "short"),
    ("longer", "shorter"), ("top", "bottom"), ("same", "different"),
    ("can", "cannot"), ("safe", "unsafe"), ("visible", "invisible"),
)
MEANING_SWAPS = {**dict(ANTONYMS), **{right: left for left, right in ANTONYMS}}
# Neutral sentences with no numbers or names, so an addition seeds only one category.
ADDITIONS = (
    "Wear gloves while you do this.",
    "Ask a teacher to check the result.",
    "Write your name at the top of the page.",
    "Some students find this step difficult.",
    "Keep the room quiet during this activity.",
)
# Replacement names for a proper noun or acronym in the English source.
NAMES = ("Kenya", "Oslo", "Darwin", "Mercury", "Lima", "Curie")
# Sentence ends include the Devanagari/Bengali danda as well as Latin punctuation.
SENTENCE = re.compile(r"[^.!?।॥\n]+[.!?।॥]")
NUMBER = re.compile(r"(?<!\w)[+\-−]?\d+(?:[.,:/]\d+)*(?:\s?(?:°[CF]|[%₹]|km|cm|mm|kg|mg|mL|Hz|kWh|m/s|s|m|g|L)(?!\w))?", re.IGNORECASE)
FORMULA = re.compile(r"(?<!\\)(\$\$[\s\S]+?\$\$|\$(?!\$)[^$\n]+?\$)")
LITERAL_ENTITY = re.compile(r"https?://[^\s)>]+|(?<!\w)[A-Z][A-Z0-9]{1,}(?!\w)")
UNIT_SWAPS = {"cm": "mm", "mm": "cm", "km": "m", "kg": "g", "g": "kg", "°C": "°F", "mL": "L", "L": "mL", "%": "°C"}


def defect(category: str, field: str | None, start: int = 0, end: int = 0) -> dict:
    """Create an exact label for a controlled edit, without guessing unavailable offsets."""
    label = {"type": category, "severity": "major"}
    if field is not None:
        label.update(field=field, span=[start, end])
    return label


def pick(row: dict, category: str, count: int) -> int:
    """Choose a mutation style from the row identity, so reruns produce the same dataset."""
    return int(digest(f"{row.get('id', '')}:{category}".encode())[:8], 16) % count


def change_digit(candidate: str) -> tuple[str, dict, str] | None:
    """Replace one digit of the first quantity with a different digit in the same script."""
    match = NUMBER.search(candidate)
    if match is None:
        return None
    offset = next(i for i in range(match.start(), match.end()) if candidate[i].isdecimal())
    digit = unicodedata.decimal(candidate[offset])
    replacement = chr(ord(candidate[offset]) - digit + (digit + 1 if digit < 9 else 8))
    changed = candidate[:offset] + replacement + candidate[offset + 1 :]
    return changed, defect("number", "translation", match.start(), match.end()), "alter_original_digit"


def change_unit(candidate: str) -> tuple[str, dict, str] | None:
    """Swap a Latin unit symbol kept in the translation for a different unit."""
    match = re.search(r"(?<=\d)\s?(°C|cm|mm|km|kg|mL|g|L|%)(?![\w°])", candidate)
    if match is None:
        return None
    unit, start = match.group(1), match.start(1)
    changed = candidate[:start] + UNIT_SWAPS[unit] + candidate[match.end(1) :]
    return changed, defect("number", "translation", start, start + len(UNIT_SWAPS[unit])), "swap_unit_symbol"


def format_edits(candidate: str) -> list[tuple[str, dict, str]]:
    """Return every structural Markdown or LaTeX break that applies to this candidate."""
    edits = []
    math = FORMULA.search(candidate)
    if math is not None:
        start, end = math.span()
        edits.append((candidate[:start] + candidate[start + 1 :], defect("format", "translation", start, end - 1), "break_formula_delimiter"))
    separator = re.search(r"(?m)^[ \t]*\|[ \t:|-]*-{2,}[ \t:|-]*$\n?", candidate)
    if separator is not None:
        changed = candidate[: separator.start()] + candidate[separator.end() :]
        start = min(separator.start(), max(len(changed) - 1, 0))
        edits.append((changed, defect("format", "translation", start, start + 1), "remove_table_separator"))
    rows = [m for m in re.finditer(r"(?m)^[ \t]*\|.+\|[ \t]*$", candidate) if not re.fullmatch(r"[\s|:-]+", m.group())]
    if len(rows) >= 2:
        row = rows[-1]
        cell = re.search(r"\|[^|\n]+(?=\|)", row.group())
        if cell is not None:
            start = row.start() + cell.start()
            changed = candidate[:start] + candidate[start + len(cell.group()) :]
            edits.append((changed, defect("format", "translation", start, start + 1), "drop_table_cell"))
    bold = re.search(r"\*\*[^*\n]+\*\*", candidate)
    if bold is not None:
        end = bold.end() - 2
        edits.append((candidate[:end] + candidate[bold.end() :], defect("format", "translation", bold.start(), end), "drop_bold_closer"))
    heading = re.search(r"(?m)^#{1,6}[ \t]+", candidate)
    if heading is not None:
        line_end = candidate.find("\n", heading.end())
        line_end = len(candidate) if line_end < 0 else line_end
        changed = candidate[: heading.start()] + candidate[heading.end() :]
        edits.append((changed, defect("format", "translation", heading.start(), line_end - (heading.end() - heading.start())), "remove_heading_marker"))
    link = re.search(r"\]\((https?://[^)\s]+)\)", candidate)
    if link is not None:
        changed = candidate[: link.start() + 1] + " " + candidate[link.start() + 1 :]
        edits.append((changed, defect("format", "translation", link.start(), link.end() + 1), "break_link_syntax"))
    return edits


def text_variant(row: dict, category: str, choice: int | None = None) -> tuple[str, dict, str] | None:
    """Return a controlled direct text edit with its exact labelled span."""
    source, candidate = row["source"], row["translation"]
    if category == "wrong_language" and row["lang"] != "en" and candidate != source:
        sentences = list(SENTENCE.finditer(candidate))
        source_sentences = list(SENTENCE.finditer(source))
        style = pick(row, category, 2) if choice is None else choice
        if style == 1 and len(sentences) >= 2 and len(source_sentences) == len(sentences):
            # Leave one sentence untranslated: the English sentence in the same position.
            index = pick(row, "sentence", len(sentences))
            target, english = sentences[index], source_sentences[index].group().strip()
            start = target.start() + len(target.group()) - len(target.group().lstrip())
            changed = candidate[:start] + english + candidate[target.end() :]
            return changed, defect(category, "translation", start, start + len(english)), "leave_sentence_untranslated"
        return source, defect(category, "translation", 0, len(source)), "substitute_original_english"
    if category == "number":
        options = [edit for edit in (change_digit(candidate), change_unit(candidate)) if edit]
        if options:
            return options[(pick(row, category, len(options)) if choice is None else choice) % len(options)]
    if category == "entity":
        match = LITERAL_ENTITY.search(candidate)
        if match is not None and match.group() in source:
            value = match.group()
            replacement = value + ("/archive" if value.startswith("http") else "X")
            changed = candidate[: match.start()] + replacement + candidate[match.end() :]
            return changed, defect(category, "translation", match.start(), match.start() + len(replacement)), "alter_literal_entity"
    if category == "format":
        options = format_edits(candidate)
        if options:
            return options[(pick(row, category, len(options)) if choice is None else choice) % len(options)]
    return None


def changed_span(before: str, after: str) -> tuple[int, int] | None:
    """Locate the largest changed output region when the source has a controlled edit."""
    changes = [(start, end) for kind, _, _, start, end in SequenceMatcher(None, before, after).get_opcodes() if kind in ("insert", "replace") and end > start]
    return max(changes, key=lambda span: span[1] - span[0]) if changes else None


def perturbed_source(row: dict, category: str) -> tuple[str, tuple[int, int] | None] | None:
    """Edit the English source. For an omission, also return the omitted source span."""
    source = row["source"]
    if category == "omission":
        sentences = list(SENTENCE.finditer(source))
        if len(sentences) < 2:
            return None
        dropped = sentences[pick(row, category, len(sentences))]
        start = dropped.start() + len(dropped.group()) - len(dropped.group().lstrip())
        return (source[: dropped.start()] + source[dropped.end() :]).strip(), (start, dropped.end())
    if category == "addition":
        return source + " " + ADDITIONS[pick(row, category, len(ADDITIONS))], None
    if category == "entity":
        acronyms = list(re.finditer(r"\b[A-Z]{2,}\b", source))
        names = [m for m in re.finditer(r"\b[A-Z][a-z]{2,}\b", source) if m.start() > 0 and not re.search(r"(^|[.!?:#\n]\s*)$", source[: m.start()])]
        targets = acronyms or names
        if not targets:
            return None
        target = targets[0]
        replacement = next(name for name in NAMES[pick(row, category, len(NAMES)) :] + NAMES if name != target.group())
        return source[: target.start()] + replacement + source[target.end() :], None
    cues = [match for match in re.finditer(r"\b[a-z]+\b", source) if match.group() in MEANING_SWAPS]
    if not cues:
        return None
    cue = cues[pick(row, category, len(cues))]
    return source[: cue.start()] + MEANING_SWAPS[cue.group()] + source[cue.end() :], None


def splice_variant(row: dict, category: str, donors: list[dict]) -> tuple[str, dict, str] | None:
    """Delete or insert whole Bodhan-translated sentences. No target-language text is written."""
    candidate = row["translation"]
    sentences = list(SENTENCE.finditer(candidate))
    source_sentences = list(SENTENCE.finditer(row["source"]))
    if category == "omission" and len(sentences) >= 2 and len(sentences) == len(source_sentences):
        index = pick(row, category, len(sentences))
        dropped, aligned = sentences[index], source_sentences[index]
        start = aligned.start() + len(aligned.group()) - len(aligned.group().lstrip())
        changed = (candidate[: dropped.start()] + candidate[dropped.end() :]).strip()
        return changed, defect(category, "source", start, aligned.end()), "delete_translated_sentence"
    if category == "addition":
        pool = [
            match.group().strip()
            for donor in donors
            if donor["id"] != row["id"] and donor["lang"] == row["lang"]
            for match in SENTENCE.finditer(donor["translation"])
            if len(match.group().strip()) > 12
        ]
        if not pool:
            return None
        sentence = pool[pick(row, category, len(pool))]
        start = len(candidate) + 1
        return candidate + " " + sentence, defect(category, "translation", start, start + len(sentence)), "insert_sentence_from_other_item"
    return None


async def semantic_variant(row: dict, category: str, providers: Providers) -> tuple[str, dict, str] | None:
    """Alter English meaning and use Bodhan for every resulting target-language string."""
    perturbed = perturbed_source(row, category)
    if perturbed is None:
        return None
    source_for_translation, omitted = perturbed
    call = await providers.translate(source_for_translation, row["lang"])
    changed = call.value.decode()
    if changed == row["translation"]:
        return None
    if category == "omission":
        label = defect(category, "source", *omitted)
    else:
        output_span = changed_span(row["translation"], changed)
        label = defect(category, "translation", *output_span) if output_span else defect(category, None)
    return changed, label, "translate_perturbed_english"


def milliseconds(samples: int) -> int:
    """Convert a sample count to whole milliseconds."""
    return samples * 1000 // SAMPLE_RATE


def audio_variant(input_audio: Path, category: str, choice: int | None = None) -> tuple[bytes, dict, str] | None:
    """Create a real truncated, repeated, silent or noisy signal from a recording."""
    samples = decode_audio(input_audio).samples
    if len(samples) < SAMPLE_RATE:
        return None
    style = int(digest(f"{input_audio.name}:{category}".encode())[:8], 16) % 2 if choice is None else choice
    if category == "speech_omission":
        if style == 0:
            # The omitted interval is on the original recording, outside the truncated file.
            return pcm_wav(samples[: len(samples) // 2]), defect(category, None), "remove_audio_tail"
        cut_start, cut_end = len(samples) * 2 // 5, len(samples) * 3 // 4
        return pcm_wav(samples[:cut_start] + samples[cut_end:]), defect(category, None), "remove_middle_audio"
    if category == "speech_repetition":
        if style == 0:
            start = (len(samples) * 1000 + SAMPLE_RATE - 1) // SAMPLE_RATE
            return pcm_wav(samples + samples), defect(category, "audio_ms", start, 2 * start), "repeat_audio_once"
        piece_start, piece_end = len(samples) // 4, len(samples) * 3 // 4
        looped = samples[:piece_end] + samples[piece_start:piece_end] + samples[piece_end:]
        span = (milliseconds(piece_end), milliseconds(2 * piece_end - piece_start))
        return pcm_wav(looped), defect(category, "audio_ms", *span), "repeat_middle_phrase"
    midpoint = len(samples) // 2
    start = milliseconds(midpoint)
    if style == 0:
        silence = array("h", [0]) * (3 * SAMPLE_RATE)
        return pcm_wav(samples[:midpoint] + silence + samples[midpoint:]), defect(category, "audio_ms", start, start + 3000), "insert_three_seconds_of_silence"
    # Broadband noise at the clip's own loudness, the way a corrupted stretch sounds.
    level = max(1000, int(2 * (sum(value * value for value in samples) / len(samples)) ** 0.5))
    generator = random.Random(digest(input_audio.name.encode()))
    noise = array("h", (max(-32768, min(32767, int(generator.gauss(0, level)))) for _ in range(2 * SAMPLE_RATE)))
    return pcm_wav(samples[:midpoint] + noise + samples[midpoint:]), defect(category, "audio_ms", start, start + 2000), "insert_two_seconds_of_noise"


def controls_from_report(report: Path, output: Path) -> list[dict]:
    """Select generated translations and copy matching real audio by digest."""
    controls = []
    for _, row, error in read_jsonl(report / "items.jsonl"):
        if error or not row.get("translation") or row.get("checks", {}).get("translation_input", {}).get("origin") != "generated":
            continue
        control = {name: row[name] for name in ("id", "lang", "source", "translation")}
        if row.get("audio") and row.get("audio_sha256") and row["checks"].get("audio_input", {}).get("origin") == "generated":
            media = report / row["audio"]
            target = output / "audio" / media.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(media, target)
            control["audio"] = target.relative_to(output).as_posix()
        controls.append(control)
    return controls


def review_rows(rows: list[dict]) -> list[dict]:
    """Export source context and candidate media without revealing the seed or row order."""
    candidates = [
        {
            "review_id": review_id(row["lang"], row["id"]),
            **{key: row[key] for key in ("lang", "source", "translation", "audio") if key in row},
        }
        for row in rows
    ]
    return sorted(candidates, key=lambda row: row["review_id"])


async def inject(args: argparse.Namespace) -> None:
    """Produce a demo dataset with bounded per-category examples and a review manifest."""
    args.out.mkdir(parents=True, exist_ok=True)
    controls = controls_from_report(args.report, args.out)
    if not controls:
        raise ValueError("No generated translations found. Run bodhan_eval with --generate first.")
    variants = []
    comparison = []
    language_counts = {}
    async with Providers(args.cache, args.max_cost_inr, 1) as providers:
        for lang in sorted({row["lang"] for row in controls}):
            available = [row for row in controls if row["lang"] == lang]
            for category in CATEGORIES:
                count = 0
                # Stable ordering distributes variants across different control sources.
                order = sorted(available, key=lambda row: digest(f"{category}:{row['id']}".encode()))
                for row in order:
                    replacement = audio_path = None
                    if category in ("omission", "addition", "mistranslation"):
                        try:
                            perturbation = await semantic_variant(row, category, providers)
                        except CallError:
                            # Without Indic-Translate access, fall back to splicing whole sentences.
                            perturbation = None
                        if perturbation is None:
                            perturbation = splice_variant(row, category, controls)
                    elif category in ("speech_omission", "speech_repetition", "garbled_audio"):
                        perturbation = None
                        if row.get("audio"):
                            audio = audio_variant(args.out / row["audio"], category)
                            if audio:
                                data, label, method = audio
                                audio_path = f"audio/{digest(data)}.wav"
                                (args.out / audio_path).write_bytes(data)
                                perturbation = (None, label, method)
                    else:
                        perturbation = text_variant(row, category)
                        if perturbation is None and category == "entity":
                            # No literal term to alter: rename an entity in English and re-translate.
                            try:
                                perturbation = await semantic_variant(row, category, providers)
                            except CallError:
                                perturbation = None
                    if perturbation is None:
                        continue
                    replacement, label, method = perturbation
                    item = {
                        **row,
                        "id": f"{row['id']}-{category}",
                        "label_origin": "injected",
                        "labels": [label],
                        "mutation": {"method": method, "control_id": row["id"]},
                    }
                    if replacement is not None:
                        item["translation"] = replacement
                        item.pop("audio", None)  # Speech belonged to the original translation.
                    elif audio_path:
                        item["audio"] = audio_path
                    variants.append(item)
                    original = {**row, "id": item["id"]}
                    if "audio" not in item:
                        original.pop("audio", None)
                    comparison.append(original)
                    count += 1
                    if count >= args.per_category_language:
                        break
                language_counts[f"{lang}:{category}"] = count
    write_jsonl(args.out / "demo.jsonl", [*controls, *variants])
    write_jsonl(args.out / "comparison_controls.jsonl", [*controls, *comparison])
    write_jsonl(args.out / "review_candidates.jsonl", review_rows([*controls, *variants]))
    print(json.dumps({"controls": len(controls), "injected": len(variants), "injected_by_language_and_category": language_counts}))


def main() -> int:
    """Run the injector over an existing generated evaluation without fabricated outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="prior output directory from --generate")
    parser.add_argument("--out", type=Path, required=True, help="directory for demo JSONL, assets, and review candidates")
    parser.add_argument("--cache", type=Path, default=Path(".bodhan-cache/responses.sqlite3"))
    parser.add_argument("--max-cost-inr", type=money, default=10 * MICRO_INR)
    parser.add_argument("--per-category-language", type=positive, default=2)
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    try:
        asyncio.run(inject(args))
    except (CallError, OSError, ValueError) as error:
        print(f"bodhan_eval.demo: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
