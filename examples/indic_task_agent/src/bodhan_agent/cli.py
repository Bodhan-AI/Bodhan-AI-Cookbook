"""python -m bodhan_agent --task "..." --files path/ --out ./out [--max-steps 30] [--max-cost-inr 20]"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from bodhan_mcp.core import config
from bodhan_mcp.core.errors import BodhanError

from . import intake, planner, reporters, verifier
from .executor import Ledger, _StepFailed, price_step, run_plan
from .planner import Plan
from .trace import Trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bodhan_agent", description="Budget-aware Indic task agent on Bodhan models.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", default="")
    parser.add_argument("--out", default="./out")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost-inr", type=float, default=20.0)
    parser.add_argument("--ui", action="store_true", help="also start the read-only trace viewer (stretch)")
    args = parser.parse_args(argv)

    config.load_dotenv()
    out = Path(args.out)
    try:
        out.mkdir(parents=True, exist_ok=True)
        (out / "artifacts").mkdir(exist_ok=True)
    except OSError as exc:
        print(f"cannot write --out {out}: {exc}", file=sys.stderr)
        return 2
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
        plan_obj = planner.plan(args.task, rows, budgets, trace)
        understanding = plan_obj.understanding
        assumptions = [a.model_dump() for a in plan_obj.assumptions]
        trace.emit("plan", attempt=1, understanding=understanding, feasible=plan_obj.feasible, steps=[s.model_dump() for s in plan_obj.steps], assumptions=assumptions, refusal=plan_obj.refusal)

        if not plan_obj.feasible:
            raise BodhanError("UNSUPPORTED_LANGUAGE", plan_obj.refusal or "The task cannot be done with the available models.", hint="No model was called and no output was produced.")

        estimate = sum(price_step(s, lookup) for s in plan_obj.steps) * 1.15
        trace.emit("estimate", estimated_cost_inr=round(estimate, 4), max_cost_inr=args.max_cost_inr)
        if estimate > args.max_cost_inr:
            replans += 1
            note = f"This plan costs about Rs {estimate:.2f}; Rs {args.max_cost_inr:.2f} is available. Reduce scope, choosing cheaper or fewer steps."
            trace.emit("replan", reason="budget", replans_used=replans, note=note)
            plan_obj = planner.plan(args.task + "\n\n" + note, rows, budgets, trace, attempt=2)
            assumptions = [a.model_dump() for a in plan_obj.assumptions]
            estimate = sum(price_step(s, lookup) for s in plan_obj.steps) * 1.15
            if not plan_obj.feasible:
                raise BodhanError("UNSUPPORTED_LANGUAGE", plan_obj.refusal or "The task cannot be done with the available models.")
            if estimate > args.max_cost_inr:
                raise BodhanError("BUDGET_EXCEEDED", f"The cheapest plan found costs about Rs {estimate:.2f}; the budget was Rs {args.max_cost_inr:.2f}.", hint="Raise --max-cost-inr. Nothing was purchased.")

        run_plan(plan_obj, out_dir=out / "artifacts", ledger=ledger, trace=trace, inventory=lookup, records=records)

        if _wants_srt(args.task):
            records, _ = _collect(records, out)
            srt = _build_srt(trace)
            if srt:
                srt_note = True
                notes.append(f"Subtitle file written with interpolated timing: {srt.name}")
            else:
                notes.append("An SRT was requested but no transcript timings reached the final plan; see steps.")
        records, artifacts = _collect(records, out)
        checks = _verify(artifacts, records, out, ledger, trace)
        status = verifier.derive_status(checks, bool(artifacts))
        if status != "completed":
            errors.append({"code": "VERIFICATION_FAILED", "message": "One or more checks did not pass.", "hint": None})
    except _StepFailed as exc:
        errors.append(exc.record.error or {"code": "INTERNAL", "message": "step failed"})
        records, artifacts = _collect(records, out)
        checks = _verify(artifacts, records, out, ledger, trace) if artifacts else []
        status = verifier.derive_status(checks, bool(artifacts))
    except BodhanError as exc:
        errors.append(exc.payload()["error"])
        trace.emit("error", **exc.payload()["error"])
        records, artifacts = _collect(records, out)
        checks = _verify(artifacts, records, out, ledger, trace) if artifacts else []
        status = "partial" if (artifacts and exc.code != "UNSUPPORTED_LANGUAGE") else "failed"
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
    if args.ui:
        try:
            from .webui import serve

            serve(str(out), port=8766, block=True)
        except Exception as exc:  # noqa: BLE001
            print(f"UI unavailable: {exc}", file=sys.stderr)
    print(json.dumps({"status": status, "cost_inr": spent, "out": str(out)}))
    return 0


def _collect(records: list[Any], out: Path) -> tuple[list[Any], list[dict[str, Any]]]:
    artifacts_dir = out / "artifacts"
    items: list[dict[str, Any]] = []
    if artifacts_dir.is_dir():
        for path in sorted(artifacts_dir.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            kind = "audio" if suffix in (".wav", ".mp3") else "translation" if suffix == ".txt" else "document" if suffix in (".md", ".html") else "file"
            detail = f"{path.stat().st_size / 1024:.0f} KB"
            if kind == "audio":
                try:
                    from bodhan_mcp.core import media

                    detail = f"{media.ffprobe_duration(str(path)):.1f}s"
                except Exception:  # noqa: BLE001
                    detail = "duration unavailable"
            items.append({"name": path.name, "path": str(path), "kind": kind, "bytes": path.stat().st_size, "sha256": verifier.hash_file(path), "detail": detail})
    return records, items


def _verify(artifacts: list[dict[str, Any]], records: list[Any], out: Path, ledger: Ledger, trace: Trace) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    by_path = {str(r.result.get("text_path") or r.result.get("markdown_path") or r.result.get("audio_path")): r for r in records if r.ok}
    for art in artifacts:
        path = Path(art["path"])
        if art["name"].endswith(".srt"):
            checks += verifier.verify_srt(path, trace)
            continue
        if art["kind"] == "file":
            continue
        record = by_path.get(art["path"])
        expected = record.args.get("expected_chars") if record else None
        kind = art["kind"]
        checks += verifier.verify_artifact(path, kind, expected_chars=expected, trace=trace)
        if kind == "audio" and record is not None:
            checks += verifier.l2_audio(path, Path(record.args.get("text_path", out / "artifacts" / "missing.txt")), ledger, trace)
    return checks


def _wants_srt(task: str) -> bool:
    lowered = task.lower()
    return any(word in lowered for word in ("srt", "subtitle", "subtitles", "caption", "captions"))


def _build_srt(trace: Trace) -> Path | None:
    """Find a transcript and the translation made from it, then write interpolated cues next to them."""
    transcript = None
    for event in _events(trace.path):
        pass
    records = _load_records(trace.path)
    transcripts = [r for r in records if r.get("tool") == "transcribe_audio" and r.get("ok")]
    translations = [r for r in records if r.get("tool") == "translate_text" and r.get("ok")]
    for t in translations:
        for tr in transcripts:
            if t["args"].get("source_path") == tr["result"].get("text_path"):
                transcript = tr
                translation = t
                break
        if transcript:
            break
    if not transcript:
        return None
    from bodhan_agent import srt as srt_mod

    chunks = transcript["result"].get("chunks") or []
    text = Path(translation["result"]["text_path"]).read_text(encoding="utf-8")
    cues = _allocate(text, chunks)
    path = Path(translation["result"]["text_path"]).with_suffix(".srt")
    srt_mod.write_srt(cues, str(path))
    return path


def _events(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _load_records(path: Path) -> list[dict[str, Any]]:
    """Reconstruct minimal step records from the trace's tool_result/call pairs."""
    calls: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for event in _events(path):
        if event.get("type") == "tool_call":
            calls[event["step"]] = event
        elif event.get("type") == "tool_result":
            call = calls.get(event["step"], {})
            out.append({"step": event["step"], "tool": event.get("tool"), "ok": event.get("ok"), "args": call.get("args", {}), "summary": event.get("summary", {})})
    return out


def _allocate(text: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spread the translated text across the transcription windows by character weight."""
    if not chunks:
        return [{"start_s": 0.0, "end_s": max(len(text) / 13.5, 1.0), "text": text.strip()}] if text.strip() else []
    weights = [max(c.get("chars", 1), 1) for c in chunks]
    total = sum(weights)
    words = text.split()
    cues = []
    cursor = 0
    for chunk, weight in zip(chunks, weights):
        take = max(1, round(len(words) * weight / total)) if chunk is not chunks[-1] else len(words) - cursor
        piece = " ".join(words[cursor : cursor + take]).strip()
        cursor += take
        if piece:
            cues.append({"start_s": chunk.get("start_s", 0.0), "end_s": chunk.get("end_s", 0.0), "text": piece})
    return cues


def _copy_demo_media() -> None:  # pragma: no cover - convenience only
    media_root = Path(__import__("os").environ.get("BODHAN_DEMO_MEDIA", ""))
    if media_root.is_dir():
        shutil.copytree(media_root, Path("demo_tasks/media"), dirs_exist_ok=True)
