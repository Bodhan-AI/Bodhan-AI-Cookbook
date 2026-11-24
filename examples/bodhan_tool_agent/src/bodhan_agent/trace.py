"""Execution Tracing and Run Reporting for Bodhan CLI Task Agent (trace schema v2).

Generates:
1. trace.jsonl: Per-step record of plan, tool, arguments, summary, latency, and estimated Bodhan cost.
   Schema v2 adds span metadata (schema_version, run_id, span_id "s%04d", span_type
   tool|guard|verification|meta, parent_span_id, error_class) and full-output spill refs
   (output_ref/output_sha256/output_chars). Still exactly one line per log_step call.
2. llm_spans.jsonl: One record per LLM call (Turn-0 planning and per-step reasoning) with usage
   (prompt/completion tokens), finish_reason, parsed tool_calls, latency, and error fields.
   Lives in its own file so trace.jsonl line counts stay unchanged; rotates/mirrors with the run.
3. messages.jsonl: Full conversation transcript, one JSON line per message
   ({seq, timestamp, turn, role, content, tool_call_id, name, tool_calls}); rotates/mirrors with the run.
4. spans/: Full tool outputs spilled to disk as UTF-8 text (spans/<span_id>_<tool>-output.txt),
   capped by AGENT_TRACE_MAX_OUTPUT_BYTES (default 5 MB); trace records carry output_ref,
   output_sha256 (of the written bytes) and output_chars (original length).
5. report.json: Final machine-readable execution summary; report.json["metadata"] holds redacted
   config, versions, git commit, and input-file hashes.
6. result.md: Human-readable markdown summary linking generated artifacts.
7. runs/: Archived run snapshots (<timestamp>_<task_slug>/) guaranteeing run isolation; crash
   rotation and mirroring include messages.jsonl, llm_spans.jsonl, and spans/.

error_class taxonomy (classify_error, None on success): rate_limit, unsupported_language,
budget, policy_refusal, preflight, network, timeout, other.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.common.ledger import CostLedger, PRICE_SOURCE_URL, PRICE_VERIFIED_ON


def slugify(text: str, max_length: int = 35) -> str:
    """Convert arbitrary text into a safe, cross-platform filesystem slug."""
    text = text.lower().strip()
    # Replace non-alphanumeric characters (excluding underscores and hyphens) with hyphens
    slug = re.sub(r"[^a-z0-9_-]+", "-", text)
    # Collapse multiple consecutive hyphens
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    if max_length and len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "run"


def classify_error(result_text: str, status: str = "success") -> str | None:
    """Map a tool/guard observation to the v2 error_class taxonomy.

    Returns None for successful observations. Markers are matched
    case-insensitively; check order is contractual: a 429 rate-limit body that
    also contains transport words classifies as rate_limit, not network.
    """
    if (status or "").strip().lower() == "success":
        return None
    lowered = (result_text or "").lower()
    if "429" in lowered or "rate limit" in lowered:
        return "rate_limit"
    if "unsupported_language" in lowered:
        return "unsupported_language"
    if "budgetexceedederror" in lowered or "exceeds the remaining" in lowered:
        return "budget"
    if (
        "policy:" in lowered
        or "policy: indic-script text must originate" in lowered
        or "provenance_guard" in lowered
        or "refused by policy" in lowered
    ):
        return "policy_refusal"
    if "estimateerror" in lowered or "pre-flight estimate unavailable" in lowered:
        return "preflight"
    if (
        "connecttimeout" in lowered
        or "connecterror" in lowered
        or "connectionerror" in lowered
        or "connection refused" in lowered
        or "unreachable" in lowered
        or "bodhan service may be down" in lowered
    ):
        return "network"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    return "other"


def _normalize_span_tool_calls(tool_calls: Any, include_id: bool = False) -> list[dict[str, Any]] | None:
    """Parse raw tool_calls into plain serializable dicts.

    Tolerates parsed entries ({"name", "arguments"}), OpenAI-shaped dicts
    ({"function": {"name", "arguments"}}), and objects exposing .function.name
    / .function.arguments. Non-JSON argument strings are kept verbatim. Never
    raises.
    """
    if not tool_calls:
        return None
    try:
        candidates = list(tool_calls)
    except TypeError:
        return None
    parsed: list[dict[str, Any]] = []
    for tc in candidates:
        try:
            if isinstance(tc, dict):
                if "name" in tc or "arguments" in tc:
                    tc_id = tc.get("id")
                    name = tc.get("name")
                    raw_args = tc.get("arguments")
                else:
                    func = tc.get("function") or {}
                    tc_id = tc.get("id")
                    name = func.get("name") if isinstance(func, dict) else None
                    raw_args = func.get("arguments") if isinstance(func, dict) else None
            else:
                func = getattr(tc, "function", None)
                tc_id = getattr(tc, "id", None)
                name = getattr(func, "name", None)
                raw_args = getattr(func, "arguments", None)
            if isinstance(raw_args, str) and raw_args.strip():
                try:
                    arguments: Any = json.loads(raw_args)
                except Exception:
                    arguments = raw_args
            else:
                arguments = raw_args
            entry: dict[str, Any] = {"name": name, "arguments": arguments}
            if include_id:
                entry = {"id": tc_id, "name": name, "arguments": arguments}
            parsed.append(entry)
        except Exception:
            continue
    return parsed or None


@dataclass
class StepRecord:
    step: int
    timestamp: str
    plan: str
    tool: str
    arguments: dict[str, Any]
    result_summary: str
    latency_ms: int
    cost_inr: float
    cumulative_cost_inr: float
    status: Literal["success", "error", "budget_exceeded", "halted", "impossible", "failed"]
    cost_basis: str = "not_accounted"
    estimated_cost_inr: float | None = None
    # --- trace schema v2 (additive) ---
    schema_version: int = 2
    run_id: str = ""
    span_id: str = ""
    span_type: str = "tool"
    parent_span_id: str | None = None
    error_class: str | None = None
    output_ref: str | None = None
    output_sha256: str | None = None
    output_chars: int | None = None


class TraceLogger:
    def __init__(self, output_dir: str | Path, task: str = ""):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir = self.output_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.output_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.spans_dir = self.output_dir / "spans"
        self.spans_dir.mkdir(parents=True, exist_ok=True)
        self.trace_file = self.output_dir / "trace.jsonl"
        self.messages_file = self.output_dir / "messages.jsonl"
        self.llm_spans_file = self.output_dir / "llm_spans.jsonl"

        self.task = task
        self.task_slug = slugify(task)

        # Pre-run rotation/archiving of previous run files before starting the new run
        self._archive_previous_run_if_exists()

        # Touch the trace file up front so the output-layout invariant
        # (trace.jsonl + report.json + result.md + artifacts/) holds even for
        # runs that fail before their first logged step.
        self.trace_file.touch(exist_ok=True)
        self.messages_file.touch(exist_ok=True)
        self.llm_spans_file.touch(exist_ok=True)
        self.steps: list[StepRecord] = []
        self.start_time = time.time()
        # v2 span bookkeeping: one monotonic counter across LLM and tool spans,
        # the conversation message sequence, and the last LLM span (id, step)
        # for tool-span parent linkage.
        self._span_counter = 0
        self._message_seq = 0
        self._last_llm_span: tuple[str, int] | None = None

        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%d_%H%M%S")
        run_id = f"{ts}_{self.task_slug}"
        if (self.runs_dir / run_id).exists():
            ts = now.strftime("%Y%m%d_%H%M%S_%f")
            run_id = f"{ts}_{self.task_slug}"
        self.timestamp = ts
        self.run_id = run_id
        self.archive_dir: Path | None = None

    def _archive_previous_run_if_exists(self) -> None:
        """Rotate and archive previous run artifacts to out_dir/runs/ if not already archived.

        Guarantees run isolation:
        1. If a non-empty report.json, trace.jsonl, messages.jsonl, or llm_spans.jsonl
           exists in output_dir, or spans/ contains any entry:
           - Check if already archived in runs/. If not (e.g. crashed or unarchived run),
             archive existing files to runs/<timestamp>_<task_slug>/. This also covers runs
             that crashed after logging messages/llm-spans/spans but before the first
             log_step or report write.
        2. Clean output_dir/artifacts/ so the current run starts with a completely empty directory.
        3. Remove stale report.json and result.md, and reset trace.jsonl for the new run.
        4. Stale messages.jsonl, llm_spans.jsonl, and spans/ contents from a crashed previous
           run are copied into the crash archive, then removed/emptied for the new run.
        """
        report_file = self.output_dir / "report.json"
        trace_file = self.output_dir / "trace.jsonl"
        result_file = self.output_dir / "result.md"
        messages_file = self.output_dir / "messages.jsonl"
        llm_spans_file = self.output_dir / "llm_spans.jsonl"

        has_trace = trace_file.exists() and trace_file.stat().st_size > 0
        has_report = report_file.exists() and report_file.stat().st_size > 0
        has_messages = messages_file.exists() and messages_file.stat().st_size > 0
        has_llm_spans = llm_spans_file.exists() and llm_spans_file.stat().st_size > 0
        has_spans = self.spans_dir.exists() and any(self.spans_dir.iterdir())

        if has_trace or has_report or has_messages or has_llm_spans or has_spans:
            already_archived = False
            prev_run_id = None
            prev_task = None
            prev_ts = None

            if has_report:
                try:
                    with open(report_file, "r", encoding="utf-8") as f:
                        prev_report_data = json.load(f)
                    prev_run_id = prev_report_data.get("run_id")
                    prev_task = prev_report_data.get("task")
                    prev_ts = prev_report_data.get("timestamp")
                except Exception:
                    prev_report_data = None

                if prev_run_id:
                    archive_candidate = self.runs_dir / prev_run_id
                    if archive_candidate.is_dir() and (archive_candidate / "report.json").exists():
                        already_archived = True

            if not already_archived:
                # Archive the unarchived or crashed previous run
                if not prev_ts:
                    ref_file: Path | None
                    if has_report:
                        ref_file = report_file
                    elif has_trace:
                        ref_file = trace_file
                    elif has_messages:
                        ref_file = messages_file
                    elif has_llm_spans:
                        ref_file = llm_spans_file
                    else:
                        # Only spans/ content: fall back to the current time.
                        ref_file = None
                    if ref_file is not None:
                        mtime = ref_file.stat().st_mtime
                        prev_ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
                    else:
                        prev_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

                if not prev_task:
                    if has_trace:
                        try:
                            with open(trace_file, "r", encoding="utf-8") as tf:
                                first_line = tf.readline().strip()
                                if first_line:
                                    rec = json.loads(first_line)
                                    prev_task = rec.get("plan") or "crashed_run"
                        except Exception:
                            prev_task = "crashed_run"
                    else:
                        prev_task = "crashed_run"

                prev_slug = slugify(prev_task or "crashed_run")
                crashed_run_id = f"{prev_ts}_{prev_slug}"
                target_archive = self.runs_dir / crashed_run_id
                if target_archive.exists():
                    target_archive = self.runs_dir / f"{prev_ts}_{int(time.time() * 1000) % 1000:03d}_{prev_slug}"

                target_archive.mkdir(parents=True, exist_ok=True)
                target_artifacts = target_archive / "artifacts"
                target_artifacts.mkdir(parents=True, exist_ok=True)

                for src in (report_file, result_file, trace_file, messages_file, llm_spans_file):
                    if src.exists():
                        shutil.copy2(src, target_archive / src.name)

                if self.spans_dir.exists():
                    target_spans = target_archive / "spans"
                    target_spans.mkdir(parents=True, exist_ok=True)
                    for p in self.spans_dir.iterdir():
                        if p.is_file():
                            shutil.copy2(p, target_spans / p.name)
                        elif p.is_dir():
                            shutil.copytree(p, target_spans / p.name, dirs_exist_ok=True)

                if self.artifacts_dir.exists():
                    for p in self.artifacts_dir.iterdir():
                        if p.is_file():
                            shutil.copy2(p, target_artifacts / p.name)
                        elif p.is_dir():
                            shutil.copytree(p, target_artifacts / p.name, dirs_exist_ok=True)

        # In all cases where previous files might exist, clean the active output directory
        if self.artifacts_dir.exists():
            for p in self.artifacts_dir.iterdir():
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(p)

        if report_file.exists():
            report_file.unlink()
        if result_file.exists():
            result_file.unlink()
        if trace_file.exists():
            trace_file.unlink()
        if messages_file.exists():
            messages_file.unlink()
        if llm_spans_file.exists():
            llm_spans_file.unlink()
        if self.spans_dir.exists():
            for p in self.spans_dir.iterdir():
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(p)

    def log_step(
        self,
        step: int,
        plan: str,
        tool: str,
        arguments: dict[str, Any],
        result_summary: str,
        latency_ms: int,
        cost_inr: float,
        cumulative_cost_inr: float,
        status: Literal["success", "error", "budget_exceeded", "halted"] = "success",
        cost_basis: str = "not_accounted",
        estimated_cost_inr: float | None = None,
        *,
        full_output: str | None = None,
        error_class: str | None = None,
        span_type: str = "tool",
    ) -> StepRecord:
        span_id = self._next_span_id()
        parent_span_id: str | None = None
        if self._last_llm_span and self._last_llm_span[1] == step:
            parent_span_id = self._last_llm_span[0]

        output_ref: str | None = None
        output_sha256: str | None = None
        output_chars: int | None = None
        if full_output is not None:
            output_chars = len(full_output)
            try:
                output_ref, output_sha256 = self._spill_output(span_id, tool, full_output)
            except Exception:
                output_ref = None
                output_sha256 = None

        record = StepRecord(
            step=step,
            timestamp=datetime.now(timezone.utc).isoformat(),
            plan=plan,
            tool=tool,
            arguments=arguments,
            result_summary=result_summary,
            latency_ms=latency_ms,
            cost_inr=cost_inr,
            cumulative_cost_inr=cumulative_cost_inr,
            status=status,
            cost_basis=cost_basis,
            estimated_cost_inr=estimated_cost_inr,
            schema_version=2,
            run_id=self.run_id,
            span_id=span_id,
            span_type=span_type,
            parent_span_id=parent_span_id,
            error_class=error_class,
            output_ref=output_ref,
            output_sha256=output_sha256,
            output_chars=output_chars,
        )
        self.steps.append(record)

        # Immediate flush to trace.jsonl
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")

        return record

    def _next_span_id(self) -> str:
        """Allocate the next monotonic span id ("s%04d")."""
        self._span_counter += 1
        return f"s{self._span_counter:04d}"

    def _max_output_bytes(self) -> int:
        try:
            return max(1, int(os.environ.get("AGENT_TRACE_MAX_OUTPUT_BYTES", "5000000")))
        except (TypeError, ValueError):
            return 5_000_000

    def _spill_output(self, span_id: str, tool: str, full_output: str) -> tuple[str, str]:
        """Spill a full tool output to spans/<span_id>_<tool>-output.txt (UTF-8).

        Returns (posix-relative output_ref, sha256 of the written bytes). The
        written file is capped at AGENT_TRACE_MAX_OUTPUT_BYTES (default 5 MB)
        by truncating the UTF-8 bytes; the slice backs off over partial UTF-8
        continuation sequences so the truncated file stays valid UTF-8, and a
        short marker is appended inside the cap so it never exceeds the limit.
        """
        self.spans_dir.mkdir(parents=True, exist_ok=True)
        safe_tool = re.sub(r"[^A-Za-z0-9_-]+", "_", tool or "tool").strip("_") or "tool"
        file_name = f"{span_id}_{safe_tool}-output.txt"
        path = self.spans_dir / file_name
        data = full_output.encode("utf-8")
        cap = self._max_output_bytes()
        if len(data) > cap:
            marker = "\n...[truncated]".encode("utf-8")
            room = cap - len(marker)
            if room > 0:
                # Back off over any partial UTF-8 continuation sequence at the
                # slice boundary so data[:room] + marker is always valid UTF-8.
                while room > 0 and (data[room] & 0xC0) == 0x80:
                    room -= 1
                data = data[:room] + marker
            elif room == 0:
                # Backoff allowance exhausted: write only the marker.
                data = marker
            else:
                # Cap smaller than the marker: largest valid UTF-8 prefix
                # that still fits under the cap.
                room = cap
                while room > 0 and (data[room] & 0xC0) == 0x80:
                    room -= 1
                data = data[:room]
        with open(path, "wb") as f:
            f.write(data)
        sha256 = hashlib.sha256(data).hexdigest()
        return f"spans/{file_name}", sha256

    def log_message(
        self,
        role: str,
        content: str | None,
        turn: int | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
        tool_calls: Any = None,
    ) -> None:
        """Append one conversation message to messages.jsonl.

        One JSON line per message with an incrementing seq, flush-on-write.
        Best-effort: write failures never crash the agent.
        """
        self._message_seq += 1
        record = {
            "seq": self._message_seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn": turn,
            "role": role,
            "content": content,
            "tool_call_id": tool_call_id,
            "name": name,
            "tool_calls": _normalize_span_tool_calls(tool_calls, include_id=True),
        }
        try:
            with open(self.messages_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def log_llm_span(
        self,
        step: int,
        plan: str,
        model: str,
        latency_ms: int,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        finish_reason: str | None = None,
        tool_calls: Any = None,
        error_class: str | None = None,
        error: str | None = None,
    ) -> str:
        """Record one LLM call (Turn-0 planning or per-step reasoning) in llm_spans.jsonl.

        Written to its own file (never trace.jsonl) so trace.jsonl keeps exactly
        one line per log_step call. Best-effort: never raises; the span id is
        remembered for tool-span parent linkage even on error spans. Returns
        the span id.
        """
        span_id = self._next_span_id()
        record = {
            "schema_version": 2,
            "run_id": self.run_id,
            "span_index": self._span_counter,
            "span_id": span_id,
            "parent_span_id": None,
            "step": step,
            "turn": step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "span_type": "llm",
            "plan": plan,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "finish_reason": finish_reason,
            "tool_calls": _normalize_span_tool_calls(tool_calls, include_id=False),
            "latency_ms": latency_ms,
            "error_class": error_class,
            "error": error,
        }
        try:
            with open(self.llm_spans_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
        self._last_llm_span = (span_id, step)
        return span_id

    def _mirror_to_runs_archive(self) -> Path:
        """Mirror current run outputs and artifacts to out_dir/runs/<timestamp>_<task_slug>/."""
        target_dir = self.runs_dir / self.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_artifacts = target_dir / "artifacts"
        target_artifacts.mkdir(parents=True, exist_ok=True)

        for filename in ("report.json", "result.md", "trace.jsonl", "messages.jsonl", "llm_spans.jsonl"):
            src_file = self.output_dir / filename
            if src_file.exists():
                shutil.copy2(src_file, target_dir / filename)

        target_spans = target_dir / "spans"
        target_spans.mkdir(parents=True, exist_ok=True)
        if self.spans_dir.exists():
            for p in self.spans_dir.iterdir():
                if p.is_file():
                    shutil.copy2(p, target_spans / p.name)
                elif p.is_dir():
                    shutil.copytree(p, target_spans / p.name, dirs_exist_ok=True)

        if self.artifacts_dir.exists():
            for p in self.artifacts_dir.iterdir():
                if p.is_file():
                    shutil.copy2(p, target_artifacts / p.name)
                elif p.is_dir():
                    shutil.copytree(p, target_artifacts / p.name, dirs_exist_ok=True)

        self.archive_dir = target_dir
        return target_dir

    def write_report_and_result(
        self,
        task: str,
        status: Literal["completed", "failed", "budget_exhausted", "impossible"],
        final_answer: str,
        cost_ledger: CostLedger,
        verification_details: dict[str, Any] | None = None,
        assumptions: list[str] | None = None,
        plan: dict[str, Any] | None = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        elapsed_sec = round(time.time() - self.start_time, 2)

        # If task was not known at TraceLogger.__init__, update task, slug, and run_id now
        if not self.task and task:
            self.task = task
            self.task_slug = slugify(task)
            self.run_id = f"{self.timestamp}_{self.task_slug}"
        
        # 1. Write report.json
        artifacts = sorted([
            p for p in self.artifacts_dir.rglob("*")
            if p.is_file()
        ])
        artifacts_list = [p.relative_to(self.artifacts_dir).as_posix() for p in artifacts]
        report_data = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "task": task,
            "status": status,
            "total_steps": len(self.steps),
            "total_cost_inr": cost_ledger.cumulative_cost_inr,
            "estimated_bodhan_cost_inr": cost_ledger.cumulative_cost_inr,
            "max_budget_inr": cost_ledger.max_budget_inr,
            "cost_basis": (
                "Bodhan published unit-rate estimate only; excludes OpenAI-compatible "
                "LLM/gateway charges; translation units are estimated from text length; "
                "nominal rates are applied to cache-served calls; and failed-request charges "
                "are unknown. It is not provider-reported billing."
            ),
            "pricing_source_url": PRICE_SOURCE_URL,
            "pricing_verified_on": PRICE_VERIFIED_ON,
            "elapsed_seconds": elapsed_sec,
            "plan": plan or {},
            "assumptions": assumptions or [],
            "verification": verification_details or {"verified": False, "notes": "None"},
            "artifacts": artifacts_list,
            "execution_telemetry": {
                "total_tool_latency_ms": sum(s.latency_ms for s in self.steps),
                "step_count": len(self.steps),
                "error_count": sum(1 for s in self.steps if s.status == "error"),
                "failed_tools": [s.tool for s in self.steps if s.status == "error"],
                "last_error": next((s.result_summary for s in reversed(self.steps) if s.status == "error"), None),
            },
            "metadata": metadata or {},
        }
        with open(self.output_dir / "report.json", "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        # 2. Write result.md
        result_lines = [
            "# Task Execution Summary",
            "",
            f"**Task:** {task}  ",
            f"**Status:** `{status.upper()}`  ",
            f"**Total Steps:** {len(self.steps)} | **Estimated Bodhan cost (LLM/gateway excluded):** ₹{cost_ledger.cumulative_cost_inr:.2f} / ₹{cost_ledger.max_budget_inr:.2f} | **Time:** {elapsed_sec}s  ",
            "",
            "The amount above is an estimate based on published Bodhan unit rates, not provider-reported billing. "
            "OpenAI-compatible LLM/gateway charges are excluded. Cache-served calls are counted at nominal rates, "
            "and charges for failed requests may differ; check provider usage records for actual charges.",
        ]

        if plan and (plan.get("goal") or plan.get("steps")):
            result_lines.extend([
                "## Execution Plan",
                "",
            ])
            if plan.get("goal"):
                result_lines.append(f"**Goal:** {plan['goal']}  ")
            if plan.get("target_language"):
                result_lines.append(f"**Target Language:** {plan['target_language']}  ")
            if plan.get("steps"):
                result_lines.append("")
                result_lines.append("**Steps:**")
                for i, s in enumerate(plan["steps"], 1):
                    result_lines.append(f"{i}. {s}")
            result_lines.append("")

        if assumptions:
            result_lines.extend([
                "## Assumptions Made",
                "",
                *[f"- {a}" for a in assumptions],
                "",
            ])

        result_lines.extend([
            "## Final Output",
            "",
            final_answer.strip(),
            "",
        ])

        if artifacts:
            result_lines.extend([
                "## Generated Artifacts",
                "",
                *[
                    f"- [`{p.relative_to(self.artifacts_dir).as_posix()}`](artifacts/{p.relative_to(self.artifacts_dir).as_posix()}) ({p.stat().st_size / 1024:.1f} KB)"
                    for p in artifacts
                ],
                "",
            ])

        with open(self.output_dir / "result.md", "w", encoding="utf-8") as f:
            f.write("\n".join(result_lines))

        # 3. Mirror current run to runs archive snapshot
        self._mirror_to_runs_archive()
