#!/usr/bin/env python3
"""Validate the merged Mahabharata voyage (DATA_CONTRACT.md) - schema + referential integrity.

Checks
  1. JSON Schema (draft 2020-12): web/data/mahabharata_voyage.schema.json on the merged voyage and
     web/data/asset_registry.schema.json on web/data/asset_registry.json.
  2. Referential integrity:
     - every location / character / event / chapter / question / discovery / staging / asset id
       referenced anywhere resolves; ids are unique (question, shot, discovery ids voyage-wide;
       staging and hotspot ids per chapter)
     - every canonical id of section 1 (locations, characters, events) is defined
     - every asset id used exists in asset_registry.json; every registry factory is in the allowed list
       (the engine's factories, section 3.2); char.* assets use the 'figure' factory
     - expected_topics are top-level keys of mahabharata_graph.json
     - unlock_conditions / next_chapters / previous_chapter / start_chapter refer to real chapters
     - main chapters have >= 3 shots (sub >= 2); every camera_path has >= 2 points; target_path, when
       present, matches camera_path length
     - locations[].chapters matches the chapters located there (as filled by merge_voyage.py)
  3. Optional --graph: graph format (two levels; leaves are lists of non-empty strings) and the
     section-4 key list.
  Contract style guidance (counts of paragraphs/questions, varied camera moves, etc.) is reported as
  warnings; use --strict to make warnings fail too.

Usage (paths are relative to this script, so it runs from anywhere):
  python tools/validate_voyage.py                       # validate web/data/mahabharata_voyage.json
  python tools/validate_voyage.py --graph               # ... and the knowledge graph format
  python tools/validate_voyage.py --graph-only          # only the knowledge graph
  python tools/validate_voyage.py --voyage X.json --registry R.json --graph-file G.json
  python tools/validate_voyage.py --strict --max-errors 500

Exit status: 0 = valid, 1 = validation errors (or warnings with --strict), 2 = could not load inputs.
Needs the 'jsonschema' package (e.g. /opt/miniconda3/envs/onto/bin/python).
"""
import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "web", "data")
WEB = os.path.join(ROOT, "web")
DEFAULTS = {
    "voyage": os.path.join(DATA, "mahabharata_voyage.json"),
    "schema": os.path.join(DATA, "mahabharata_voyage.schema.json"),
    "registry": os.path.join(DATA, "asset_registry.json"),
    "registry_schema": os.path.join(DATA, "asset_registry.schema.json"),
    "graph_file": os.path.join(ROOT, "mahabharata_graph.json"),
}

PARVA_NAMES = None  # filled from the schema


# --------------------------------------------------------------------------------------------- report
class Report:
    def __init__(self, max_errors):
        self.errors = []
        self.warnings = []
        self.notes = []
        self.max_errors = max_errors

    def err(self, where, msg):
        self.errors.append((where, msg))

    def warn(self, where, msg):
        self.warnings.append((where, msg))

    def note(self, msg):
        self.notes.append(msg)

    def print(self, strict):
        def emit(label, items, limit):
            shown = items if limit is None else items[:limit]
            for where, msg in shown:
                print("  %s %s: %s" % (label, where, msg))
            if limit is not None and len(items) > limit:
                print("  ... %d more %s(s) not shown (raise --max-errors)" % (len(items) - limit, label.lower()))

        for n in self.notes:
            print("  note: " + n)
        if self.errors:
            print("\nERRORS (%d)" % len(self.errors))
            emit("ERROR", self.errors, self.max_errors)
        if self.warnings:
            print("\nWARNINGS (%d)" % len(self.warnings))
            emit("WARN", self.warnings, self.max_errors)
        failed = bool(self.errors) or (strict and bool(self.warnings))
        print("\nRESULT: %s - %d error(s), %d warning(s)%s" % (
            "FAIL" if failed else "OK", len(self.errors), len(self.warnings),
            " (--strict: warnings count as failures)" if strict and self.warnings else ""))
        return failed


# --------------------------------------------------------------------------------------------- helpers
def load_json(path, what):
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError("%s %s is not valid JSON: %s" % (what, path, e))


def as_list(x):
    return x if isinstance(x, list) else []


def as_dict(x):
    return x if isinstance(x, dict) else {}


def is_vec3(v):
    return isinstance(v, list) and len(v) == 3 and all(
        isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in v)


def fmt_path(path):
    out = ""
    for p in path:
        out += "[%d]" % p if isinstance(p, int) else ("." + str(p) if out else str(p))
    return out or "(root)"


