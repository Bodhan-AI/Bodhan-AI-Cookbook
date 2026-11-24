"""Writes out/result.md and out/report.json from the run state."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bodhan_mcp import __version__
from bodhan_mcp.runtime import Artifact, RunContext
from bodhan_mcp.tools import TOOLS

PAID_TOOLS = {t.name for t in TOOLS if t.paid}

TEXT_KINDS = {"translation", "subtitles", "text", "ocr", "transcript"}
SINGLE_STEP_TARGET_S, MULTI_STEP_TARGET_S = 30, 300


def _rel(out: Path, p: Path | str) -> str:
    p = Path(p)
    try:
        return str(p.resolve().relative_to(out.resolve())).replace("\\", "/")
    except ValueError:
        return str(p)


def _excerpt(path: Path, limit: int = 400) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _count_retries(trace: Path) -> int:
    n = 0
    for e in _events(trace):
        try:
            n += e.get("type") == "api_call" and int(e.get("attempt") or 1) > 1
        except (TypeError, ValueError):
            continue
    return n


CACHED = ("this_run", "previous_run", "in_flight")


def _events(trace: Path) -> list[dict]:
    """The trace's complete events. Lines are decoded one by one, so a tail cut off mid-character (a crash while
    writing Indic text) loses only that line."""
    out = []
    try:
        raw = trace.read_bytes()
    except OSError:
        return out
    for line in raw.splitlines():
        try:
            e = json.loads(line.decode("utf-8"))
        except ValueError:  # includes UnicodeDecodeError: a line being written right now
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _wall(events: list[dict]) -> float | None:
    """Wall-clock seconds covered by these events' [end - latency, end] intervals, overlaps counted once
    (parallel tool calls run at the same time). None if an event has no timestamp."""
    spans = []
    for e in events:
        try:
            end = datetime.fromisoformat(str(e["ts"])).timestamp()
        except (KeyError, ValueError):
            return None
        spans.append((end - float(e.get("latency_s") or 0), end))
    total, cur = 0.0, None
    for a, b in sorted(spans):
        if cur is None or a > cur[1]:
            total += cur[1] - cur[0] if cur else 0.0
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    return total + (cur[1] - cur[0] if cur else 0.0)


def latency_reason(events: list[dict], elapsed: float, target: float) -> str:
    """Why a run missed its latency target, from its own trace: where the wall-clock time went (tool calls, planner
    replies, and the rest), the slowest step, the Bodhan requests behind it and how many attempts rate limits refused.
    Parallel calls are counted once; time no event accounts for (planner retries and their waits, preflight,
    bookkeeping) is reported as such, not attributed to a step."""
    llm = [e for e in events if e.get("type") in ("llm_call", "coverage_check")]  # the coverage check is a planner-model call too
    tools = [e for e in events if e.get("type") == "tool_call"]
    sent = [e for e in events if e.get("type") == "api_call" and e.get("cache") not in CACHED]
    lat = lambda e: float(e.get("latency_s") or 0)  # noqa: E731
    pct = lambda x: round(100 * x / elapsed) if elapsed > 0 else 0  # noqa: E731
    timed = _wall(tools + llm) is not None
    wall = lambda evs: _wall(evs) if timed else sum(map(lat, evs))  # noqa: E731
    share = (lambda x: f"{pct(x)}% of the time ({x:.0f} s)") if timed else (lambda x: f"{x:.0f} s in total")  # noqa: E731
    parts = [f"missed by {elapsed - target:.0f} s ({elapsed:.0f} s against {target:.0f} s)"]
    if tools:
        by_tool: dict[str, float] = {}
        for name in {str(e.get("tool")) for e in tools}:
            by_tool[name] = wall([e for e in tools if str(e.get("tool")) == name])
        top = max(sorted(by_tool), key=by_tool.get)
        slow = max(tools, key=lat)
        head = f"tool calls {share(wall(tools))}"
        if sum(1 for e in tools if str(e.get("tool")) == top) == 1 and slow.get("tool") == top:  # one call holds all of that tool's time
            parts.append(f"{head}, mostly step {slow.get('step')} ({top}, {lat(slow):.0f} s)")
        else:
            parts.append(f"{head}, mostly {top} ({by_tool[top]:.0f} s); slowest step {slow.get('step')} ({slow.get('tool')}, {lat(slow):.0f} s)")
    if llm:
        parts.append(f"planner replies {share(wall(llm))} ({len(llm)} calls)")
    if timed and elapsed > 0:
        rest = elapsed - _wall(tools + llm)
        if rest >= max(1.0, 0.05 * elapsed):
            parts.append(f"{share(rest)} outside tool calls and planner replies (planner retries and their waits, preflight, bookkeeping)")
    done = [e for e in sent if e.get("status") != 429]
    if done:
        models: dict[str, int] = {}
        for e in done:
            models[str(e.get("model"))] = models.get(str(e.get("model")), 0) + 1
        parts.append(f"{len(done)} Bodhan requests (" + ", ".join(f"{m} {n}" for m, n in sorted(models.items(), key=lambda x: -x[1])) + ")")
    retried = sum(1 for e in sent if e.get("status") == 429 and e.get("will_retry", True))
    final = sum(1 for e in sent if e.get("status") == 429 and not e.get("will_retry", True))
    if retried:
        parts.append(f"{retried} attempt{'s' if retried > 1 else ''} refused by rate limits and retried")
    if final:
        parts.append(f"{final} request{'s' if final > 1 else ''} refused by rate limits and not retried")
    return "; ".join(parts)


def budget_reason(ledger: dict[str, Any]) -> str:
    over = ledger["total_counted_inr"] - (ledger["max_inr"] or 0)
    bits = [f"₹{ledger['bodhan_billed_inr']:.4f} billed by Bodhan"]
    if ledger.get("bodhan_uncertain_inr"):
        bits.append(f"₹{ledger['bodhan_uncertain_inr']:.4f} for failed or timed-out attempts that may have been billed (counted to be safe)")
    if ledger.get("llm_inr"):
        bits.append(f"₹{ledger['llm_inr']:.4f} for planner calls")
    return f"over the ₹{ledger['max_inr']} cap by ₹{over:.4f}: " + ", ".join(bits)


def verification_reason(unverified: list[str], failed: list[tuple[str, str]]) -> str | None:
    bits = []
    if unverified:
        bits.append("not checked: " + ", ".join(unverified) + " (no check ran on them; see the tool calls in trace.jsonl)")
    if failed:
        bits.append("failed its check: " + "; ".join(f"{p} ({why})" for p, why in failed))
    return "; ".join(bits) or None


def build_report(agent: Any, out: Path, started_at: datetime, elapsed: float) -> dict[str, Any]:
    ctx: RunContext = agent.ctx
    outcome = agent.outcome
    deliverable_keys = set(outcome.deliverables)
    arts = list(ctx.artifacts.values())
    paid_calls = [t for t in agent.tool_log if t["ok"] and t["cost_inr"]]
    # multi-step = more than one paid model call (free helpers such as read_text or write_text do not count)
    multi = sum(1 for t in agent.tool_log if t["tool"] in PAID_TOOLS) > 1
    target = MULTI_STEP_TARGET_S if multi else SINGLE_STEP_TARGET_S
    ledger = ctx.ledger.summary()
    retries = _count_retries(out / "trace.jsonl")
    verifications = [{"artifact": _rel(out, a.path), "kind": a.kind, **(a.verification or {})} for a in arts if a.verification]
    unverified = [_rel(out, a.path) for a in arts if a.kind in ("translation", "audio", "subtitles") and not a.verification]
    injections = sorted({t["result_summary"].get("injection_warning") for t in agent.tool_log
                         if isinstance(t.get("result_summary"), dict) and t["result_summary"].get("injection_warning")})
    try:
        llm_host = urlparse(os.environ.get("LLM_BASE_URL", "")).hostname
    except ValueError:
        llm_host = None
    within = ctx.ledger.max_inr is None or ctx.ledger.committed <= ctx.ledger.max_inr + 1e-9
    failed = [(_rel(out, a.path), "; ".join(map(str, (a.verification.get("problems") or [])[:2])) or "see its verification record")
              for a in arts if a.kind in ("translation", "audio", "subtitles") and (a.verification or {}).get("status") == "fail"]
    return {
        "status": outcome.status,
        "stop_reason": outcome.stop_reason,
        "planner_status": outcome.planner_status,
        "task": agent.task,
        "inputs": {"files": [str(f) for f in agent.files], "missing": agent.missing},
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "elapsed_s": round(elapsed, 2),
        "steps": {"used": agent.steps, "max": agent.settings.max_steps,
                  "tool_calls": [{k: t.get(k) for k in ("step", "tool", "ok", "latency_s", "cost_inr")} |
                                 {"verification": (t["result_summary"].get("verification") or {}).get("status") if isinstance(t["result_summary"], dict) else None,
                                  "error": (t["result_summary"].get("error") or {}).get("code") if isinstance(t["result_summary"], dict) and t["result_summary"].get("error") else None}
                                 for t in agent.tool_log]},
        "plan": agent.plan,
        "final_answer": outcome.final_answer,
        "assumptions": outcome.assumptions,
        "limitations": outcome.limitations,
        "deliverables": [a.as_dict(out) for a in arts if str(a.path) in deliverable_keys],
        "artifacts": [a.as_dict(out) for a in arts],
        "verification": {"results": verifications, "unverified_translations_or_audio": unverified,
                         "all_checked": not unverified},
        "cost": ledger,
        "llm": {"model": getattr(agent.llm, "model", None), "endpoint_host": llm_host,
                "adaptations": list(getattr(agent.llm, "notes", []) or [])},  # e.g. tool_choice dropped, text protocol used
        "quality_targets": {
            "latency": {"kind": "multi-step" if multi else "single-step", "target_s": target, "actual_s": round(elapsed, 1),
                        "met": elapsed <= target,
                        "note": None if elapsed <= target else latency_reason(_events(out / "trace.jsonl"), elapsed, target)},
            "budget": {"max_inr": ctx.ledger.max_inr, "counted_inr": round(ctx.ledger.committed, 4), "within_budget": within,
                       "note": None if within else budget_reason(ledger),
                       "scope": "Bodhan calls (billed + uncertain retries); LLM only if priced via --llm-*-inr-per-1m"},
            "no_waste": {"cache_hits": ctx.ledger.cache_hits, "bodhan_requests": ctx.ledger.calls, "retried_attempts": retries,
                         "note": "identical (model, input, settings) requests are served from the cache or shared while in flight; "
                                 "the only repeats are retries of a failed or timed-out attempt (rate limit, 5xx, timeout), "
                                 "counted here and traced as api_call events with attempt > 1"},
            "verification": {"all_generated_audio_and_translations_checked": not unverified,
                             "note": verification_reason(unverified, failed)},
            "honest_failure": "unassessed: code cannot judge whether the answer is truthful; see verification results and review the outputs",
        },
        "injection_warnings": injections,
        "paid_tool_calls": len(paid_calls),
        "version": __version__,
    }


def write_outputs(agent: Any, out: Path, started_at: datetime, elapsed: float, provisional: bool = False) -> dict[str, Any]:
    report = build_report(agent, out, started_at, elapsed)
    if provisional:
        report["provisional"] = True  # written while running; the final report replaces it
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "result.md").write_text(render_markdown(report, agent.ctx, out), encoding="utf-8")
    return report


def _ver_line(v: dict | None) -> str:
    if not v:
        return "not verified"
    bits = [v.get("status", "?").upper()]
    if v.get("mean_similarity") is not None:
        bits.append(f"back-transcription similarity {v['mean_similarity']}")
    bt = (v.get("checks") or {}).get("back_translation")
    if bt:
        bits.append(f"back-translation chrF {bt.get('mean_chrf')}")
    if v.get("duration"):
        bits.append(f"duration {v['duration']['actual_s']}s vs target {v['duration']['target_s']}s")
    return ", ".join(bits)


def render_markdown(r: dict[str, Any], ctx: RunContext, out: Path) -> str:
    status_icon = {"completed": "✅", "completed_with_warnings": "⚠️", "partial": "🟡", "failed": "❌", "unsupported": "⛔"}.get(r["status"], "")
    lines = [f"# Task result: {status_icon} {r['status']}", "", f"**Task:** {r['task']}", ""]
    lines += ["## Answer", "", r["final_answer"] or "_No answer._", ""]
    by_path = {str(a.path): a for a in ctx.artifacts.values()}
    if r["deliverables"]:
        lines += ["## Deliverables", ""]
        for d in r["deliverables"]:
            art: Artifact | None = by_path.get(str((out / d["path"]).resolve())) or by_path.get(d["path"])
            lines.append(f"- [{d['path']}]({d['path']}) — {d['kind']}" + (f" ({d['language']})" if d.get("language") else "")
                         + f"; produced by `{d['producer']}` via {d['provenance']}; verification: {_ver_line(d.get('verification'))}")
            for note in d.get("notes") or []:
                lines.append(f"  - {note}")
            if art and d["kind"] in TEXT_KINDS:
                ex = _excerpt(art.path)
                if ex:
                    lines += ["", "  ```text", *["  " + x for x in ex.splitlines()], "  ```", ""]
        lines.append("")
    else:
        lines += ["## Deliverables", "", "_None. No output files are presented as results of this task._", ""]
    if any(d["kind"] == "audio" for d in r["deliverables"]):
        lines += ["> 🔊 Audio files are **AI-generated speech** (Bodhan Indic-Speak, fixed voice library, no voice cloning).", ""]
    if r["assumptions"]:
        lines += ["## Assumptions", "", *[f"- {a}" for a in r["assumptions"]], ""]
    lim = list(r["limitations"])
    if r["injection_warnings"]:
        lim += [f"Input content contains text that looks like an instruction; the tools return it as data: {w}" for w in r["injection_warnings"]]
    if r["verification"]["unverified_translations_or_audio"]:
        lim.append(f"Not verified: {r['verification']['unverified_translations_or_audio']}")
    if lim:
        lines += ["## Limitations", "", *[f"- {x}" for x in lim], ""]
    others = [a for a in r["artifacts"] if a["path"] not in {d["path"] for d in r["deliverables"]}]
    if others:
        lines += ["## Intermediate artifacts", "", *[f"- [{a['path']}]({a['path']}) — {a['kind']}, {a['provenance']}" for a in others], ""]
    c = r["cost"]
    lines += ["## Cost, time and steps", "",
              f"- Status: `{r['status']}` (stop reason: `{r['stop_reason']}`)",
              f"- Bodhan cost: ₹{c['bodhan_billed_inr']:.4f} billed" + (f" + ₹{c['bodhan_uncertain_inr']:.4f} possibly billed (failed/timed-out attempts)" if c["bodhan_uncertain_inr"] else "")
              + (f"; budget ₹{c['max_inr']}" if c["max_inr"] is not None else "; no budget cap"),
              f"- Planner LLM: {r['llm']['model']} — {c['llm']['calls']} calls, {c['llm']['prompt_tokens']} prompt + {c['llm']['completion_tokens']} completion tokens ({c['llm']['pricing']})",
              f"- Time: {r['elapsed_s']} s ({r['quality_targets']['latency']['kind']}, target {r['quality_targets']['latency']['target_s']} s: "
              + ("met" if r["quality_targets"]["latency"]["met"] else "missed") + ")"
              + (f"; {r['quality_targets']['latency']['note']}" if r["quality_targets"]["latency"].get("note") else ""),
              f"- Steps: {r['steps']['used']} of {r['steps']['max']}; cache hits: {c['cache_hits']}", "",
              "| Step | Tool | OK | Latency (s) | Cost (₹) | Verification |", "|---|---|---|---|---|---|"]
    for t in r["steps"]["tool_calls"]:
        lines.append(f"| {t['step']} | `{t['tool']}` | {'yes' if t['ok'] else 'no: ' + str(t.get('error'))} | {t['latency_s']} | {t['cost_inr']} | {t.get('verification') or ''} |")
    lines += ["", "Full step-by-step trace: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).", ""]
    return "\n".join(lines)
