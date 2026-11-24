"""Record real Bodhan results once, so stub-mode evals can replay them for free.

    python -m evals.record_cassettes --dry-run            # show what would be sent and the estimated cost
    python -m evals.record_cassettes                      # record everything missing (spends credit)
    python -m evals.record_cassettes --only marathi_worksheet_keep_table --force

Calls the real tool functions directly (no LLM involved). Each case's "record" block in golden.json lists what to send.
Entries already in a cassette are skipped (unless --force), identical calls are made once across cases, every paid call is
written to outputs/credit_ledger.json, and nothing runs when less than Rs 2 of credit is left.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import wave
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from bodhan_core import ledger  # noqa: E402
from bodhan_mcp.tools.ocr import ocr_document  # noqa: E402
from bodhan_mcp.tools.transcribe import transcribe  # noqa: E402
from bodhan_mcp.tools.translate import translate  # noqa: E402
from evals import cassettes  # noqa: E402
from evals.fixtures import build_inputs  # noqa: E402
from evals.harness import ROOT_DEFAULT_LEDGER, load_golden  # noqa: E402

EST_TRANSLATE_INR = 0.005  # generous per call; real cost is a few paise at most for short text
OCR_INR_PER_PAGE = 0.20
TRANSCRIBE_INR_PER_MIN = 0.10
MODEL = {"translate_text": "indic-translate", "ocr_document": "indic-ocr", "transcribe_audio": "indic-transcribe"}


def _full(inline: str | None, path: str | None) -> str:
    return Path(path).read_text(encoding="utf-8-sig") if path else (inline or "")


def _sha(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError):
        return 10.0


def plan_case(case: dict, fixtures: dict, work: Path, existing: dict, force: bool) -> list[dict]:
    """The calls to make for this case, in order, each with an estimate. Skips entries already recorded."""
    inputs = {Path(p).name: Path(p) for p in build_inputs(case, fixtures, work / "inputs")}
    todo: list[dict] = []
    rec = case.get("record", {})
    for item in rec.get("ocr_document", []):
        f = inputs[item["input"]]
        if force or not cassettes.find_file(existing, "ocr_document", f.name):
            todo.append({"tool": "ocr_document", "file": f, "key": ("ocr", _sha(f)), "est": OCR_INR_PER_PAGE})
    for item in rec.get("transcribe_audio", []):
        f = inputs[item["input"]]
        if force or not cassettes.find_file(existing, "transcribe_audio", f.name):
            todo.append({"tool": "transcribe_audio", "file": f, "key": ("asr", _sha(f)), "est": _wav_seconds(f) / 60 * TRANSCRIBE_INR_PER_MIN})
    for item in rec.get("translate_text", []):
        src = item.get("text") or ""
        if "input" in item:
            src = inputs[item["input"]].read_text(encoding="utf-8")
        elif item.get("source") == "stub_ocr":
            src = "\n".join(case["stubs"]["ocr_document"]["lines"])
        elif item.get("source") == "ocr":
            src = None  # filled from this case's OCR recording at run time
        if src is not None and not force and cassettes.find_translation(existing, src, item["target"]):
            continue
        todo.append({"tool": "translate_text", "source": src, "target": item["target"], "key": ("tr", src and cassettes.norm(src), item["target"]), "est": EST_TRANSLATE_INR})
    return todo


async def execute(call: dict, cassette: dict, cache: dict, work: Path) -> float:
    """Make one real call (or reuse an identical one from this run). Returns the rupees actually spent."""
    tool, key = call["tool"], call["key"]
    cost = 0.0
    if tool == "translate_text":
        src = call["source"] if call["source"] is not None else cassette["ocr_document"][-1]["markdown"]
        key = ("tr", cassettes.norm(src), call["target"])
        if key not in cache:
            res = await translate(src, call["target"])
            if not res.ok:
                raise SystemExit(f"translate failed: {res.error.code}: {res.error.message}")
            cache[key] = ({"source": src, "target": call["target"], "translation": _full(res.translation, res.translation_path)}, res.cost_inr)
        entry, paid = cache[key]
        cost, cache[key] = (paid, (entry, 0.0))  # a reused result costs nothing
        cassette.setdefault(tool, []).append(entry)
    elif tool == "ocr_document":
        if key not in cache:
            res = await ocr_document(str(call["file"]))
            if not res.ok:
                raise SystemExit(f"ocr failed: {res.error.code}: {res.error.message}")
            cache[key] = ({"markdown": _full(res.markdown, res.markdown_path), "page_count": res.page_count}, res.cost_inr)
        body, paid = cache[key]
        cost, cache[key] = (paid, (body, 0.0))
        cassette.setdefault(tool, []).append({"file": call["file"].name, **body})
    else:
        if key not in cache:
            res = await transcribe(str(call["file"]))
            if not res.ok:
                raise SystemExit(f"transcribe failed: {res.error.code}: {res.error.message}")
            segs = [s.model_dump() for s in res.segments] or (json.loads(Path(res.segments_path).read_text(encoding="utf-8")) if res.segments_path else [])
            cache[key] = ({"text": _full(res.text, res.text_path), "duration_s": res.duration_s, "segments": segs}, res.cost_inr)
        body, paid = cache[key]
        cost, cache[key] = (paid, (body, 0.0))
        cassette.setdefault(tool, []).append({"file": call["file"].name, **body})
    return cost


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="evals.record_cassettes")
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-record entries that already exist")
    args = ap.parse_args()

    golden = load_golden()
    only = args.only.split(",") if args.only else None
    work = Path(tempfile.mkdtemp(prefix="bodhan-cassettes-"))
    os.environ["BODHAN_WORK_DIR"] = str(work / "work")
    plans = {}
    for case in golden["cases"]:
        if "record" not in case or (only and case["id"] not in only):
            continue
        plans[case["id"]] = plan_case(case, golden["fixtures"], work / case["id"], cassettes.load(case["id"]) or {}, args.force)

    seen, estimate = set(), 0.0  # an identical OCR / transcription is paid once per run, so count it once
    for cid, calls in plans.items():
        for c in calls:
            dup = c["tool"] != "translate_text" and c["key"] in seen
            seen.add(c["key"])
            cost = 0.0 if dup else c["est"]
            estimate += cost
            what = c["file"].name if "file" in c else f"-> {c['target']}"
            print(f"  {cid:34} {c['tool']:17} {what:14} est Rs {cost:.3f}" + ("  (shared with an earlier case)" if dup else ""))
    left = ledger.remaining(ROOT_DEFAULT_LEDGER)
    print(f"\nestimated Rs {estimate:.3f}; credit left Rs {left:.2f}")
    if not any(plans.values()):
        print("nothing to record: every cassette entry already exists (use --force to re-record)")
        return 0
    if args.dry_run:
        return 0
    if left < ledger.LOW_CREDIT_INR or estimate > left:
        print("refusing: not enough credit left")
        return 2

    async def go() -> None:
        cache: dict = {}
        for cid, calls in plans.items():
            if not calls:
                continue
            cassette = cassettes.load(cid) or {}
            spent = 0.0
            for c in calls:
                spent += await execute(c, cassette, cache, work)
            cassettes.save(cid, cassette)
            if spent > 0:
                print(f"[recorded] {cid}: Rs {spent:.4f}; credit left Rs {ledger.record(f'record cassette: {cid}', 'indic-* (record)', round(spent, 6), ROOT_DEFAULT_LEDGER):.2f}")
            else:
                print(f"[recorded] {cid}: reused this run's identical calls (Rs 0)")

    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