def friendly_path(doc, path):
    """Replace list indices with ids where possible: chapters[3] -> chapters[ch04]."""
    out, cur = "", doc
    for p in path:
        if isinstance(p, int) and isinstance(cur, list) and 0 <= p < len(cur):
            item = cur[p]
            ident = None
            if isinstance(item, dict):
                ident = item.get("id") or item.get("shot_id")
            out += "[%s]" % ident if isinstance(ident, str) else "[%d]" % p
            cur = item
        else:
            out += ("." + str(p)) if out else str(p)
            cur = cur.get(p) if isinstance(cur, dict) else None
    return out or "(root)"


# --------------------------------------------------------------------------------------------- schema
def make_validators(voyage_schema, registry_schema):
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012
    except ImportError:
        print("ERROR: the 'jsonschema' package is required (try /opt/miniconda3/envs/onto/bin/python)",
              file=sys.stderr)
        sys.exit(2)
    Draft202012Validator.check_schema(voyage_schema)
    if registry_schema is not None:
        Draft202012Validator.check_schema(registry_schema)
    resources = [(voyage_schema.get("$id", "mahabharata_voyage.schema.json"),
                  Resource.from_contents(voyage_schema, default_specification=DRAFT202012))]
    if registry_schema is not None:
        resources.append((registry_schema.get("$id", "asset_registry.schema.json"),
                          Resource.from_contents(registry_schema, default_specification=DRAFT202012)))
    reg = Registry().with_resources(resources)
    v = Draft202012Validator(voyage_schema, registry=reg)
    r = Draft202012Validator(registry_schema, registry=reg) if registry_schema is not None else None
    return v, r


def def_names(*schemas):
    names = {}
    for s in schemas:
        if not s:
            continue
        for name, sub in as_dict(s.get("$defs")).items():
            names[id(sub)] = name
    return names


