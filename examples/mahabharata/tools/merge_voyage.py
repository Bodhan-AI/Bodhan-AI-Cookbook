#!/usr/bin/env python3
"""Merge the voyage PART files into web/data/mahabharata_voyage.json.

Inputs  (default): web/data/voyage_parts/world.json
                   web/data/voyage_parts/chapters_*.json   ({"chapters": [...], "extra_events": [...]})
Output  (default): web/data/mahabharata_voyage.json

What it does (DATA_CONTRACT.md section 3):
  * copies meta / locations / characters / relationships / events from world.json
  * appends every part's extra_events to events (world events first, then parts in file-name order)
  * collects all chapters and sorts them by chapter_order (ties broken by id, with a warning)
  * fills locations[].chapters with the ids of the chapters located there (in chapter order)
  * REFUSES (exit 1, nothing written) on duplicate ids: locations, characters, events, chapters,
    question ids, shot ids, discovery ids
  * writes UTF-8, 2-space-indented JSON atomically

Usage (from the project root, or anywhere: paths are relative to this script):
  python tools/merge_voyage.py
  python tools/merge_voyage.py --parts-dir path/to/parts --out path/to/merged.json
  python tools/merge_voyage.py --check          # merge in memory, report, write nothing

Stdlib only. Then run tools/validate_voyage.py on the output.
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
DEFAULT_PARTS = os.path.join(ROOT, "web", "data", "voyage_parts")
DEFAULT_OUT = os.path.join(ROOT, "web", "data", "mahabharata_voyage.json")

WORLD_KEYS = ("meta", "locations", "characters", "relationships", "events")
PART_KEYS = ("chapters", "extra_events")


class MergeError(Exception):
    pass


def natural_key(path):
    name = os.path.basename(path)
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise MergeError("%s: invalid JSON: %s" % (path, e))
    except OSError as e:
        raise MergeError("%s: cannot read: %s" % (path, e))


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


class IdBook:
    """Records where each id was first seen so duplicates can be reported with both sources."""

    def __init__(self, kind):
        self.kind = kind
        self.seen = {}

    def add(self, ident, where, errors):
        if not isinstance(ident, str) or not ident:
            errors.append("%s: %s has a missing or non-string id (%r)" % (where, self.kind, ident))
            return
        if ident in self.seen:
            errors.append("duplicate %s id '%s': %s and %s" % (self.kind, ident, self.seen[ident], where))
        else:
            self.seen[ident] = where


def merge(parts_dir):
    errors, warnings = [], []
    world_path = os.path.join(parts_dir, "world.json")
    if not os.path.isfile(world_path):
        raise MergeError("world part not found: %s" % world_path)
    world = load_json(world_path)
    if not isinstance(world, dict):
        raise MergeError("%s: top level must be an object" % world_path)

    for k in WORLD_KEYS:
        if k not in world:
            errors.append("world.json: missing top-level key '%s'" % k)
    for k in world:
        if k not in WORLD_KEYS:
            warnings.append("world.json: unexpected top-level key '%s' (dropped)" % k)
    if errors:
        raise MergeError("\n".join(errors))

    for k in ("locations", "characters", "relationships", "events"):
        if not isinstance(world[k], list):
            raise MergeError("world.json: '%s' must be a list" % k)

    loc_ids, char_ids = IdBook("location"), IdBook("character")
    event_ids, chapter_ids = IdBook("event"), IdBook("chapter")
    q_ids, shot_ids, disc_ids = IdBook("question"), IdBook("shot"), IdBook("discovery")

    for i, loc in enumerate(world["locations"]):
        loc_ids.add(loc.get("id") if isinstance(loc, dict) else None, "world.json locations[%d]" % i, errors)
    for i, ch in enumerate(world["characters"]):
        char_ids.add(ch.get("id") if isinstance(ch, dict) else None, "world.json characters[%d]" % i, errors)
    for i, ev in enumerate(world["events"]):
        event_ids.add(ev.get("id") if isinstance(ev, dict) else None, "world.json events[%d]" % i, errors)

    events = list(world["events"])
    chapters = []

    part_paths = sorted(glob.glob(os.path.join(parts_dir, "chapters_*.json")), key=natural_key)
    if not part_paths:
        warnings.append("no chapters_*.json part files found in %s" % parts_dir)

    for path in part_paths:
        name = os.path.basename(path)
        part = load_json(path)
        if not isinstance(part, dict):
            errors.append("%s: top level must be an object" % name)
            continue
        for k in part:
            if k not in PART_KEYS:
                warnings.append("%s: unexpected top-level key '%s' (dropped)" % (name, k))
        part_chapters = part.get("chapters", [])
        extra = part.get("extra_events", [])
        if not isinstance(part_chapters, list) or not isinstance(extra, list):
            errors.append("%s: 'chapters' and 'extra_events' must be lists" % name)
            continue
        if "chapters" not in part:
            warnings.append("%s: no 'chapters' key" % name)

        for i, ev in enumerate(extra):
            event_ids.add(ev.get("id") if isinstance(ev, dict) else None,
                          "%s extra_events[%d]" % (name, i), errors)
            events.append(ev)

        for i, ch in enumerate(part_chapters):
            where = "%s chapters[%d]" % (name, i)
            if not isinstance(ch, dict):
                errors.append("%s: chapter must be an object" % where)
                continue
            cid = ch.get("id")
            chapter_ids.add(cid, where, errors)
            where = "%s %s" % (name, cid)
            if not isinstance(ch.get("chapter_order"), (int, float)) or isinstance(ch.get("chapter_order"), bool):
                errors.append("%s: chapter_order must be a number (got %r)" % (where, ch.get("chapter_order")))
            qs = []
            if isinstance(ch.get("central_question"), dict):
                qs.append(ch["central_question"])
            if isinstance(ch.get("questions"), list):
                qs.extend(q for q in ch["questions"] if isinstance(q, dict))
            for q in qs:
                q_ids.add(q.get("id"), where, errors)
            for s in ch.get("shots", []) if isinstance(ch.get("shots"), list) else []:
                if isinstance(s, dict):
                    shot_ids.add(s.get("shot_id"), where, errors)
            for d in ch.get("optional_discoveries", []) if isinstance(ch.get("optional_discoveries"), list) else []:
                if isinstance(d, dict):
                    disc_ids.add(d.get("id"), where, errors)
            chapters.append(ch)

    if errors:
        raise MergeError("\n".join(errors))

    def order_key(ch):
        return (ch["chapter_order"], ch["id"])

    chapters.sort(key=order_key)
    orders = {}
    for ch in chapters:
        orders.setdefault(ch["chapter_order"], []).append(ch["id"])
    for o, ids in orders.items():
        if len(ids) > 1:
            warnings.append("chapter_order %s shared by %s (sorted by id)" % (o, ", ".join(ids)))

    # fill locations[].chapters
    by_loc = OrderedDict()
    for ch in chapters:
        by_loc.setdefault(ch.get("location_id"), []).append(ch["id"])
    known_locs = set(loc_ids.seen)
    locations = []
    for loc in world["locations"]:
        loc = dict(loc)
        prev = loc.get("chapters") or []
        filled = by_loc.get(loc["id"], [])
        if prev and prev != filled:
            warnings.append("location '%s': world.json chapters %r replaced by computed %r"
                            % (loc["id"], prev, filled))
        loc["chapters"] = filled
        locations.append(loc)
    for lid, ids in by_loc.items():
        if lid not in known_locs:
            warnings.append("chapters %s use unknown location_id %r" % (", ".join(ids), lid))

    merged = OrderedDict()
    merged["meta"] = world["meta"]
    merged["locations"] = locations
    merged["characters"] = world["characters"]
    merged["relationships"] = world["relationships"]
    merged["events"] = events
    merged["chapters"] = chapters

    stats = {
        "parts": len(part_paths),
        "locations": len(locations),
        "characters": len(world["characters"]),
        "relationships": len(world["relationships"]),
        "events": len(events),
        "extra_events": len(events) - len(world["events"]),
        "chapters": len(chapters),
        "questions": len(q_ids.seen),
        "shots": len(shot_ids.seen),
    }
    return merged, warnings, stats, part_paths


def main(argv=None):
    ap = argparse.ArgumentParser(description="Merge voyage part files into mahabharata_voyage.json")
    ap.add_argument("--parts-dir", default=DEFAULT_PARTS, help="directory with world.json and chapters_*.json")
    ap.add_argument("--out", default=DEFAULT_OUT, help="merged output path")
    ap.add_argument("--check", action="store_true", help="merge in memory and report; do not write")
    args = ap.parse_args(argv)

    try:
        merged, warnings, stats, part_paths = merge(args.parts_dir)
    except MergeError as e:
        print("MERGE FAILED - nothing written:", file=sys.stderr)
        for line in str(e).splitlines():
            print("  ERROR: " + line, file=sys.stderr)
        return 1

    print("Merged world.json + %d chapter part(s): %s" % (
        stats["parts"], ", ".join(os.path.basename(p) for p in part_paths) or "-"))
    print("  locations=%(locations)d characters=%(characters)d relationships=%(relationships)d "
          "events=%(events)d (extra %(extra_events)d) chapters=%(chapters)d questions=%(questions)d "
          "shots=%(shots)d" % stats)
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
