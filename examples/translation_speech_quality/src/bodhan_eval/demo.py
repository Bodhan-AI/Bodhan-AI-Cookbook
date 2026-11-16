"""Create labelled perturbations from real Bodhan outputs and publishable controls."""

from __future__ import annotations

import argparse
import asyncio
import json
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
from .providers import MICRO_INR, CallError, Providers, digest
from .report import write_jsonl
from .schema import CATEGORIES, read_jsonl
from .text_checks import ENTITY, FORMULA, NUMBER, markdown_shape

MEANING_SWAPS = {
    "before": "after",
    "north": "south",
    "larger": "smaller",
    "increase": "decrease",
    "increases": "decreases",
    "parallel": "perpendicular",
    "open": "closed",
    "clockwise": "counterclockwise",
    "higher": "lower",
    "equal": "unequal",
}
FIRST_SENTENCE = re.compile(r"^[\s\S]+?[.!?](?=\s+[A-Z])")


def defect(category: str, field: str | None, start: int = 0, end: int = 0) -> dict:
    """Create an exact label for a controlled edit, without guessing unavailable offsets."""
    label = {"type": category, "severity": "major"}
    if field is not None:
        label.update(field=field, span=[start, end])
    return label


def text_variant(row: dict, category: str) -> tuple[str, dict, str] | None:
    """Return a controlled direct text edit with its exact labelled span."""
    source, candidate = row["source"], row["translation"]
    if category == "wrong_language" and row["lang"] != "en" and candidate != source:
        return source, defect(category, "translation", 0, len(source)), "substitute_original_english"
    if category == "number":
        match = NUMBER.search(candidate)
        if match is not None:
            offset = next(i for i in range(match.start(), match.end()) if candidate[i].isdecimal())
            digit = unicodedata.decimal(candidate[offset])
            replacement = chr(ord(candidate[offset]) - digit + (digit + 1 if digit < 9 else 8))
            changed = candidate[:offset] + replacement + candidate[offset + 1 :]
            return changed, defect(category, "translation", match.start(), match.end()), "alter_original_digit"
    if category == "entity":
        match = ENTITY.search(candidate)
        if match is not None and match.group() in source:
            value = match.group()
            replacement = value + ("/archive" if value.startswith("http") else "X")
            changed = candidate[: match.start()] + replacement + candidate[match.end() :]
            return changed, defect(category, "translation", match.start(), match.start() + len(replacement)), "alter_literal_entity"
    if category == "format":
        math = FORMULA.search(candidate)
        if math is not None:
            start, end = math.span()
            changed = candidate[:start] + candidate[start + 1 :]
            if len(FORMULA.findall(changed)) < len(FORMULA.findall(candidate)):
                return changed, defect(category, "translation", start, end - 1), "break_formula_delimiter"
        for match in re.finditer(r"(?m)^[^\n]*\|[ \t:|-]*-{2,}[ \t:|-]*[^\S\n]*(?:\n|$)", candidate):
            changed = candidate[: match.start()] + candidate[match.end() :]
            if len(markdown_shape(changed)) < len(markdown_shape(candidate)):
                start = min(match.start(), len(changed) - 1)
                return changed, defect(category, "translation", start, min(start + 1, len(changed))), "remove_table_separator"
    return None


def changed_span(before: str, after: str) -> tuple[int, int] | None:
    """Locate the largest changed output region when the source has a controlled edit."""
    changes = [(start, end) for kind, _, _, start, end in SequenceMatcher(None, before, after).get_opcodes() if kind in ("insert", "replace") and end > start]
    return max(changes, key=lambda span: span[1] - span[0]) if changes else None


async def semantic_variant(row: dict, category: str, providers: Providers) -> tuple[str, dict, str] | None:
    """Alter English meaning and use Bodhan for every resulting target-language string."""
    source, candidate = row["source"], row["translation"]
    span = None
    if category == "omission":
        sentences = FIRST_SENTENCE.match(source)
        if sentences is None:
            return None
        source_for_translation = source[sentences.end() :].strip()
        span = sentences.span()
    elif category == "addition":
        source_for_translation = source + " The box also contains a silver key."
    else:
        cue = next((match for match in re.finditer(r"\b[a-z]+\b", source) if match.group() in MEANING_SWAPS), None)
        if cue is None:
            return None
        source_for_translation = source[: cue.start()] + MEANING_SWAPS[cue.group()] + source[cue.end() :]
    call = await providers.translate(source_for_translation, row["lang"])
    changed = call.value.decode()
    if changed == candidate:
        return None
    if category == "omission":
        label = defect(category, "source", *span)
    else:
        output_span = changed_span(candidate, changed)
        label = defect(category, "translation", *output_span) if output_span else defect(category, None)
    return changed, label, "translate_perturbed_english"


def audio_variant(input_audio: Path, category: str) -> tuple[bytes, dict, str] | None:
    """Create a real clipped, repeated, or silent signal from a provided recording."""
    samples = decode_audio(input_audio).samples
    if len(samples) < SAMPLE_RATE:
        return None
    if category == "speech_omission":
        # The omitted interval is on the original recording, outside the truncated file.
        shortened = samples[: len(samples) // 2]
        return pcm_wav(shortened), defect(category, None), "remove_audio_tail"
    if category == "speech_repetition":
        repeated = samples + samples
        start = (len(samples) * 1000 + SAMPLE_RATE - 1) // SAMPLE_RATE
        return pcm_wav(repeated), defect(category, "audio_ms", start, 2 * start), "repeat_audio_once"
    midpoint = len(samples) // 2
    silence = array("h", [0]) * (3 * SAMPLE_RATE)
    perturbed = samples[:midpoint] + silence + samples[midpoint:]
    start = midpoint * 1000 // SAMPLE_RATE
    return pcm_wav(perturbed), defect(category, "audio_ms", start, start + 3000), "insert_three_seconds_of_silence"


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
                        perturbation = await semantic_variant(row, category, providers)
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
    write_jsonl(
        args.out / "review_candidates.jsonl",
        [{key: row[key] for key in ("id", "lang", "source", "translation", "audio") if key in row} for row in [*controls, *variants]],
    )
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