def schema_errors(validator, doc, label, report, names):
    errs = sorted(validator.iter_errors(doc), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    for e in errs:
        where = "%s %s" % (label, friendly_path(doc, list(e.absolute_path)))
        if e.validator == "enum":
            dn = names.get(id(e.schema))
            msg = "%r is not an allowed %s" % (e.instance, dn or "value")
            if not dn and len(e.validator_value) <= 12:
                msg += " (allowed: %s)" % ", ".join(map(str, e.validator_value))
        elif e.validator in ("oneOf", "anyOf"):
            dn = names.get(id(e.schema))
            inst = json.dumps(e.instance, ensure_ascii=False)
            msg = "%s does not match %s" % (inst[:80] + ("..." if len(inst) > 80 else ""), dn or e.validator)
        elif e.validator == "additionalProperties" and isinstance(e.instance, dict):
            allowed = set(as_dict(e.schema.get("properties")))
            extra = sorted(k for k in e.instance if k not in allowed)
            msg = "unexpected field(s) %s (not in the contract)" % ", ".join(repr(k) for k in extra)
        elif e.validator in ("minItems", "maxItems") and isinstance(e.instance, list):
            msg = "has %d item(s); %s is %s" % (
                len(e.instance), "minimum" if e.validator == "minItems" else "maximum", e.validator_value)
        else:
            msg = e.message
            if len(msg) > 220:
                msg = msg[:220] + "..."
        report.err(where, "schema: " + msg)
    return len(errs)


# --------------------------------------------------------------------------------------------- graph
def check_graph_format(graph, report, canonical_keys):
    label = "graph"
    if not isinstance(graph, dict):
        report.err(label, "top level must be an object {top-level key: {subtopic: [facts]}}")
        return
    for key, value in graph.items():
        where = "graph[%s]" % key
        if not key.strip():
            report.err(where, "empty top-level key")
        if not isinstance(value, dict):
            report.err(where, "must be an object of subtopics (got %s)" % type(value).__name__)
            continue
        if not value:
            report.err(where, "has no subtopics")
        for sub, leaves in value.items():
            w2 = "%s[%s]" % (where, sub)
            if not sub.strip():
                report.err(w2, "empty subtopic name")
            if not isinstance(leaves, list):
                report.err(w2, "leaf must be a list of strings (got %s; graph is exactly two levels)"
                           % type(leaves).__name__)
                continue
            if not leaves:
                report.err(w2, "empty fact list")
            for i, fact in enumerate(leaves):
                if not isinstance(fact, str):
                    report.err("%s[%d]" % (w2, i), "fact must be a string (got %s)" % type(fact).__name__)
                elif not fact.strip():
                    report.err("%s[%d]" % (w2, i), "fact must be a non-empty string")
                elif "|" in fact and ":" not in fact.split("|")[0]:
                    report.warn("%s[%d]" % (w2, i),
                                "looks like a numeric fact but not in 'Label: A n | B m' form")
    for k in canonical_keys:
        if k not in graph:
            report.warn("graph", "section-4 key missing: '%s'" % k)
    for k in graph:
        if k not in canonical_keys:
            report.warn("graph", "top-level key not in section 4: '%s'" % k)


# --------------------------------------------------------------------------------------------- registry
def check_registry(registry, reg_schema, canonical_assets, report):
    assets = as_dict(registry.get("assets")) if isinstance(registry, dict) else {}
    factories = set(as_dict(as_dict(reg_schema.get("$defs")).get("factory")).get("enum", [])) if reg_schema else set()
    family_by_prefix = {"arch": "architecture", "char": "character", "obj": "object", "env": "environment"}
    for aid, a in assets.items():
        where = "registry[%s]" % aid
        if aid not in canonical_assets:
            report.err(where, "asset id is not in the canonical list (DATA_CONTRACT section 1)")
        if not isinstance(a, dict):
            continue
        prefix = aid.split(".", 1)[0]
        fam = a.get("family")
        if prefix in family_by_prefix and fam != family_by_prefix[prefix]:
            report.err(where, "family %r does not match id prefix '%s.' (expected %r)"
                       % (fam, prefix, family_by_prefix[prefix]))
        fb = as_dict(a.get("fallback"))
        fac = fb.get("factory")
        if factories and fac not in factories:
            report.err(where, "fallback.factory %r is not an engine factory" % fac)
        if prefix == "char" and fac != "figure":
            report.err(where, "character assets must use the 'figure' factory (got %r)" % fac)
        params = as_dict(fb.get("params"))
        att = params.get("attribute")
        if att is not None and att not in assets:
            report.err(where, "figure params.attribute %r is not in the registry" % att)
        if prefix == "char":
            for k in ("height", "robe", "skin", "pose"):
                if k not in params:
                    report.warn(where, "figure params lacks '%s'" % k)
        glb = a.get("glb")
        if isinstance(glb, str):
            if not (os.path.isfile(os.path.join(WEB, glb)) or os.path.isfile(os.path.join(ROOT, glb))):
                report.err(where, "glb %r does not exist (use null until a model really exists)" % glb)
    missing = [a for a in canonical_assets if a not in assets]
    if missing:
        report.warn("registry", "%d canonical asset id(s) have no registry entry (they cannot be used): %s"
                    % (len(missing), ", ".join(missing[:15]) + (" ..." if len(missing) > 15 else "")))
    return set(assets)


# --------------------------------------------------------------------------------------------- voyage
def check_voyage(doc, report, canon, registry_ids, graph_keys, graph_subtopics):
    meta = as_dict(doc.get("meta"))
    locations = [l for l in as_list(doc.get("locations")) if isinstance(l, dict)]
    characters = [c for c in as_list(doc.get("characters")) if isinstance(c, dict)]
    relationships = [r for r in as_list(doc.get("relationships")) if isinstance(r, dict)]
    events = [e for e in as_list(doc.get("events")) if isinstance(e, dict)]
    chapters = [c for c in as_list(doc.get("chapters")) if isinstance(c, dict)]

    def unique_ids(items, key, kind):
        seen = Counter(i.get(key) for i in items)
        for ident, n in seen.items():
            if n > 1:
                report.err(kind, "duplicate %s id %r (%d times)" % (kind, ident, n))
        return set(k for k in seen if isinstance(k, str))

    loc_ids = unique_ids(locations, "id", "location")
    char_ids = unique_ids(characters, "id", "character")
    event_ids = unique_ids(events, "id", "event")
    chapter_ids = unique_ids(chapters, "id", "chapter")
    chapter_by_id = {c.get("id"): c for c in chapters}

    # canonical presence
    for kind, have, want in (("location", loc_ids, canon["locations"]),
                             ("character", char_ids, canon["characters"]),
                             ("event", event_ids, canon["events"])):
        missing = [i for i in want if i not in have]
        if missing:
            report.err("canonical", "%d canonical %s id(s) not defined: %s" % (len(missing), kind, ", ".join(missing)))

    # questions / discoveries / shots (voyage-wide)
    question_owner, discovery_owner, shot_owner, hotspot_owner = {}, {}, {}, defaultdict(list)
    for ch in chapters:
        cid = ch.get("id")
        qs = ([ch["central_question"]] if isinstance(ch.get("central_question"), dict) else []) + \
             [q for q in as_list(ch.get("questions")) if isinstance(q, dict)]
        for q in qs:
            qid = q.get("id")
            if qid in question_owner:
                report.err("question", "duplicate question id %r (in %s and %s)" % (qid, question_owner[qid], cid))
            else:
                question_owner[qid] = cid
        for d in as_list(ch.get("optional_discoveries")):
            did = as_dict(d).get("id")
            if did in discovery_owner:
                report.err("discovery", "duplicate discovery id %r (in %s and %s)" % (did, discovery_owner[did], cid))
            else:
                discovery_owner[did] = cid
        for s in as_list(ch.get("shots")):
            sid = as_dict(s).get("shot_id")
            if sid in shot_owner:
                report.err("shot", "duplicate shot id %r (in %s and %s)" % (sid, shot_owner[sid], cid))
            else:
                shot_owner[sid] = cid
            for h in as_list(as_dict(s).get("interactive_hotspots")):
                hotspot_owner[as_dict(h).get("id")].append(cid)
    for hid, owners in hotspot_owner.items():
        if len(set(owners)) > 1:
            report.warn("hotspot", "hotspot id %r reused in chapters %s" % (hid, ", ".join(sorted(set(owners)))))
    question_ids = set(k for k in question_owner if isinstance(k, str))
    discovery_ids = set(k for k in discovery_owner if isinstance(k, str))

    targets = {
        "character": char_ids, "location": loc_ids, "event": event_ids,
        "question": question_ids, "chapter": chapter_ids, "discovery": discovery_ids,
    }

    def ref(where, kind, value):
        pool = targets.get(kind)
        if pool is None:
            report.err(where, "unknown target type %r" % kind)
        elif value not in pool:
            report.err(where, "%s %r does not exist" % (kind, value))

    def asset_ref(where, aid):
        if registry_ids is None:  # registry unavailable (already reported): check the canonical list only
            if aid not in canon["assets"]:
                report.err(where, "asset %r is not a canonical asset id" % aid)
        elif aid not in registry_ids:
            if aid in canon["assets"]:
                report.err(where, "asset %r is canonical but missing from asset_registry.json" % aid)
            else:
                report.err(where, "asset %r is not a canonical asset id / not in the registry" % aid)

    # meta
    if meta.get("start_chapter") is not None:
        ref("meta.start_chapter", "chapter", meta.get("start_chapter"))

    # locations
    loc_by_id = {l.get("id"): l for l in locations}
    for loc in locations:
        lid = loc.get("id")
        w = "location %s" % lid
        for p in as_list(loc.get("paths_to")):
            ref(w + " paths_to", "location", p)
            if p == lid:
                report.warn(w, "paths_to contains itself")
        for i, item in enumerate(as_list(loc.get("base_layout"))):
            if isinstance(item, dict) and "asset" in item:
                asset_ref("%s base_layout[%d]" % (w, i), item.get("asset"))
        expected = [c.get("id") for c in chapters if c.get("location_id") == lid]
        got = as_list(loc.get("chapters"))
        for c in got:
            ref(w + " chapters", "chapter", c)
        if sorted(map(str, got)) != sorted(map(str, expected)):
            report.err(w, "chapters %r != chapters located here %r (re-run tools/merge_voyage.py)" % (got, expected))
        if loc.get("traditional_identification") and "traditionally identified" not in str(
                loc.get("traditional_identification")).lower():
            report.warn(w, "traditional_identification should be phrased 'traditionally identified with ...'")

    # characters
    for c in characters:
        cid = c.get("id")
        w = "character %s" % cid
        if c.get("first_chapter") is not None:
            ref(w + " first_chapter", "chapter", c.get("first_chapter"))
        if c.get("asset") != "char.%s" % cid:
            report.err(w, "asset must be 'char.%s' (got %r)" % (cid, c.get("asset")))
        elif c.get("asset") is not None:
            asset_ref(w + " asset", c.get("asset"))
        for a in as_list(as_dict(c.get("visual_identity")).get("attributes")):
            asset_ref(w + " visual_identity.attributes", a)

    # relationships
    seen_rel = Counter()
    for i, r in enumerate(relationships):
        w = "relationships[%d] %s->%s" % (i, r.get("from"), r.get("to"))
        ref(w + " from", "character", r.get("from"))
        ref(w + " to", "character", r.get("to"))
        if r.get("from") == r.get("to"):
            report.err(w, "relationship from a character to itself")
        seen_rel[(r.get("from"), r.get("to"), r.get("type"))] += 1
    for (a, b, t), n in seen_rel.items():
        if n > 1:
            report.warn("relationships", "duplicate relationship %s -%s-> %s (%d times)" % (a, t, b, n))

    # events
    orders = defaultdict(list)
    canon_events = set(canon["events"])
    for e in events:
        eid = e.get("id")
        w = "event %s" % eid
        if e.get("chapter") is not None:
            ref(w + " chapter", "chapter", e.get("chapter"))
        if e.get("location_id") is not None:
            ref(w + " location_id", "location", e.get("location_id"))
        for c in as_list(e.get("characters")):
            ref(w + " characters", "character", c)
        if isinstance(e.get("order"), int):
            orders[e["order"]].append(eid)
        if isinstance(eid, str) and eid not in canon_events and not eid.startswith("ev_"):
            report.warn(w, "new event id should start with 'ev_'")
    for o, ids in sorted(orders.items()):
        if len(ids) > 1:
            report.warn("events", "order %d shared by %s" % (o, ", ".join(map(str, ids))))

    # chapters
    prev_order = None
    order_count = Counter(c.get("chapter_order") for c in chapters)
    for o, n in order_count.items():
        if n > 1:
            report.warn("chapters", "chapter_order %s used by %d chapters" % (o, n))
    for ch in chapters:
        check_chapter(ch, report, ref, asset_ref, targets, registry_ids, loc_by_id, chapter_by_id,
                      graph_keys, graph_subtopics)
        o = ch.get("chapter_order")
        if isinstance(o, (int, float)) and prev_order is not None and o < prev_order:
            report.warn("chapters", "not sorted by chapter_order at %s (re-run merge_voyage.py)" % ch.get("id"))
        if isinstance(o, (int, float)):
            prev_order = o

    # reachability from start_chapter
    start = meta.get("start_chapter")
    if start in chapter_by_id:
        seen, todo = {start}, deque([start])
        while todo:
            cur = chapter_by_id.get(todo.popleft(), {})
            nxt = list(as_list(cur.get("next_chapters")))
            for q in [cur.get("central_question")] + as_list(cur.get("questions")):
                q = as_dict(q)
                ns = as_dict(q.get("next_stage"))
                if ns.get("type") == "chapter":
                    nxt.append(ns.get("target"))
                for b in as_list(q.get("branching_options")):
                    if as_dict(b).get("target_type") == "chapter":
                        nxt.append(as_dict(b).get("target"))
            for d in as_list(cur.get("optional_discoveries")):
                if as_dict(d).get("type") == "chapter":
                    nxt.append(as_dict(d).get("target"))
            for n in nxt:
                if n in chapter_by_id and n not in seen:
                    seen.add(n)
                    todo.append(n)
        unreachable = [c for c in chapter_by_id if c not in seen and isinstance(c, str)]
        if unreachable:
            report.warn("navigation", "chapters not reachable from start_chapter %s: %s"
                        % (start, ", ".join(sorted(unreachable))))

    return {
        "locations": len(locations), "characters": len(characters), "relationships": len(relationships),
        "events": len(events), "chapters": len(chapters), "questions": len(question_ids),
        "shots": len(shot_owner), "discoveries": len(discovery_ids),
    }


def check_chapter(ch, report, ref, asset_ref, targets, registry_ids, loc_by_id, chapter_by_id,
                  graph_keys, graph_subtopics):
    cid = ch.get("id")
    W = "chapter %s" % cid
    kind = ch.get("kind")
    if isinstance(cid, str):
        is_sub_id = bool(re.match(r"^ch[0-9]{2,3}[a-z]$", cid))
        if kind == "main" and is_sub_id:
            report.warn(W, "kind 'main' but id looks like a sub-chapter id")
        if kind == "sub" and not is_sub_id:
            report.warn(W, "kind 'sub' but id has no letter suffix (e.g. ch05b)")

    lid = ch.get("location_id")
    if lid is not None:
        ref(W + " location_id", "location", lid)
    for c in as_list(ch.get("related_characters")):
        ref(W + " related_characters", "character", c)
    for l in as_list(ch.get("related_locations")):
        ref(W + " related_locations", "location", l)
    for e in as_list(ch.get("related_events")):
        ref(W + " related_events", "event", e)

    narrative = as_list(ch.get("narrative"))
    if narrative and not 2 <= len(narrative) <= 4:
        report.warn(W, "narrative has %d paragraph(s); contract asks for 2-4" % len(narrative))

    sc = as_dict(ch.get("story_context"))
    if sc.get("parva") and PARVA_NAMES:
        p = str(sc["parva"]).replace(" Parva", "").replace(" parva", "").strip()
        if p not in PARVA_NAMES:
            report.warn(W + " story_context.parva", "%r is not one of the 18 parva names" % sc["parva"])

    # staging
    staging_ids = set()
    staging_assets = set()
    for i, s in enumerate(as_list(ch.get("staging"))):
        s = as_dict(s)
        sid = s.get("id")
        if sid in staging_ids:
            report.err(W, "duplicate staging id %r" % sid)
        staging_ids.add(sid)
        if "asset" in s:
            asset_ref("%s staging[%s]" % (W, sid or i), s.get("asset"))
            staging_assets.add(s.get("asset"))
    staging_ids.discard(None)

    base_assets = set(as_dict(i).get("asset") for i in as_list(as_dict(loc_by_id.get(lid)).get("base_layout")))
    used_assets = set(staging_assets)

    # shots
    shots = as_list(ch.get("shots"))
    min_shots = 3 if kind == "main" else 2
    if len(shots) < min_shots:
        report.err(W, "%s chapter has %d shot(s); needs >= %d" % (kind, len(shots), min_shots))
    movements = []
    for i, s in enumerate(shots):
        s = as_dict(s)
        sid = s.get("shot_id") or i
        w = "%s shot %s" % (W, sid)
        if isinstance(sid, str) and isinstance(cid, str) and not sid.startswith("sh_%s_" % cid):
            report.warn(w, "shot id should start with 'sh_%s_'" % cid)
        movements.append(s.get("camera_movement"))
        cp = s.get("camera_path")
        if not isinstance(cp, list) or len(cp) < 2:
            report.err(w, "camera_path needs >= 2 points (got %s)" % (len(cp) if isinstance(cp, list) else cp))
        else:
            for j, pt in enumerate(cp):
                if not is_vec3(pt):
                    report.err(w, "camera_path[%d] is not [x,y,z]" % j)
        tp = s.get("target_path")
        if tp is not None and isinstance(cp, list) and isinstance(tp, list) and len(tp) != len(cp):
            report.err(w, "target_path has %d points but camera_path has %d" % (len(tp), len(cp)))
        env = as_dict(s.get("environment"))
        if env.get("sky") is not None:
            asset_ref(w + " environment.sky", env.get("sky"))
            used_assets.add(env.get("sky"))
        for a in as_list(env.get("elements")):
            asset_ref(w + " environment.elements", a)
            used_assets.add(a)
        for k, cue in enumerate(as_list(s.get("animation_cues"))):
            cue = as_dict(cue)
            t = cue.get("target")
            if t in staging_ids:
                continue
            if isinstance(t, str) and "." in t:
                asset_ref("%s animation_cues[%d].target" % (w, k), t)
                used_assets.add(t)
                if t not in staging_assets and t not in base_assets:
                    report.warn("%s animation_cues[%d]" % (w, k),
                                "animates asset %r that is neither staged in this chapter nor in the location layout" % t)
            else:
                report.err("%s animation_cues[%d].target" % (w, k),
                           "%r is neither a staging id of %s nor an asset id" % (t, cid))
            if isinstance(cue.get("at"), (int, float)) and isinstance(s.get("duration"), (int, float)) \
                    and cue["at"] > s["duration"]:
                report.warn("%s animation_cues[%d]" % (w, k), "at=%s is after the shot ends (%ss)" % (cue["at"], s["duration"]))
        hs_seen = set()
        for h in as_list(s.get("interactive_hotspots")):
            h = as_dict(h)
            if h.get("id") in hs_seen:
                report.err(w, "duplicate hotspot id %r" % h.get("id"))
            hs_seen.add(h.get("id"))
            if h.get("target_type") is not None:
                ref("%s hotspot %s" % (w, h.get("id")), h.get("target_type"), h.get("target"))
    if len(shots) >= 3 and len(set(movements)) == 1:
        report.warn(W, "every shot uses camera_movement %r; contract asks for varied cinematography" % movements[0])

    # assets[]
    listed = set()
    for a in as_list(ch.get("assets")):
        asset_ref(W + " assets", a)
        listed.add(a)
    not_listed = sorted(a for a in used_assets - listed - base_assets if isinstance(a, str))
    if not_listed:
        report.warn(W + " assets", "used but not listed: %s" % ", ".join(not_listed))

    # questions
    qs = []
    if isinstance(ch.get("central_question"), dict):
        qs.append(("central_question", ch["central_question"]))
    more = [q for q in as_list(ch.get("questions")) if isinstance(q, dict)]
    qs.extend(("questions[%d]" % i, q) for i, q in enumerate(more))
    if not 3 <= len(more) <= 5:
        report.warn(W, "has %d extra question(s); contract asks for 3-5 besides the central question" % len(more))
    for label, q in qs:
        qid = q.get("id")
        w = "%s %s %s" % (W, label, qid)
        if isinstance(qid, str) and isinstance(cid, str) and not qid.startswith("q_%s_" % cid):
            report.err(w, "question id must start with 'q_%s_'" % cid)
        for t in as_list(q.get("expected_topics")):
            if t in graph_keys:
                continue
            if t in graph_subtopics:
                report.warn(w + " expected_topics", "%r is a subtopic, not a top-level graph key (under: %s)"
                            % (t, ", ".join(sorted(graph_subtopics[t]))[:120]))
            else:
                report.err(w + " expected_topics", "%r is not a top-level key of the knowledge graph" % t)
        re_ = as_dict(q.get("related_entities"))
        for c in as_list(re_.get("characters")):
            ref(w + " related_entities.characters", "character", c)
        for l in as_list(re_.get("locations")):
            ref(w + " related_entities.locations", "location", l)
        for e in as_list(re_.get("events")):
            ref(w + " related_entities.events", "event", e)
        ns = as_dict(q.get("next_stage"))
        if ns.get("type") is not None:
            ref(w + " next_stage", ns.get("type"), ns.get("target"))
            if ns.get("type") == "question" and ns.get("target") == qid:
                report.err(w + " next_stage", "question points to itself")
        for j, b in enumerate(as_list(q.get("branching_options"))):
            b = as_dict(b)
            if b.get("target_type") is not None:
                ref("%s branching_options[%d]" % (w, j), b.get("target_type"), b.get("target"))

    # navigation
    nav = as_dict(ch.get("navigation"))
    if nav.get("map_focus") is not None:
        ref(W + " navigation.map_focus", "location", nav.get("map_focus"))
    if nav.get("timeline_anchor") is not None:
        ref(W + " navigation.timeline_anchor", "event", nav.get("timeline_anchor"))

    # unlock
    uc = as_dict(ch.get("unlock_conditions"))
    for c in as_list(uc.get("chapters")):
        ref(W + " unlock_conditions.chapters", "chapter", c)
        if c == cid:
            report.err(W + " unlock_conditions", "chapter requires itself")
    for q in as_list(uc.get("questions")):
        ref(W + " unlock_conditions.questions", "question", q)
    t = uc.get("type")
    if t == "always" and (as_list(uc.get("chapters")) or as_list(uc.get("questions"))):
        report.warn(W + " unlock_conditions", "type 'always' but chapters/questions are listed")
    if t == "chapter_visited" and not as_list(uc.get("chapters")):
        report.err(W + " unlock_conditions", "type 'chapter_visited' needs at least one chapter")
    if t == "question_answered" and not as_list(uc.get("questions")):
        report.err(W + " unlock_conditions", "type 'question_answered' needs at least one question")
    if t == "any_of" and not (as_list(uc.get("chapters")) or as_list(uc.get("questions"))):
        report.err(W + " unlock_conditions", "type 'any_of' needs chapters or questions")

    # previous / next
    prev = ch.get("previous_chapter")
    if prev is not None:
        ref(W + " previous_chapter", "chapter", prev)
        if prev == cid:
            report.err(W, "previous_chapter is itself")
        elif prev in chapter_by_id and cid not in as_list(chapter_by_id[prev].get("next_chapters")):
            report.warn(W, "previous_chapter %s does not list %s in its next_chapters" % (prev, cid))
    for n in as_list(ch.get("next_chapters")):
        ref(W + " next_chapters", "chapter", n)
        if n == cid:
            report.err(W, "next_chapters contains itself")

    # discoveries
    for d in as_list(ch.get("optional_discoveries")):
        d = as_dict(d)
        if d.get("type") is not None:
            ref("%s discovery %s" % (W, d.get("id")), d.get("type"), d.get("target"))


# --------------------------------------------------------------------------------------------- main
def main(argv=None):
    global PARVA_NAMES
    ap = argparse.ArgumentParser(description="Validate the merged Mahabharata voyage (schema + references)")
    ap.add_argument("--voyage", default=DEFAULTS["voyage"])
    ap.add_argument("--schema", default=DEFAULTS["schema"])
    ap.add_argument("--registry", default=DEFAULTS["registry"])
    ap.add_argument("--registry-schema", default=DEFAULTS["registry_schema"])
    ap.add_argument("--graph-file", default=DEFAULTS["graph_file"], help="knowledge graph JSON")
    ap.add_argument("--graph", action="store_true", help="also validate the knowledge-graph format")
    ap.add_argument("--graph-only", action="store_true", help="validate only the knowledge graph")
    ap.add_argument("--strict", action="store_true", help="warnings also make the run fail")
    ap.add_argument("--max-errors", type=int, default=200, help="max errors/warnings printed per list")
    args = ap.parse_args(argv)

    report = Report(args.max_errors)
    def rel(p):
        r = os.path.relpath(p)
        return p if r.startswith(".." + os.sep + "..") else r

    try:
        voyage_schema = load_json(args.schema, "schema")
    except (OSError, ValueError) as e:
        print("ERROR: cannot load schema: %s" % e, file=sys.stderr)
        return 2
    vdefs = as_dict(voyage_schema.get("$defs"))
    enum_of = lambda name: list(as_dict(vdefs.get(name)).get("enum", []))
    canon = {
        "locations": enum_of("location_id"), "characters": enum_of("character_id"),
        "events": enum_of("canonical_event_id"), "assets": enum_of("asset_id"),
        "graph_keys": enum_of("graph_top_level_key"),
    }
    PARVA_NAMES = set(enum_of("parva"))

    # ---- graph
    graph = None
    if os.path.isfile(args.graph_file):
        try:
            graph = load_json(args.graph_file, "graph")
        except (OSError, ValueError) as e:
            report.err("graph", str(e))
    print("Mahabharata voyage validation")
    print("  schema:   %s" % rel(args.schema))
    if args.graph or args.graph_only:
        print("  graph:    %s" % rel(args.graph_file))
        if graph is None:
            report.err("graph", "graph file not found or unreadable: %s" % rel(args.graph_file))
        else:
            check_graph_format(graph, report, canon["graph_keys"])
    if args.graph_only:
        return 1 if report.print(args.strict) else 0

    if isinstance(graph, dict):
        graph_keys = set(graph)
        graph_subtopics = defaultdict(set)
        for k, v in graph.items():
            for sub in as_dict(v):
                graph_subtopics[sub].add(k)
    else:
        graph_keys = set(canon["graph_keys"])
        graph_subtopics = {}
        report.warn("graph", "%s not available; expected_topics checked against the section-4 key list"
                    % rel(args.graph_file))

    # ---- registry
    reg_schema, registry = None, None
    try:
        reg_schema = load_json(args.registry_schema, "registry schema")
    except (OSError, ValueError) as e:
        report.err("registry", "cannot load registry schema: %s" % e)
    try:
        registry = load_json(args.registry, "registry")
    except (OSError, ValueError) as e:
        report.err("registry", "cannot load asset registry (asset ids cannot be resolved): %s" % e)

    voyage_validator, registry_validator = make_validators(voyage_schema, reg_schema)
    names = def_names(voyage_schema, reg_schema)

    registry_ids = None
    print("  registry: %s" % rel(args.registry))
    if registry is not None:
        if registry_validator is not None:
            schema_errors(registry_validator, registry, "registry", report, names)
        registry_ids = check_registry(registry, reg_schema, canon["assets"], report)

    # ---- voyage
    print("  voyage:   %s" % rel(args.voyage))
    try:
        doc = load_json(args.voyage, "voyage")
    except (OSError, ValueError) as e:
        report.err("voyage", "cannot load: %s (run tools/merge_voyage.py first)" % e)
        report.print(args.strict)
        return 2
    n_schema = schema_errors(voyage_validator, doc, "voyage", report, names)
    if not isinstance(doc, dict):
        report.print(args.strict)
        return 1
    stats = check_voyage(doc, report, canon, registry_ids, graph_keys, graph_subtopics)
    print("  counts:   " + " ".join("%s=%d" % kv for kv in stats.items()) +
          " registry_assets=%s" % (len(registry_ids) if registry_ids is not None else "n/a"))
    print("  schema errors: %d (voyage)" % n_schema)
    return 1 if report.print(args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
