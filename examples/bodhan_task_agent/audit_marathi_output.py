r"""Offline structure/number audit for Bodhan's existing Marathi worksheet output.

Does not call models, does not change translations, and does not load .env.
Run from examples/bodhan_task_agent:
  .\.venv\Scripts\python.exe audit_marathi_output.py
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys
import unicodedata

NUMERIC = re.compile(r"(?<!\d)\d+(?:[.,:/-]\d+)*(?!\d)", re.UNICODE)
MATH = re.compile(r"\$\$.*?\$\$|\$[^$\n]+\$|\\\(.*?\\\)|\\\[.*?\\\]", re.DOTALL)


def numeric_tokens(text: str) -> list[dict]:
    tokens = []
    for match in NUMERIC.finditer(text):
        original = match.group()
        canonical = "".join(str(unicodedata.digit(char)) if char.isdecimal() else char for char in original)
        line = text.count("\n", 0, match.start()) + 1
        tokens.append({"line": line, "raw": original, "canonical": canonical})
    return tokens


def markdown_table(text: str) -> list[dict]:
    rows = []
    for line_no, line in enumerate(text.splitlines(), 1):
        trimmed = line.strip()
        if not (trimmed.startswith("|") and trimmed.endswith("|")):
            continue
        cells = [c.strip() for c in trimmed[1:-1].split("|")]
        separator = bool(re.fullmatch(r"[\s|:-]+", trimmed))
        rows.append({"line": line_no, "columns": len(cells), "separator": separator,
                     "cells": cells})
    return rows


def diff_tokens(source: list[dict], target: list[dict]) -> list[dict]:
    a = [t["canonical"] for t in source]
    b = [t["canonical"] for t in target]
    result = []
    for change, a0, a1, b0, b1 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if change != "equal":
            result.append({"kind": change, "source": source[a0:a1], "target": target[b0:b1]})
    return result


def audit(source: str, target: str) -> dict:
    src_nums, tgt_nums = numeric_tokens(source), numeric_tokens(target)
    src_rows, tgt_rows = markdown_table(source), markdown_table(target)
    src_math, tgt_math = MATH.findall(source), MATH.findall(target)
    rows_preserved = ([(r["columns"], r["separator"]) for r in src_rows]
                      == [(r["columns"], r["separator"]) for r in tgt_rows])
    table_numeric_issues = []
    if rows_preserved:
        for a, b in zip(src_rows, tgt_rows):
            if a["separator"]:
                continue
            for col_idx, (a_cell, b_cell) in enumerate(zip(a["cells"], b["cells"]), 1):
                a_digits = [i["canonical"] for i in numeric_tokens(a_cell)]
                b_digits = [i["canonical"] for i in numeric_tokens(b_cell)]
                if a_digits != b_digits:
                    table_numeric_issues.append({"source_line": a["line"],
                                                 "target_line": b["line"],
                                                 "column": col_idx,
                                                 "source_numbers": a_digits,
                                                 "target_numbers": b_digits})
    return {
        "checks": {
            "numbers_preserved_in_order": not diff_tokens(src_nums, tgt_nums),
            "table_shape_preserved": rows_preserved,
            "table_numeric_cells_preserved": rows_preserved and not table_numeric_issues,
            "math_preserved_exactly": src_math == tgt_math,
        },
        "source_numeric_tokens": src_nums,
        "translated_numeric_tokens": tgt_nums,
        "numeric_mismatches": diff_tokens(src_nums, tgt_nums),
        "table_numeric_issues": table_numeric_issues,
        "math_fragments_source": src_math,
        "math_fragments_target": tgt_math,
        "notes": [
            "This is an offline structural check, not a Marathi translation quality score.",
            "Digit glyphs are normalized for comparison (९ and 9 compare equal).",
            "The existing files are not modified. No API requests are made.",
            "If numeric mismatches remain, review the matching Marathi sentence before editing; do not invent target-language content.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="artifacts/worksheet_demo.ocr.md")
    parser.add_argument("--target", default="artifacts/worksheet_demo.translated.mr.md")
    parser.add_argument("--report", default="artifacts/worksheet_demo.numeric_audit.json")
    args = parser.parse_args(argv)
    src, tgt = Path(args.source), Path(args.target)
    if not src.is_file() or not tgt.is_file():
        print("MISSING_FILE: source or translated worksheet not found", file=sys.stderr)
        return 2
    result = audit(src.read_text(encoding="utf-8"), tgt.read_text(encoding="utf-8"))
    dest = Path(args.report)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, passed in result["checks"].items():
        print(f"{'PASS' if passed else 'REVIEW'} {name}")
    for item in result["numeric_mismatches"]:
        print(f"MISMATCH {item['kind']}: source={item['source']} target={item['target']}")
    print(f"Audit saved: {dest}")
    if result["checks"]["numbers_preserved_in_order"] and result["checks"]["table_shape_preserved"] and result["checks"]["math_preserved_exactly"]:
        print("STRUCTURAL_CHECKS_PASS. Human review of Marathi meaning is still required.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
