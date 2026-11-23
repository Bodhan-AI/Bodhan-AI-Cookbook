#!/usr/bin/env python3
"""Merge knowledge-graph parts into mahabharata_graph.json (DATA_CONTRACT.md section 4).

Inputs  (default): data_src/graph_parts/g*.json   each = {top-level key: {subtopic: [fact strings]}}
Output  (default): mahabharata_graph.json         (project root, next to helios_group.json)

  * parts are read in natural file-name order (g1, g2, ..., g10)
  * REFUSES (exit 1, nothing written) when the same top-level key appears in two parts
  * WARNS for any section-4 key that is missing, and for keys not in section 4 (kept)
  * output order: the section-4 keys in contract order, then any extra keys in part order
  * light format check (two levels, leaves = lists of non-empty strings) reported as warnings;
    run `tools/validate_voyage.py --graph` for the strict check
  * writes UTF-8, 2-space-indented JSON atomically

Usage (paths are relative to this script, so it runs from anywhere):
  python tools/merge_graph.py
  python tools/merge_graph.py --parts-dir path/to/graph_parts --out path/to/graph.json
  python tools/merge_graph.py --check           # report only, write nothing
  python tools/merge_graph.py --strict          # treat missing section-4 keys / format issues as errors

Stdlib only.
"""
import argparse
import glob
import json
import os
import re
import sys
import tempfile
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_PARTS = os.path.join(ROOT, "data_src", "graph_parts")
DEFAULT_OUT = os.path.join(ROOT, "mahabharata_graph.json")

# DATA_CONTRACT.md section 4 (also mirrored in mahabharata_voyage.schema.json $defs/graph_top_level_key)
GRAPH_KEYS = [
    "About This Knowledge Graph", "Structure of the Epic", "Frame Narratives and Narrators",
    "Kuru Dynasty and Lineage", "Characters — Pandava Side", "Characters — Kaurava Side",
    "Characters — Elders, Teachers and Others", "Krishna and the Yadavas",
    "Youth, Education and Rivalry", "Draupadi and the Swayamvara", "Indraprastha and the Rajasuya",
    "The Dice Game", "Exile in the Forest", "The Year in Virata", "Diplomacy and the Road to War",
    "Armies and Alliances", "The Bhagavad Gita", "The Kurukshetra War — Day by Day",
    "Battle Formations and Strategy", "Deaths of the Great Warriors", "Aftermath of the War",
    "Teachings After the War", "The Final Journey", "Places of the Epic", "Concepts and Themes",
    "Interpretations, Traditions and Debates",
]


def natural_key(path):
    name = os.path.basename(path)
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def format_problems(key, value):
    """Return a list of human-readable problems with one top-level entry (two-level format)."""
    probs = []
    if not isinstance(value, dict):
        return ["'%s': value must be an object of subtopics (got %s)" % (key, type(value).__name__)]
    if not value:
        probs.append("'%s': has no subtopics" % key)
    for sub, leaves in value.items():
        if not isinstance(sub, str) or not sub.strip():
            probs.append("'%s': empty subtopic name" % key)
        if not isinstance(leaves, list):
            probs.append("'%s' / '%s': leaf must be a list of strings (got %s)"
                         % (key, sub, type(leaves).__name__))
            continue
        if not leaves:
            probs.append("'%s' / '%s': empty fact list" % (key, sub))
        for i, fact in enumerate(leaves):
            if not isinstance(fact, str) or not fact.strip():
                probs.append("'%s' / '%s' [%d]: fact must be a non-empty string" % (key, sub, i))
    return probs


def write_json_atomic(path, data):
    out_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description="Merge graph parts into mahabharata_graph.json")
    ap.add_argument("--parts-dir", default=DEFAULT_PARTS, help="directory with g*.json parts")
    ap.add_argument("--out", default=DEFAULT_OUT, help="merged graph output path")
    ap.add_argument("--check", action="store_true", help="report only; do not write")
    ap.add_argument("--strict", action="store_true", help="missing section-4 keys and format problems are errors")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.parts_dir, "g*.json")), key=natural_key)
    if not paths:
        print("MERGE FAILED - no g*.json parts in %s" % args.parts_dir, file=sys.stderr)
        return 1

    errors, warnings = [], []
    collected = OrderedDict()
    origin = {}
    for path in paths:
        name = os.path.basename(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                part = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            errors.append("%s: cannot load: %s" % (name, e))
            continue
        if not isinstance(part, dict):
            errors.append("%s: top level must be an object" % name)
            continue
        for key, value in part.items():
            if key in origin:
                errors.append("duplicate top-level key '%s' in %s and %s" % (key, origin[key], name))
                continue
            origin[key] = name
            collected[key] = value
            for p in format_problems(key, value):
                warnings.append("%s: %s" % (name, p))

    missing = [k for k in GRAPH_KEYS if k not in collected]
    extra = [k for k in collected if k not in GRAPH_KEYS]
    for k in missing:
        warnings.append("section-4 key missing: '%s'" % k)
    for k in extra:
        warnings.append("key not in section 4 (kept): '%s' (from %s)" % (k, origin[k]))

    if args.strict:
        errors.extend(warnings)
        warnings = []

    if errors:
        print("MERGE FAILED - nothing written:", file=sys.stderr)
        for e in errors:
            print("  ERROR: " + e, file=sys.stderr)
        for w in warnings:
            print("  WARNING: " + w, file=sys.stderr)
        return 1

    merged = OrderedDict()
    for k in GRAPH_KEYS:
        if k in collected:
            merged[k] = collected[k]
    for k in extra:
        merged[k] = collected[k]

    n_sub = sum(len(v) for v in merged.values() if isinstance(v, dict))
    n_fact = sum(len(l) for v in merged.values() if isinstance(v, dict) for l in v.values() if isinstance(l, list))
    print("Merged %d part(s): %s" % (len(paths), ", ".join(os.path.basename(p) for p in paths)))
    print("  top-level keys=%d (section-4 present %d/%d) subtopics=%d facts=%d"
          % (len(merged), len(GRAPH_KEYS) - len(missing), len(GRAPH_KEYS), n_sub, n_fact))
    for w in warnings:
        print("  WARNING: " + w)
    if args.check:
        print("--check: nothing written")
    else:
        write_json_atomic(args.out, merged)
        print("Wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
