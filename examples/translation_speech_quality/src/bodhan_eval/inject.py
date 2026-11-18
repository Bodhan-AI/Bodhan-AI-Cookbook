"""Programmatic perturbations with machine-generated provenance labels."""

import argparse
import copy
import json
import re
from pathlib import Path


def inject(item, kind):
    altered = copy.deepcopy(item)
    text = altered["translation"]
    field = "translation"
    if kind == "number":
        match = re.search(r"\d", text)
        if not match:
            raise ValueError("Number injection requires a digit in the translation")
        start = match.start()
        replacement = str((int(match.group()) + 3) % 10)
        text = text[:start] + replacement + text[start + 1:]
        span = [start, start + 1]
    elif kind == "omission":
        # Delete a third of the target. Label the corresponding approximate
        # source clause separately; exact alignment requires human review.
        end = text.rfind(" ", 0, max(1, len(text) * 2 // 3))
        if end < 1:
            end = len(text) // 2
        text = text[:end]
        field = "source"
        start = len(item["source"]) * 2 // 3
        span = [start, len(item["source"])]
    elif kind == "addition":
        extra = " This statement is not supported by the source."
        span = [len(text), len(text) + len(extra)]
        text += extra
    elif kind == "wrong_language":
        text = item["source"]
        span = [0, len(text)]
    elif kind == "format":
        matches = list(re.finditer(r"(?<!\\)\$", text))
        if not matches:
            raise ValueError("Format injection requires a math delimiter")
        start = matches[-1].start()
        text = text[:start] + text[start + 1:]
        # Missing delimiter has a zero-width position in the output.
        span = [max(0, start - 1), min(len(text), start + 1)]
    elif kind == "entity":
        glossary = item.get("glossary", {})
        term = next(iter(glossary.values()))
        term = term if isinstance(term, str) else term[0]
        start = text.index(term)
        replacement = "DIFFERENT_ENTITY"
        text = text[:start] + replacement + text[start + len(term):]
        span = [start, start + len(replacement)]
    else:
        raise ValueError(f"Unsupported injection: {kind}")
    altered["translation"] = text
    altered["id"] = item["id"] + ":" + kind
    altered["group_id"] = item.get("group_id", item["id"])
    altered["labels"] = [{"type": kind, "severity": "major", "field": field, "span": span}]
    altered["annotation_source"] = "programmatic_injection"
    altered["injection"] = {"kind": kind, "base_id": item["id"],
                            "localization": "approximate_source_clause" if kind == "omission" else "known_mutation"}
    return altered


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--kind", choices=["number", "omission", "addition", "wrong_language", "format", "entity"], required=True)
    args = p.parse_args()
    rows = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    with args.out.open("w") as output:
        for row in rows:
            try:
                altered = inject(row, args.kind)
            except (ValueError, StopIteration):
                continue
            output.write(json.dumps(altered, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
