"""Command line entry point.

    python -m bodhan_agent --task "..." --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]
    python -m bodhan_agent --resume --out ./out          # continue an interrupted run
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import signal
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import BodhanKeys, load_env_file
from bodhan_mcp.runtime import CostLedger, ResultCache, RunContext

from . import state as checkpoint
from .llm import LLMConfigError, PlannerLLM
from .loop import MAX_TOOL_RESULT_CHARS, Agent, Outcome, Settings, expand_inputs
from .report import write_outputs
from .trace import TraceWriter

OUR_OUTPUTS = ("result.md", "report.json", "trace.jsonl", "artifacts", checkpoint.STATE_DIR)


class UsageError(Exception):
    """Bad command line: reported on stderr with exit code 2, before anything is changed on disk."""


def _number(kind, minimum, inclusive=True):
    def parse(text: str):
        try:
            value = kind(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{text!r} is not a valid {kind.__name__}") from exc
        if value != value or value in (float("inf"), float("-inf")) or (value < minimum if inclusive else value <= minimum):
            raise argparse.ArgumentTypeError(f"must be {'>=' if inclusive else '>'} {minimum}")
        return value

    return parse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m bodhan_agent", description="Run an Indian-language media task with Bodhan tools.")
    p.add_argument("--task", help="The task in plain language (required unless --resume).")
    p.add_argument("--files", nargs="*", default=[], help="Input files and/or folders.")
    p.add_argument("--out", required=True, help="Output folder (result.md, artifacts/, trace.jsonl, report.json). One folder per run.")
    p.add_argument("--max-steps", type=_number(int, 1), default=30, help="Maximum planner calls, plan and finish included (default 30).")
    p.add_argument("--max-cost-inr", type=_number(float, 0), default=20.0, help="Maximum Bodhan spend in rupees (default 20).")
    p.add_argument("--max-seconds", type=_number(float, 0, inclusive=False), default=600.0, help="Wall-clock limit for the whole run (default 600).")
    p.add_argument("--call-timeout", type=_number(float, 0, inclusive=False), default=60.0, help="Per-request timeout for Bodhan calls (default 60 s).")
    p.add_argument("--llm-timeout", type=_number(float, 0, inclusive=False), default=120.0, help="Per-request timeout for planner calls (default 120 s).")
    p.add_argument("--concurrency", type=_number(int, 1), default=4, help="Parallel requests per Bodhan model (default 4; the API allows 10).")
    p.add_argument("--cache-dir", default="~/.cache/bodhan_tool_agent", help="Shared result cache, so reruns do not repeat paid calls.")
    p.add_argument("--no-cache", action="store_true", help="Do not use the shared cache (the run folder still keeps its own, for --resume).")
    p.add_argument("--env-file", default=None, help="Optional .env file with the Bodhan/LLM variables.")
    p.add_argument("--llm-input-inr-per-1m", type=_number(float, 0), default=None, help="Optional planner price (INR per 1M prompt tokens) to count planner spend in the budget.")
    p.add_argument("--llm-output-inr-per-1m", type=_number(float, 0), default=None, help="Optional planner price (INR per 1M completion tokens).")
    p.add_argument("--resume", action="store_true", help="Continue the interrupted run in --out (same task, inputs and limits).")
    args = p.parse_args(argv)
    if not args.resume and not args.task:
        p.error("--task is required (unless --resume)")
    return args


def check_inputs_vs_out(raw: list[str], out: Path) -> None:
    """An input inside the output folder would be archived or listed as output; refuse it
    before anything on disk changes. A folder that *contains* the output folder is fine:
    the output subtree is excluded from its file list."""
    for r in raw:
        for part in [x for x in r.split(",") if x.strip()] if "," in r and not Path(r).exists() else [r]:
            p = Path(part.strip()).expanduser()
            if p.exists() and (p.resolve() == out or p.resolve().is_relative_to(out)):
                raise UsageError(f"Input {part} is inside the output folder {out}. Use a different --out, or copy the input elsewhere.")


def prepare_out(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stale = [out / n for n in OUR_OUTPUTS if (out / n).exists()]
    if stale:  # keep earlier results instead of deleting them, but never mix them with this run's artifacts
        keep = out / ".previous_runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        keep.mkdir(parents=True, exist_ok=True)
        for s in stale:
            shutil.move(str(s), str(keep / s.name))
    (out / "artifacts").mkdir()


async def run(args: argparse.Namespace, *, transport=None, llm: PlannerLLM | None = None) -> dict:
    """Run (or resume) one task. `transport`/`llm` are injection points for the offline tests only."""
    t0 = time.monotonic()  # the run deadline counts from here, so setup and preflight are inside it
    load_env_file(args.env_file)
    out = Path(args.out).resolve()
    resume_state = None
    if getattr(args, "resume", False):
        resume_state = checkpoint.load(out)
        if args.task and args.task != resume_state["task"]:
            raise checkpoint.ResumeError("--task differs from the task of the run being resumed.")
        task = resume_state["task"]
        files = checkpoint.check_inputs(resume_state)
        missing = resume_state.get("missing", [])
        roots = [Path(r) for r in resume_state.get("roots", [])]
        settings = Settings(**resume_state["settings"])
        deadline = t0 + max(0.0, settings.max_seconds - float(resume_state.get("elapsed_s", 0.0)))
    else:
        task = args.task
        check_inputs_vs_out(args.files, out)
        try:
            files, missing, roots = expand_inputs(args.files, exclude=out)
        except OSError as exc:  # e.g. an unreadable folder: a usage problem, reported before anything changes
            raise UsageError(f"Cannot read the input files: {exc}") from exc
        settings = Settings(max_steps=args.max_steps, max_cost_inr=args.max_cost_inr, max_seconds=args.max_seconds,
                            llm_input_inr_per_1m=args.llm_input_inr_per_1m, llm_output_inr_per_1m=args.llm_output_inr_per_1m)
        deadline = t0 + settings.max_seconds
        try:
            prepare_out(out)
        except OSError as exc:
            raise UsageError(f"Cannot create the output folder {out}: {exc}. Pass a writable --out.") from exc
    started_at = datetime.now(UTC)
    trace = TraceWriter(out / "trace.jsonl", append=bool(resume_state))
    ledger = CostLedger(max_inr=settings.max_cost_inr)
    notes: list[str] = []
    client = None
    try:
        cache = ResultCache(None if args.no_cache else Path(args.cache_dir).expanduser(), local=checkpoint.state_dir(out) / "cache")
        if cache.warning:
            notes.append(cache.warning)
        client = BodhanClient(BodhanKeys.from_env(), ledger, cache, trace.tool_event, timeout=args.call_timeout, concurrency=args.concurrency,
                              transport=transport, deadline=deadline)
        ctx = RunContext(artifacts_dir=out / "artifacts", ledger=ledger, cache=cache, client=client, read_roots=roots, trace=trace.tool_event,
                         max_result_chars=MAX_TOOL_RESULT_CHARS - 600)  # room for the envelope (ok, cost, budget) added after the tool
        llm_error = None
        if llm is None:
            try:
                llm = PlannerLLM(timeout=args.llm_timeout)
            except LLMConfigError as exc:
                llm_error = str(exc)
        agent = Agent(task, ctx, trace, settings, llm, files, missing, llm_error, deadline=deadline, out=out,
                      resume_state=resume_state, notes=notes)
        agent.roots = roots
        agent.started_at = started_at
    except Exception as exc:  # setup failed: no paid work happened; still write an honest report
        trace.write({"type": "setup_error", "error": repr(exc), "traceback": traceback.format_exc()[-3000:]})
        ctx = RunContext(artifacts_dir=out / "artifacts", ledger=ledger, cache=ResultCache(None), client=None, read_roots=roots)
        agent = Agent(task, ctx, trace, settings, None, files, missing, deadline=deadline, out=out, notes=notes)
        agent.outcome = Outcome("failed", "setup_error", f"The run could not start: {type(exc).__name__}: {exc}. No model was called.")
        return _finish(agent, ledger, trace, out, started_at, t0)
    try:
        await agent.run()
    except (KeyboardInterrupt, asyncio.CancelledError):  # Ctrl-C / SIGTERM: keep the run resumable, leave an honest report
        trace.write({"type": "interrupted", "step": agent.steps})
        agent._save()  # checkpoint plus the provisional report that says how to resume
        raise
    except Exception as exc:  # never leave a run without a report; same output rule as any other stop
        trace.write({"type": "internal_error", "error": repr(exc), "traceback": traceback.format_exc()[-3000:]})
        agent.forced_outcome("internal_error", f"The agent hit an internal error: {type(exc).__name__}: {exc}.")
        agent._save()
    finally:
        if client is not None:
            await client.aclose()
    return _finish(agent, ledger, trace, out, started_at, t0)


def _finish(agent: Agent, ledger: CostLedger, trace: TraceWriter, out: Path, started_at: datetime, t0: float) -> dict:
    elapsed = agent.elapsed_before + (time.monotonic() - t0)
    trace.write({"type": "run_end", "status": agent.outcome.status, "elapsed_s": round(elapsed, 2), "cost": ledger.summary()})
    return write_outputs(agent, out, started_at, elapsed)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(signal, "SIGTERM"):  # `docker stop`, a CI timeout: handled like Ctrl-C (report + resumable checkpoint)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        report = asyncio.run(run(args))
    except (UsageError, checkpoint.ResumeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print(f"interrupted: report and checkpoint are in {Path(args.out).resolve()}; continue with --resume", file=sys.stderr)
        sys.exit(130)
    c = report["cost"]
    print(f"status={report['status']} stop={report['stop_reason']} steps={report['steps']['used']} "
          f"bodhan_inr={c['total_counted_inr']} elapsed_s={report['elapsed_s']} out={Path(args.out).resolve()}")
    sys.exit(0)
