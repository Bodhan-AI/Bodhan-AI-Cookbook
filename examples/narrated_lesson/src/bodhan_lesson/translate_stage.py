"""Stage 3: translate the assembled lesson into one target language.

Math is swapped for placeholders before translation and restored afterwards. Every translation
is validated (placeholders, numerals, structure). A paragraph that fails is retried sentence by
sentence, and a sentence that still fails is kept in English, flagged, and left out of narration.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .assemble import render_markdown

MATH_RE = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$", re.S)
PLACEHOLDER_RE = re.compile(r"⟦\s*M\s*(\d+)\s*⟧")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+(?=[A-Z$(\[*])")
PROSE_TYPES = {"title", "heading", "subheading", "paragraph", "example", "question", "list", "caption"}


# ---------- protecting math ----------

def protect(text: str) -> tuple[str, list[str]]:
    """Replace each math span with a placeholder like ⟦M1⟧. Returns (masked_text, spans)."""
    spans: list[str] = []

    def swap(m: re.Match) -> str:
        spans.append(m.group(0))
        return f"⟦M{len(spans)}⟧"

    return MATH_RE.sub(swap, text), spans


def normalise_placeholders(text: str) -> str:
    """Undo small damage such as '⟦ M1 ⟧' so the check does not fail on spacing alone."""
    return PLACEHOLDER_RE.sub(lambda m: f"⟦M{m.group(1)}⟧", text)


def restore(text: str, spans: list[str]) -> str:
    for i, span in enumerate(spans, start=1):
        text = text.replace(f"⟦M{i}⟧", span)
    return text


def ascii_digits(text: str) -> str:
    """Map digits of any script (Devanagari, Tamil, Bengali...) to 0-9 so numerals compare equal."""
    return "".join(str(unicodedata.digit(c)) if unicodedata.category(c) == "Nd" else c for c in text)


def numbers_in(masked: str) -> list[str]:
    return sorted(NUM_RE.findall(ascii_digits(PLACEHOLDER_RE.sub("", masked))))


def validate(source_masked: str, output: str, n_spans: int) -> list[str]:
    """Problems with a translation, as short codes. Empty list means it passed."""
    problems = []
    found = PLACEHOLDER_RE.findall(output)
    for i in range(1, n_spans + 1):
        if found.count(str(i)) != 1:
            problems.append(f"placeholder_{i}_count_{found.count(str(i))}")
    if any(int(n) > n_spans or int(n) < 1 for n in found):
        problems.append("unexpected_placeholder")
    if numbers_in(source_masked) != numbers_in(output):
        problems.append("numerals_changed")
    if not output.strip():
        problems.append("empty_output")
    return problems


# ---------- translating text ----------

def split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT.split(text.strip()) if s]


def translate_once(client, text: str, lang: str) -> tuple[str | None, list[str]]:
    """Translate one piece of text with math protected. Returns (translation or None, problems)."""
    masked, spans = protect(text)
    res = client.translate(masked, lang)
    if res.get("truncated"):
        return None, ["truncated"]
    out = normalise_placeholders(res["text"])
    problems = validate(masked, out, len(spans))
    if problems:
        return None, problems
    return restore(out, spans), []


def translate_text(client, text: str, lang: str) -> tuple[str, list[str]]:
    """Translate text; fall back to sentence by sentence. Returns (text, issues).

    Sentences that cannot be translated safely stay in English and are listed in `issues`.
    """
    if not text.strip():
        return text, []
    out, problems = translate_once(client, text, lang)
    if out is not None:
        return out, []
    sentences = split_sentences(text)
    if len(sentences) <= 1:
        return text, [f"kept_in_english:{','.join(problems)}"]
    parts, issues = [], []
    for s in sentences:
        o, p = translate_once(client, s, lang)
        if o is None:
            parts.append(s)
            issues.append(f"kept_in_english:{','.join(p)}:{s[:40]}")
        else:
            parts.append(o)
    return " ".join(parts), issues


def translate_table(client, text: str, lang: str) -> tuple[str, list[str]]:
    """Translate only the words in a table, never its markup. Handles Markdown and HTML tables."""
    issues: list[str] = []

    def cell(value: str) -> str:
        if not value.strip() or re.fullmatch(r"[\s:\-|]*", value):
            return value
        out, iss = translate_text(client, value.strip(), lang)
        issues.extend(iss)
        return f" {out} "

    if "<table" in text.lower():
        return re.sub(r">([^<>]+)<", lambda m: ">" + cell(m.group(1)).strip() + "<", text), issues
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            cells = line.strip().strip("|").split("|")
            line = "|" + "|".join(cell(c) for c in cells) + "|"
        lines.append(line)
    return "\n".join(lines), issues


# ---------- batching ----------

BOUNDARY_RE = re.compile(r"⟦\s*B\s*(\d+)\s*⟧")


def make_batches(texts: list[str], max_chars: int = 1800, max_items: int = 6) -> list[list[int]]:
    """Group consecutive texts (by index) so one API call carries several short pieces."""
    batches: list[list[int]] = []
    current: list[int] = []
    size = 0
    for i, t in enumerate(texts):
        if current and (size + len(t) > max_chars or len(current) >= max_items):
            batches.append(current)
            current, size = [], 0
        current.append(i)
        size += len(t)
    if current:
        batches.append(current)
    return batches


def translate_batch(client, texts: list[str], lang: str) -> list[str | None]:
    """One API call for several texts, split apart again with boundary tokens ⟦B1⟧, ⟦B2⟧...

    Returns one entry per text: the translation, or None where that piece failed validation.
    If the boundaries did not survive, every entry is None.
    """
    masked, spans = [], []
    for t in texts:
        m, sp = protect(t)
        masked.append(m)
        spans.append(sp)
    joined = " ".join(f"⟦B{i}⟧ {m}" for i, m in enumerate(masked, start=1))
    res = client.translate(joined, lang)
    none = [None] * len(texts)
    if res.get("truncated"):
        return none
    parts = BOUNDARY_RE.split(normalise_placeholders(res["text"]))
    if parts[0].strip() or parts[1::2] != [str(i) for i in range(1, len(texts) + 1)]:
        return none
    out: list[str | None] = []
    for piece, m, sp in zip(parts[2::2], masked, spans):
        piece = piece.strip()
        out.append(None if validate(m, piece, len(sp)) else restore(piece, sp))
    return out


def translate_group(client, texts: list[str], lang: str) -> list[tuple[str, list[str]]]:
    """Translate a group of texts: one batched call first, then the careful path for any piece that failed."""
    batched = translate_batch(client, texts, lang) if len(texts) > 1 else [None] * len(texts)
    results = []
    for text, done in zip(texts, batched):
        results.append((done, []) if done is not None else translate_text(client, text, lang))
    return results


# ---------- whole lesson ----------

def translate_element(client, el: dict, lang: str) -> dict:
    """Return a translated copy of one element. Equations and gaps are copied unchanged."""
    out = dict(el)
    out["flags"] = list(el.get("flags", []))
    issues: list[str] = []
    if el["type"] in PROSE_TYPES:
        out["text"], issues = translate_text(client, el["text"], lang)
    elif el["type"] == "table":
        out["text"], issues = translate_table(client, el["text"], lang)
    elif el["type"] == "figure":
        if el.get("caption"):
            out["caption"], iss = translate_text(client, el["caption"], lang)
            issues += iss
        labels = []
        for label in el.get("labels", []):
            t, iss = translate_text(client, label, lang)
            labels.append(t)
            issues += iss
        out["labels"] = labels
    if issues:
        out["flags"] += ["translation_issue"]
        out["translation_issues"] = issues
    return out


def structure_report(source: list[dict], target: list[dict]) -> dict:
    """Same element types in the same order, and equations identical to the source."""
    src_types = [e["type"] for e in source]
    tgt_types = [e["type"] for e in target]
    eq_ok = all(a["text"] == b["text"] for a, b in zip(source, target) if a["type"] == "equation")
    return {"same_order_and_types": src_types == tgt_types, "equations_identical": eq_ok,
            "headings": sum(t in {"title", "heading", "subheading"} for t in tgt_types),
            "tables": tgt_types.count("table"), "equations": tgt_types.count("equation")}


def collect_jobs(elements: list[dict]) -> list[tuple[int, str, int | None, str]]:
    """Every piece of prose to translate: (element index, field, label index, text), in reading order."""
    jobs = []
    for i, e in enumerate(elements):
        if e["type"] in PROSE_TYPES:
            jobs.append((i, "text", None, e["text"]))
        elif e["type"] == "figure":
            if e.get("caption"):
                jobs.append((i, "caption", None, e["caption"]))
            for j, label in enumerate(e.get("labels", [])):
                jobs.append((i, "label", j, label))
    return jobs


def run_translate(client, out_dir: Path, lang: str, workers: int = 4) -> dict:
    """Translate out/source/lesson.structure.json into out/<lang>/lesson.<lang>.md and a JSON copy."""
    source = json.loads((out_dir / "source" / "lesson.structure.json").read_text(encoding="utf-8"))
    elements = source["elements"]
    translated = [dict(e, flags=list(e.get("flags", []))) for e in elements]
    for e in translated:
        if e["type"] == "figure":
            e["labels"] = list(e.get("labels", []))
    issues_by_element: dict[int, list[str]] = {}

    jobs = collect_jobs(elements)
    texts = [j[3] for j in jobs]
    # LESSON_BATCH_ITEMS=1 turns batching off (one piece per call), useful if a model mangles boundaries
    groups = make_batches(texts, max_items=max(1, int(os.environ.get("LESSON_BATCH_ITEMS", "6"))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        group_results = list(pool.map(lambda g: translate_group(client, [texts[k] for k in g], lang), groups))
        table_ids = [i for i, e in enumerate(elements) if e["type"] == "table"]
        table_results = list(pool.map(lambda i: translate_table(client, elements[i]["text"], lang), table_ids))

    for group, results in zip(groups, group_results):
        for k, (out_text, issues) in zip(group, results):
            i, field, j, _ = jobs[k]
            if field == "label":
                translated[i]["labels"][j] = out_text
            else:
                translated[i][field] = out_text
            if issues:
                issues_by_element.setdefault(i, []).extend(issues)
    for i, (out_text, issues) in zip(table_ids, table_results):
        translated[i]["text"] = out_text
        if issues:
            issues_by_element.setdefault(i, []).extend(issues)
    for i, issues in issues_by_element.items():
        translated[i]["flags"] += ["translation_issue"]
        translated[i]["translation_issues"] = issues

    lang_dir = out_dir / lang
    lang_dir.mkdir(parents=True, exist_ok=True)
    (lang_dir / f"lesson.{lang}.md").write_text(render_markdown(translated), encoding="utf-8")
    (lang_dir / f"lesson.{lang}.structure.json").write_text(
        json.dumps({"elements": translated, "sections": source["sections"]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    issues = [{"page": e["page"], "type": e["type"], "issues": e["translation_issues"]}
              for e in translated if e.get("translation_issues")]
    return {"language": lang, "elements": len(translated), "api_groups": len(groups),
            "translation_issues": issues, "structure": structure_report(elements, translated)}


if __name__ == "__main__":
    import argparse
    import sys

    from .client import BodhanClient

    ap = argparse.ArgumentParser(description="Run the translation stage on an assembled lesson.")
    ap.add_argument("--out", default="out")
    ap.add_argument("--langs", required=True, help="comma separated, for example hi,ta,bn")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    client = BodhanClient()
    failed = False
    for code in [c.strip() for c in args.langs.split(",") if c.strip()]:
        try:
            rep = run_translate(client, Path(args.out), code, args.workers)
            print(code, "structure:", rep["structure"], "| api groups:", rep["api_groups"], "| issues:", len(rep["translation_issues"]))
            for item in rep["translation_issues"]:
                print("   page", item["page"], item["type"], item["issues"])
        except Exception as exc:   # one language must not stop the others
            failed = True
            print(code, "FAILED:", exc)
    print(json.dumps(client.costs.summary(), indent=2))
    sys.exit(1 if failed else 0)