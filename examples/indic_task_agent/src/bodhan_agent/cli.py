"""python -m bodhan_agent --task "..." --files path/ --out ./out [--max-steps 30] [--max-cost-inr 20]"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from bodhan_mcp.core import config
from bodhan_mcp.core.errors import BodhanError

from . import intake, planner, reporters, verifier
from .executor import Ledger, _StepFailed, price_step, run_plan, set_resume, stale_markers
from .planner import Plan
from .trace import Trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bodhan_agent", description="Budget-aware Indic task agent on Bodhan models.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", default="")
    parser.add_argument("--out", default="./out")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost-inr", type=float, default=20.0)
    parser.add_argument("--resume", action="store_true", help="continue an interrupted run in --out: reuse its plan and finished steps")
    args = parser.parse_args(argv)

    config.load_dotenv()
    out = Path(args.out)
    try:
        out.mkdir(parents=True, exist_ok=True)
        (out / "artifacts").mkdir(exist_ok=True)
    except OSError as exc:
        print(f"cannot write --out {out}: {exc}", file=sys.stderr)
        return 2
    set_resume(args.resume)
    if not args.resume:
        for name in ("plan.json", "state.json"):
            (out / name).unlink(missing_ok=True)
    started = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    trace = Trace(out / "trace.jsonl")
    records: list[Any] = []
    checks: list[dict[str, str]] = []
    errors: list[dict[str, Any]] = []
    assumptions: list[dict[str, str]] = []
    understanding = ""
    status = "failed"
    replans = 0
    notes: list[str] = []
    ledger = Ledger(args.max_cost_inr, args.max_steps)
    plan_obj: Plan | None = None
    srt_note = False
    for stale in stale_markers(out):
        notes.append(f"An interrupted earlier run left step {stale.get('step')} ({stale.get('tool')}) unfinished; that call may already have been billed.")

    try:
        files = intake.dedupe(intake.resolve_files(args.files))
        rows, lookup = intake.inventory(files) if files else ([], {})
        trace.emit("intake", files=[str(f) for f in files], inventory=rows, cost_inr=0, hashes=intake.file_hashes(files) and None)
        if not files:
            raise BodhanError("MISSING_FILE", f"No input files were found at '{args.files}'.", hint="Pass --files with a file, a directory, or a comma-separated list.")
        usable = [r for r in rows if not r["warnings"] or "missing_file" not in r["warnings"]]
        if not usable:
            raise BodhanError("MISSING_FILE", f"None of the paths in '{args.files}' exist or can be read.", hint="Check the paths; nothing was called.")

        budgets = {"max_steps": args.max_steps, "max_cost_inr": args.max_cost_inr}
        saved = out / "plan.json"
        if args.resume and saved.is_file():
            plan_obj = Plan.model_validate_json(saved.read_text(encoding="utf-8"))
            trace.emit("resume", plan_steps=len(plan_obj.steps), note="plan reused; finished steps are skipped")
        else:
            plan_obj = planner.plan(args.task, rows, budgets, trace)
            if plan_obj.feasible:
                saved.write_text(plan_obj.model_dump_json(by_alias=True), encoding="utf-8")
        understanding = plan_obj.understanding
        assumptions = [a.model_dump() for a in plan_obj.assumptions]
        trace.emit("plan", attempt=1, understanding=understanding, feasible=plan_obj.feasible, steps=[s.model_dump() for s in plan_obj.steps], assumptions=assumptions, refusal=plan_obj.refusal)

        if not plan_obj.feasible:
            raise BodhanError(plan_obj.refusal_code or "UNSUPPORTED_TASK", plan_obj.refusal or "The task cannot be done with the available models.", hint="No model was called and no output was produced.")

        try:
            estimate = sum(price_step(s, lookup) for s in plan_obj.steps) * 1.15
        except BodhanError as exc:
            if exc.code != "LLM_INVALID_PLAN":
                raise
            replans += 1
            trace.emit("replan", reason="unpriceable_plan", replans_used=replans, note=exc.message)
            plan_obj = planner.plan(args.task + "\n\nYour previous plan could not be priced: " + exc.message + " Return the corrected plan.", rows, budgets, trace, attempt=2)
            if not plan_obj.feasible:
                raise BodhanError(plan_obj.refusal_code or "UNSUPPORTED_TASK", plan_obj.refusal or "The task cannot be done with the available models.") from None
            assumptions = [a.model_dump() for a in plan_obj.assumptions]
            understanding = plan_obj.understanding
            estimate = sum(price_step(s, lookup) for s in plan_obj.steps) * 1.15
            (out / "plan.json").write_text(plan_obj.model_dump_json(by_alias=True), encoding="utf-8")
        trace.emit("estimate", estimated_cost_inr=round(estimate, 4), max_cost_inr=args.max_cost_inr)
        if estimate > args.max_cost_inr:
            replans += 1
            note = f"This plan costs about Rs {estimate:.2f}; Rs {args.max_cost_inr:.2f} is available. Reduce scope, choosing cheaper or fewer steps."
            trace.emit("replan", reason="budget", replans_used=replans, note=note)
            plan_obj = planner.plan(args.task + "\n\n" + note, rows, budgets, trace, attempt=2)
            assumptions = [a.model_dump() for a in plan_obj.assumptions]
            estimate = sum(price_step(s, lookup) for s in plan_obj.steps) * 1.15
            if not plan_obj.feasible:
                raise BodhanError(plan_obj.refusal_code or "UNSUPPORTED_TASK", plan_obj.refusal or "The task cannot be done with the available models.")
            if estimate > args.max_cost_inr:
                raise BodhanError("BUDGET_EXCEEDED", f"The cheapest plan found costs about Rs {estimate:.2f}; the budget was Rs {args.max_cost_inr:.2f}.", hint="Raise --max-cost-inr. Nothing was purchased.")

        run_plan(plan_obj, out_dir=out / "artifacts", ledger=ledger, trace=trace, inventory=lookup, records=records)

        if _wants_srt(args.task):
            srts = _build_srt(records, out)
            if srts:
                srt_note = True
                notes += [f"Subtitle file written with interpolated timing: {p.name}" for p in srts]
            else:
                notes.append("An SRT was requested but no transcript with window timings reached the final plan; see steps.")
        records, artifacts = _collect(records, out)
        checks = _verify(artifacts, records, out, ledger, trace, args.task)
        status = verifier.derive_status(checks, bool(artifacts))
        warned = [c for c in checks if c["result"] == "warning"]
        if warned:
            notes.append(f"{len(warned)} check(s) raised a warning for human review (not counted as failures): " + ", ".join(sorted({c["check"] for c in warned})))
        gaps = _missing_final_outputs(plan_obj, records)
        if gaps:
            status = "partial" if status == "completed" else status
            errors.append({"code": "REQUIRED_OUTPUT_MISSING", "message": "The plan required " + ", ".join(gaps) + " but no file exists for it.", "hint": "That step was skipped or failed; see the steps above."})
        if status != "completed" and not errors:
            errors.append({"code": "VERIFICATION_FAILED", "message": "One or more checks did not pass.", "hint": None})
    except _StepFailed as exc:
        errors.append(exc.record.error or {"code": "INTERNAL", "message": "step failed"})
        records, artifacts = _collect(records, out)
        checks = _verify(artifacts, records, out, ledger, trace, args.task) if artifacts else []
        status = verifier.derive_status(checks, bool(artifacts))
    except BodhanError as exc:
        errors.append(exc.payload()["error"])
        trace.emit("error", **exc.payload()["error"])
        records, artifacts = _collect(records, out)
        checks = _verify(artifacts, records, out, ledger, trace, args.task) if artifacts else []
        status = "partial" if (artifacts and exc.code not in ("UNSUPPORTED_LANGUAGE", "BUDGET_EXCEEDED", "UNSUPPORTED_TASK", "MISSING_FILE")) else "failed"
    except Exception as exc:  # noqa: BLE001 - a complete run folder beats a traceback
        errors.append({"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}", "hint": None})
        trace.emit("error", code="INTERNAL", message=str(exc))
        status = "failed"

    duration = time.time() - started
    cache_hits = sum(1 for r in records if getattr(r, "cache_hit", False))
    spent = round(ledger.spent, 4)
    reporters.write_report(
        out / "report.json",
        task=args.task,
        status=status,
        started_at=started_at,
        duration_s=duration,
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
        cost_inr=spent,
        reserved_inr=ledger.reserved,
        assumptions=assumptions,
        records=records,
        checks=checks,
        artifacts=artifacts,
        errors=errors,
        replans=replans,
        cache_hits=cache_hits,
        notes=notes,
        verification_inr=max(spent - sum(r.cost_inr for r in records), 0.0),
    )
    reporters.write_result_md(
        out / "result.md",
        task=args.task,
        status=status,
        understanding=understanding,
        records=records,
        assumptions=assumptions,
        checks=checks,
        artifacts=artifacts,
        cost_inr=spent,
        max_cost_inr=args.max_cost_inr,
        duration_s=duration,
        errors=errors,
        notes=notes,
        srt_note=srt_note,
    )
    trace.emit("stop", status=status, cost_inr=spent, duration_s=round(duration, 1))
    print(json.dumps({"status": status, "cost_inr": spent, "out": str(out)}))
    return 0


def _collect(records: list[Any], out: Path) -> tuple[list[Any], list[dict[str, Any]]]:
    artifacts_dir = out / "artifacts"
    items: list[dict[str, Any]] = []
    made_by = {}
    for r in records:
        for key in ("text_path", "markdown_path", "audio_path"):
            if r.ok and r.result.get(key):
                made_by[Path(r.result[key]).name] = _KIND_BY_TOOL.get(r.tool)
    if artifacts_dir.is_dir():
        for path in sorted(artifacts_dir.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            kind = made_by.get(path.name) or ("audio" if suffix in (".wav", ".mp3") else "translation" if suffix == ".txt" else "document" if suffix in (".md", ".html") else "file")
            detail = f"{path.stat().st_size / 1024:.0f} KB"
            if kind == "audio":
                try:
                    from bodhan_mcp.core import media

                    detail = f"{media.ffprobe_duration(str(path)):.1f}s"
                except Exception:  # noqa: BLE001
                    detail = "duration unavailable"
            items.append({"name": path.name, "path": str(path), "kind": kind, "bytes": path.stat().st_size, "sha256": verifier.hash_file(path), "detail": detail})
    return records, items


_KIND_BY_TOOL = {"translate_text": "translation", "transcribe_audio": "transcript", "write_text": "text", "ocr_document": "document", "speak_text": "audio"}


def _verify(artifacts: list[dict[str, Any]], records: list[Any], out: Path, ledger: Ledger, trace: Trace, task: str = "") -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    by_path = {str(r.result.get("text_path") or r.result.get("markdown_path") or r.result.get("audio_path")): r for r in records if r.ok}
    wanted = verifier.requested_duration(task)
    for art in artifacts:
        path = Path(art["path"])
        if art["name"].endswith(".srt"):
            checks += verifier.verify_srt(path, trace)
            continue
        if art["kind"] == "file":
            continue
        record = by_path.get(art["path"])
        expected = record.args.get("expected_chars") if record else None
        source = record.args.get("source_path") if record else None
        source_path = Path(source) if source else None
        kind = _KIND_BY_TOOL.get(record.tool, art["kind"]) if record else art["kind"]
        checks += verifier.verify_artifact(path, kind, expected_chars=expected, source_path=source_path, trace=trace)
        if kind == "translation" and record is not None and source_path:
            checks += verifier.l2_translation(path, source_path, str(record.args.get("target_language") or ""), ledger, trace)
        if kind == "audio" and record is not None:
            checks += verifier.l2_audio(path, Path(record.args.get("text_path", out / "artifacts" / "missing.txt")), ledger, trace)
            if wanted:
                checks += verifier.verify_duration(path, wanted, trace)
    return checks


def _wants_srt(task: str) -> bool:
    lowered = task.lower()
    return any(word in lowered for word in ("srt", "subtitle", "subtitles", "caption", "captions"))


_PATH_FIELDS = ("text_path", "markdown_path", "audio_path", "blocks_path")


def _missing_final_outputs(plan: Plan, records: list[Any]) -> list[str]:
    """A run is not 'completed' when a file the plan promised does not exist."""
    by_id = {r.id: r for r in records}
    gaps: list[str] = []
    for output in plan.final_outputs:
        record = by_id.get(output.from_step)
        if record is not None and record.result.get("skipped"):
            continue
        paths = [record.result.get(k) for k in _PATH_FIELDS] if record and record.ok else []
        if not any(path and Path(path).is_file() for path in paths):
            gaps.append(f"{output.role} (step {output.from_step})")
    return gaps


def _build_srt(records: list[Any], out: Path) -> list[Path]:
    """Every transcript becomes cues; when a translation was made from it, those cues are in the target language.

    The API returns text without word timings, so text is spread across the
    transcription windows by character weight and split into sentences inside
    each window. That interpolation is stated in result.md.
    """
    from bodhan_agent import srt as srt_mod

    transcripts = [r for r in records if r.tool == "transcribe_audio" and r.ok and r.result.get("chunks")]
    translations = [r for r in records if r.tool == "translate_text" and r.ok]
    written: list[Path] = []
    for transcript in transcripts:
        text_path = transcript.result.get("text_path")
        if not text_path or not Path(text_path).is_file():
            continue
        translation = next((t for t in translations if t.args.get("source_path") == text_path), None)
        source = (translation.result.get("text_path") if translation else None) or text_path
        if not Path(source).is_file():
            continue
        text = Path(source).read_text(encoding="utf-8")
        cues = srt_mod.cues(_allocate(text, transcript.result["chunks"]))
        if not cues:
            continue
        target = Path("artifacts") / f"{Path(text_path).stem}.srt"
        written.append(Path(srt_mod.write_srt(cues, out / target)))
    return written


def _allocate(text: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spread the document across the transcription windows by character weight.

    One cue per window is not enough (a 30 s window holds several sentences), so
    the window text is handed to srt.cues, which splits and re-times it inside
    the window's own start and end.
    """
    if not text.strip():
        return []
    if not chunks:
        return [{"start_s": 0.0, "end_s": max(len(text) / 13.5, 1.0), "text": text.strip()}]
    weights = [max(int(c.get("chars") or 1), 1) for c in chunks]
    total = sum(weights)
    words = text.split()
    cues: list[dict[str, Any]] = []
    cursor = 0
    for i, (chunk, weight) in enumerate(zip(chunks, weights)):
        take = len(words) - cursor if i == len(weights) - 1 else max(1, round(len(words) * weight / total))
        piece = " ".join(words[cursor : cursor + take]).strip()
        cursor += take
        if piece:
            cues.append({"start_s": float(chunk.get("start_s") or 0.0), "end_s": float(chunk.get("end_s") or 0.0), "text": piece})
    return cues
