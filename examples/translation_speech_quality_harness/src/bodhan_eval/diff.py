import html
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BOOTSTRAP_SAMPLES = 2000


def _markdown_text(value: Any) -> str:
    escaped = html.escape(str(value), quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>])", r"\\\1", escaped)


@dataclass(frozen=True)
class RunDiff:
    regression: bool
    added_items: int
    removed_items: int
    changed_translations: int


def _load_items(directory: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = directory / "items.jsonl"
    records: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            key = (str(record["id"]), str(record["lang"]))
            if key in records:
                raise ValueError(f"duplicate item key {key!r} in {path}:{line_number}")
            if not isinstance(record.get("flags", []), list):
                raise ValueError(f"flags must be a list in {path}:{line_number}")
            records[key] = record
    return records


def _flag_identity(flag: dict[str, Any]) -> tuple[str, str]:
    span = flag.get("span")
    return str(flag.get("category", "unknown")), json.dumps(span, sort_keys=True)


def _translation(record: dict[str, Any]) -> str | None:
    digest = record.get("translation_hash")
    if digest is not None:
        return str(digest)
    text = record.get("translation")
    return str(text) if text is not None else None


def _bootstrap_ci(values: list[float], seed: int) -> tuple[float, float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    size = len(values)
    means = sorted(
        sum(values[rng.randrange(size)] for _ in range(size)) / size
        for _ in range(_BOOTSTRAP_SAMPLES)
    )

    def percentile(fraction: float) -> float:
        position = (len(means) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(means) - 1)
        weight = position - lower
        return means[lower] * (1 - weight) + means[upper] * weight

    return percentile(0.025), percentile(0.975)


def _format_ci(ci: tuple[float, float] | None) -> str:
    if ci is None:
        return "n/a"
    return f"({ci[0]:+.4f}, {ci[1]:+.4f})"


def compare_runs(
    baseline_dir: Path,
    current_dir: Path,
    seed: int = 20261119,
) -> RunDiff:
    baseline = _load_items(baseline_dir)
    current = _load_items(current_dir)
    matched = sorted(set(baseline) & set(current))
    deltas = [
        float(bool(current[key].get("flags"))) - float(bool(baseline[key].get("flags")))
        for key in matched
    ]
    ci = _bootstrap_ci(deltas, seed)
    return RunDiff(
        regression=bool(ci and ci[0] > 0),
        added_items=len(set(current) - set(baseline)),
        removed_items=len(set(baseline) - set(current)),
        changed_translations=sum(
            _translation(baseline[key]) is not None
            and _translation(current[key]) is not None
            and _translation(baseline[key]) != _translation(current[key])
            for key in matched
        ),
    )


def diff_markdown(
    baseline_dir: Path,
    current_dir: Path,
    seed: int = 20261119,
) -> str:
    baseline = _load_items(baseline_dir)
    current = _load_items(current_dir)
    matched = sorted(set(baseline) & set(current))
    added = sorted(set(current) - set(baseline))
    removed = sorted(set(baseline) - set(current))
    lines = ["# Baseline Diff", "", "## Item changes", ""]
    for key in added:
        lines.append(f"- New item: {_markdown_text(key[0])} ({_markdown_text(key[1])})")
    for key in removed:
        lines.append(f"- Removed item: {_markdown_text(key[0])} ({_markdown_text(key[1])})")

    changed_translations = [
        key
        for key in matched
        if _translation(baseline[key]) is not None
        and _translation(current[key]) is not None
        and _translation(baseline[key]) != _translation(current[key])
    ]
    for key in changed_translations[:5]:
        lines.append(
            f"- Translation changed: {_markdown_text(key[0])} "
            f"({_markdown_text(key[1])})"
        )
    if not added and not removed and not changed_translations:
        lines.append("- No item additions, removals, or translation changes.")

    categories = sorted({
        str(flag.get("category", "unknown"))
        for key in matched
        for record in (baseline[key], current[key])
        for flag in record.get("flags", [])
    })
    per_category: dict[str, list[float]] = defaultdict(list)
    per_language: dict[str, list[float]] = defaultdict(list)
    examples: dict[str, list[str]] = defaultdict(list)
    any_flag_deltas: list[float] = []
    for key in matched:
        old_flags = baseline[key].get("flags", [])
        new_flags = current[key].get("flags", [])
        old_identity = Counter(_flag_identity(flag) for flag in old_flags)
        new_identity = Counter(_flag_identity(flag) for flag in new_flags)
        for category in categories:
            old_present = any(flag.get("category") == category for flag in old_flags)
            new_present = any(flag.get("category") == category for flag in new_flags)
            per_category[category].append(float(new_present) - float(old_present))
        old_present = bool(old_flags)
        new_present = bool(new_flags)
        item_delta = float(new_present) - float(old_present)
        any_flag_deltas.append(item_delta)
        per_language[key[1]].append(item_delta)

        old_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        new_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for flag in old_flags:
            old_by_identity[_flag_identity(flag)].append(flag)
        for flag in new_flags:
            new_by_identity[_flag_identity(flag)].append(flag)
        for identity in sorted(set(old_identity) | set(new_identity)):
            category, _ = identity
            old_count = old_identity[identity]
            new_count = new_identity[identity]
            if new_count > old_count:
                examples[category].append(
                    f"New flag: {_markdown_text(category)} on "
                    f"{_markdown_text(key[0])} ({_markdown_text(key[1])})"
                )
            elif old_count > new_count:
                examples[category].append(
                    f"Resolved flag: {_markdown_text(category)} on "
                    f"{_markdown_text(key[0])} ({_markdown_text(key[1])})"
                )
            for old_flag, new_flag in zip(
                old_by_identity[identity], new_by_identity[identity], strict=False
            ):
                if old_flag.get("severity") != new_flag.get("severity"):
                    examples[category].append(
                        f"Severity changed: {_markdown_text(category)} "
                        f"{old_flag.get('severity', 'unknown')} -> "
                        f"{new_flag.get('severity', 'unknown')} on "
                        f"{_markdown_text(key[0])} ({_markdown_text(key[1])})"
                    )

    lines.extend(["", "## Paired flag-rate deltas", ""])
    overall_ci = _bootstrap_ci(any_flag_deltas, seed)
    overall_delta = sum(any_flag_deltas) / len(any_flag_deltas) if any_flag_deltas else 0.0
    regression = bool(overall_ci and overall_ci[0] > 0)
    lines.append(
        f"- Overall: {overall_delta:+.4f}; paired bootstrap CI95 "
        f"{_format_ci(overall_ci)} (n={len(any_flag_deltas)})"
    )
    lines.append(
        "- Regression: "
        + ("yes (flag-rate increase beyond the 95% bootstrap interval)" if regression else "no")
    )
    lines.extend(["", "### By category", ""])
    for index, category in enumerate(categories):
        values = per_category[category]
        delta = sum(values) / len(values) if values else 0.0
        lines.append(
            f"- {category}: {delta:+.4f}; paired bootstrap CI95 "
            f"{_format_ci(_bootstrap_ci(values, seed + index + 1))} (n={len(values)})"
        )
    lines.extend(["", "### By language", ""])
    all_languages = sorted(
        {key[1] for key in baseline} | {key[1] for key in current}
    )
    for index, lang in enumerate(all_languages):
        values = per_language.get(lang, [])
        delta = sum(values) / len(values) if values else 0.0
        added_for_lang = sum(key[1] == lang for key in added)
        removed_for_lang = sum(key[1] == lang for key in removed)
        lines.append(
            f"- Language: {lang}: {delta:+.4f}; paired bootstrap CI95 "
            f"{_format_ci(_bootstrap_ci(values, seed + index + len(categories) + 1))} "
            f"(n={len(values)}, added={added_for_lang}, removed={removed_for_lang})"
        )

    lines.extend(["", "## Examples", ""])
    wrote_example = False
    for category in categories:
        for example in examples[category][:5]:
            lines.append(f"- Example: {category} — {example}")
            wrote_example = True
    if not wrote_example:
        lines.append("- No flag changes.")
    if not added and not removed and not changed_translations and not wrote_example:
        lines.extend(["", "- No regressions or changes detected."])
    return "\n".join(lines) + "\n"
