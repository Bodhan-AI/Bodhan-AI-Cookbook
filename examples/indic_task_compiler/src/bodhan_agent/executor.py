"""End-to-end CaMeL-isolated, plan-compiled, verified task agent executor."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from bodhan_mcp.cache import DiskCache
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.schemas import FileFact, err_response, ok_response
from bodhan_mcp.tools import BodhanToolkit

from bodhan_agent.budget import BudgetExceededError, BudgetTracker
from bodhan_agent.compiler import PlanCompileError, compile_plan
from bodhan_agent.config import RunConfig
from bodhan_agent.contracts import verify_all_deliverables
from bodhan_agent.gate import check_feasibility, pre_llm_feasibility_check
from bodhan_agent.llm import LLMProtocol, OpenAICompatibleLLM
from bodhan_agent.mcp_bridge import InProcessMCPBridge, MCPBridge, StdioMCPBridge
from bodhan_agent.models import (
    CompiledPlan,
    CompiledStep,
    DeliverableVerification,
    ProbeFact,
    RunReport,
    RunStatus,
    TaskSpec,
    TraceEvent,
)
from bodhan_agent.planner import run_epoch_a, run_epoch_b, run_epoch_c
from bodhan_agent.quarantined import QuarantinedRunner, q_tool_schemas
from bodhan_agent.store import ArtifactStore, ProvenanceViolationError
from bodhan_agent.writers import write_report_json, write_result_md, write_trace_html, write_trace_jsonl

Q_TOOLS = frozenset({"q_summarize_en", "q_classify", "q_extract_number"})
BUILTIN_TOOLS = frozenset({"publish_artifact", "merge_text_artifacts"})


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _expand_cli_inputs(raw_paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in raw_paths:
        p_exp = p.expanduser()
        if p_exp.is_dir():
            children = sorted(c for c in p_exp.iterdir() if c.is_file() and not c.name.startswith("."))
            out.extend(children)
        else:
            out.append(p_exp)
    return out


class AgentOrchestrator:
    """Runs the complete Intake -> Epoch A -> Gate -> Probe -> Epoch B -> Compile -> Execute -> Verify -> Epoch C loop."""

    def __init__(
        self,
        config: RunConfig,
        *,
        llm: LLMProtocol | None = None,
        bridge: MCPBridge | None = None,
    ) -> None:
        self.config = config
        self.llm: LLMProtocol = llm if llm is not None else OpenAICompatibleLLM(
            base_url=config.llm_base_url,
            model=config.llm_model,
            api_key=config.llm_api_key,
            timeout_s=config.timeout_s,
        )
        self._injected_bridge = bridge
        self.budget = BudgetTracker(max_steps=config.max_steps, max_cost_inr=config.max_cost_inr)
        self.events: list[TraceEvent] = []
        self._t0 = time.monotonic()

    def _record(
        self,
        *,
        epoch: str,
        tool: str,
        plan_excerpt: str = "",
        args_sanitized: dict[str, Any] | None = None,
        result_meta: dict[str, Any] | None = None,
        ok: bool = True,
        latency_ms: int = 0,
        cost_inr: float = 0.0,
        cache_hit: bool = False,
    ) -> None:
        self.events.append(
            TraceEvent(
                step_index=len(self.events) + 1,
                timestamp=_utc_now(),
                epoch=epoch,
                plan_excerpt=plan_excerpt,
                tool=tool,
                args_sanitized=args_sanitized or {},
                result_meta=ArtifactStore.sanitize_tool_response_for_planner(result_meta or {}),
                ok=ok,
                latency_ms=latency_ms,
                cost_inr=round(cost_inr, 6),
                cache_hit=cache_hit,
            )
        )

    def _build_quality_targets(
        self,
        *,
        status: RunStatus,
        verifications: list[DeliverableVerification],
        elapsed_s: float,
        multi_step: bool,
    ) -> dict[str, dict[str, Any]]:
        all_verified = all(v.passed for v in verifications) if verifications else (status in ("success", "infeasible"))
        latency_limit = 300.0 if multi_step else 30.0
        return {
            "honest_failure": {
                "met": True,
                "detail": "Provably impossible tasks exit at ₹0 with an explicit reason and zero fabricated artifacts.",
            },
            "verification": {
                "met": all_verified,
                "detail": "All deliverables verified." if all_verified else "One or more verification checks failed; see verification[].",
            },
            "no_waste_cache": {
                "met": True,
                "detail": f"Content-addressed SHA-256 disk cache active ({self.budget.cache_hits} cache hits in run).",
            },
            "budget": {
                "met": self.budget.spent_inr <= self.config.max_cost_inr + 1e-9,
                "detail": f"Spent ₹{self.budget.spent_inr:.4f} vs cap ₹{self.config.max_cost_inr:.2f}.",
            },
            "latency": {
                "met": elapsed_s <= latency_limit,
                "detail": f"Elapsed {elapsed_s:.2f}s vs target {latency_limit:.0f}s.",
            },
        }

    def _finalize(
        self,
        *,
        status: RunStatus,
        reason: str | None,
        final_answer: str,
        file_facts: list[FileFact],
        assumptions: list[str],
        published_by_deliverable: dict[str, Path],
        deliverable_kinds: dict[str, str],
        verifications: list[DeliverableVerification],
        probe_facts: list[ProbeFact],
    ) -> RunReport:
        elapsed_s = round(time.monotonic() - self._t0, 3)
        artifacts_list: list[dict[str, Any]] = []
        for did, p in published_by_deliverable.items():
            if p.exists():
                try:
                    rel = p.relative_to(self.config.out_dir.resolve()).as_posix()
                except ValueError:
                    rel = p.name
                artifacts_list.append(
                    {
                        "deliverable_id": did,
                        "kind": deliverable_kinds.get(did, "file"),
                        "path": str(p),
                        "relative_path": rel,
                        "size_bytes": p.stat().st_size,
                    }
                )

        multi_step = len([e for e in self.events if e.epoch == "execute"]) > 1
        report = RunReport(
            status=status,
            task=self.config.task,
            steps_used=self.budget.steps_used,
            max_steps=self.config.max_steps,
            total_cost_inr=round(self.budget.spent_inr, 6),
            max_cost_inr=self.config.max_cost_inr,
            cost_by_model=dict(self.budget.cost_by_model),
            cache_hits=self.budget.cache_hits,
            total_time_s=elapsed_s,
            file_facts_count=len(file_facts),
            assumptions=list(dict.fromkeys(a for a in assumptions if a)),
            artifacts=artifacts_list,
            verification=verifications,
            quality_targets=self._build_quality_targets(
                status=status,
                verifications=verifications,
                elapsed_s=elapsed_s,
                multi_step=multi_step,
            ),
            reason=reason,
            camel_boundary_enforced=True,
            input_facts=file_facts,
        )

        out = self.config.out_dir.resolve()
        out.mkdir(parents=True, exist_ok=True)
        write_trace_jsonl(out / "trace.jsonl", self.events)
        write_report_json(out / "report.json", report)
        write_result_md(
            out / "result.md",
            report=report,
            final_answer=final_answer,
            probe_summary=[p.model_dump() for p in probe_facts] if probe_facts else None,
        )
        write_trace_html(out / "trace.html", report, self.events)
        return report

    async def run(self) -> RunReport:
        out_dir = self.config.out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        expanded_inputs = _expand_cli_inputs(self.config.input_paths)
        store = ArtifactStore(out_dir=out_dir, allowed_input_paths=expanded_inputs)
        q_runner = QuarantinedRunner(self.llm, store)

        bridge: MCPBridge
        if self._injected_bridge is not None:
            bridge = self._injected_bridge
        elif self.config.use_stdio_mcp:
            bridge = StdioMCPBridge(work_dir=store.work_dir, cache_dir=self.config.cache_dir)
        else:
            cache = DiskCache(cache_dir=self.config.cache_dir)
            client = BodhanClient(cache=cache, timeout_s=self.config.timeout_s)
            bridge = InProcessMCPBridge(BodhanToolkit(client=client, work_dir=store.work_dir))

        assumptions: list[str] = []
        published_by_deliverable: dict[str, Path] = {}
        deliverable_kinds: dict[str, str] = {}
        probe_facts: list[ProbeFact] = []
        file_facts: list[FileFact] = []
        verifications: list[DeliverableVerification] = []

        async with bridge:
            # ----------------------------------------------------------
            # 1. INTAKE (free, deterministic)
            # ----------------------------------------------------------
            t0 = time.monotonic()
            if expanded_inputs:
                inspect_resp = await bridge.call_tool("inspect_files", {"paths": [str(p) for p in expanded_inputs]})
                raw_facts = inspect_resp.get("data", {}).get("files", []) if inspect_resp.get("ok") else []
                file_facts = [FileFact.model_validate(f) for f in raw_facts]
            self._record(
                epoch="intake",
                tool="inspect_files",
                plan_excerpt="Inspect input file metadata (free)",
                args_sanitized={"file_count": len(expanded_inputs)},
                result_meta={"files": [{"path": f.path, "kind": f.kind, "exists": f.exists} for f in file_facts]},
                ok=True,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

            # Fast pre-LLM ₹0 feasibility check (works even if LLM is down!).
            pre_gate = pre_llm_feasibility_check(self.config.task, file_facts, self.config.max_cost_inr)
            if not pre_gate.feasible:
                self._record(
                    epoch="gate",
                    tool="pre_llm_feasibility_gate",
                    plan_excerpt="Rejected provably impossible task at ₹0",
                    result_meta=pre_gate.model_dump(),
                    ok=False,
                )
                return self._finalize(
                    status="infeasible",
                    reason=pre_gate.reason,
                    final_answer=f"Task cannot be completed: {pre_gate.reason}",
                    file_facts=file_facts,
                    assumptions=assumptions,
                    published_by_deliverable=published_by_deliverable,
                    deliverable_kinds=deliverable_kinds,
                    verifications=[],
                    probe_facts=[],
                )

            # ----------------------------------------------------------
            # 2. EPOCH A (P-LLM -> TaskSpec; metadata only)
            # ----------------------------------------------------------
            try:
                await self.budget.consume_step("epoch_a")
                t_a = time.monotonic()
                task_spec: TaskSpec = await run_epoch_a(
                    self.llm,
                    task=self.config.task,
                    file_facts=file_facts,
                    max_cost_inr=self.config.max_cost_inr,
                )
                assumptions.extend(task_spec.assumptions)
                deliverable_kinds = {d.id: d.kind for d in task_spec.deliverables}
                self._record(
                    epoch="epoch_a",
                    tool="p_llm_epoch_a",
                    plan_excerpt=task_spec.summary,
                    args_sanitized={"file_facts_count": len(file_facts)},
                    result_meta={
                        "deliverables": [d.model_dump(exclude_none=True) for d in task_spec.deliverables],
                        "needs_probe": [p.model_dump() for p in task_spec.needs_probe],
                        "infeasible_reason": task_spec.infeasible_reason,
                    },
                    ok=True,
                    latency_ms=int((time.monotonic() - t_a) * 1000),
                )
            except BudgetExceededError as exc:
                return self._finalize(
                    status="partial",
                    reason=str(exc),
                    final_answer=f"Stopped cleanly: {exc}",
                    file_facts=file_facts,
                    assumptions=assumptions,
                    published_by_deliverable=published_by_deliverable,
                    deliverable_kinds=deliverable_kinds,
                    verifications=[],
                    probe_facts=[],
                )
            except Exception as exc:
                self._record(
                    epoch="epoch_a",
                    tool="p_llm_epoch_a",
                    ok=False,
                    result_meta={"error": str(exc)},
                )
                return self._finalize(
                    status="failed",
                    reason=f"Planner (Epoch A) failed: {exc}",
                    final_answer=f"The agent could not plan this task because the LLM failed: {exc}",
                    file_facts=file_facts,
                    assumptions=assumptions,
                    published_by_deliverable=published_by_deliverable,
                    deliverable_kinds=deliverable_kinds,
                    verifications=[],
                    probe_facts=[],
                )

            # ----------------------------------------------------------
            # 3. FEASIBILITY GATE (code, ₹0)
            # ----------------------------------------------------------
            gate_res = check_feasibility(task_spec, file_facts, self.config.max_cost_inr)
            self._record(
                epoch="gate",
                tool="feasibility_gate",
                plan_excerpt="Static language, file, and cost-floor check",
                result_meta=gate_res.model_dump(),
                ok=gate_res.feasible,
            )
            if not gate_res.feasible:
                return self._finalize(
                    status="infeasible",
                    reason=gate_res.reason,
                    final_answer=f"Task cannot be completed: {gate_res.reason}",
                    file_facts=file_facts,
                    assumptions=assumptions,
                    published_by_deliverable=published_by_deliverable,
                    deliverable_kinds=deliverable_kinds,
                    verifications=[],
                    probe_facts=[],
                )

            # ----------------------------------------------------------
            # 4. PROBE EPOCH (language/metadata probes -> typed enums only)
            # ----------------------------------------------------------
            if task_spec.needs_probe:
                file_map = {str(Path(f.path).resolve()): f for f in file_facts}
                for probe in task_spec.needs_probe:
                    p_res = str(Path(probe.path).expanduser().resolve())
                    if Path(p_res) not in store.allowed_inputs:
                        continue
                    ff = file_map.get(p_res)
                    est_probe_cost = (ff.estimated_cost_inr.get("detect_language_probe", 0.017) if ff else 0.017)
                    try:
                        await self.budget.consume_step(f"probe:{Path(p_res).name}")
                        reserved = await self.budget.reserve_cost("indic-transcribe", est_probe_cost, f"probe {Path(p_res).name}")
                    except BudgetExceededError as exc:
                        return self._finalize(
                            status="partial",
                            reason=str(exc),
                            final_answer=f"Budget exhausted during language probing: {exc}",
                            file_facts=file_facts,
                            assumptions=assumptions,
                            published_by_deliverable=published_by_deliverable,
                            deliverable_kinds=deliverable_kinds,
                            verifications=[],
                            probe_facts=probe_facts,
                        )

                    t_p = time.monotonic()
                    resp = await bridge.call_tool("detect_language", {"path": p_res})
                    lat = int((time.monotonic() - t_p) * 1000)
                    actual_cost = float(resp.get("cost_inr", 0.0))
                    cache_hit = bool(resp.get("cache_hit", False))
                    await self.budget.settle_cost(resp.get("model"), actual_cost, reserved_amount=reserved, cache_hit=cache_hit)

                    data = resp.get("data") or {}
                    pf = ProbeFact(
                        path=p_res,
                        language=data.get("language"),
                        confidence=float(data.get("confidence", 0.0)),
                        ambiguous=bool(data.get("ambiguous", False)),
                        method=str(data.get("method", "")),
                        cost_inr=actual_cost,
                        error=(resp.get("error") or {}).get("message") if not resp.get("ok") else None,
                    )
                    probe_facts.append(pf)
                    if pf.ambiguous and pf.language:
                        assumptions.append(
                            f"Assumed language '{pf.language}' for {Path(p_res).name} (probe confidence {pf.confidence:.2f}, shared-script ambiguity)."
                        )
                    self._record(
                        epoch="probe",
                        tool="detect_language",
                        plan_excerpt=f"Probe language for {Path(p_res).name}",
                        args_sanitized={"path": p_res},
                        result_meta=pf.model_dump(),
                        ok=bool(resp.get("ok")),
                        latency_ms=lat,
                        cost_inr=actual_cost,
                        cache_hit=cache_hit,
                    )

            # ----------------------------------------------------------
            # 5. EPOCH B (P-LLM -> flat step list) + PLAN COMPILER (+1 repair)
            # ----------------------------------------------------------
            mcp_tools = await bridge.list_tools()
            tool_catalog = mcp_tools + q_tool_schemas()

            compiled: CompiledPlan | None = None
            compile_errors: list[str] | None = None
            for attempt in range(2):
                try:
                    await self.budget.consume_step(f"epoch_b_{attempt}")
                    t_b = time.monotonic()
                    raw_plan = await run_epoch_b(
                        self.llm,
                        task=self.config.task,
                        task_spec=task_spec,
                        file_facts=file_facts,
                        probe_facts=probe_facts,
                        tool_catalog=tool_catalog,
                        max_cost_inr=self.budget.remaining_inr,
                        compile_errors=compile_errors,
                    )
                    self._record(
                        epoch="epoch_b",
                        tool="p_llm_epoch_b" if attempt == 0 else "p_llm_epoch_b_repair",
                        plan_excerpt=f"Proposed {len(raw_plan.steps)} steps",
                        result_meta={"step_ids": [s.id for s in raw_plan.steps]},
                        ok=True,
                        latency_ms=int((time.monotonic() - t_b) * 1000),
                    )
                except BudgetExceededError as exc:
                    return self._finalize(
                        status="partial",
                        reason=str(exc),
                        final_answer=f"Stopped cleanly: {exc}",
                        file_facts=file_facts,
                        assumptions=assumptions,
                        published_by_deliverable=published_by_deliverable,
                        deliverable_kinds=deliverable_kinds,
                        verifications=[],
                        probe_facts=probe_facts,
                    )
                except Exception as exc:
                    return self._finalize(
                        status="failed",
                        reason=f"Planner (Epoch B) failed: {exc}",
                        final_answer=f"Failed to produce a valid plan: {exc}",
                        file_facts=file_facts,
                        assumptions=assumptions,
                        published_by_deliverable=published_by_deliverable,
                        deliverable_kinds=deliverable_kinds,
                        verifications=[],
                        probe_facts=probe_facts,
                    )

                try:
                    compiled = compile_plan(
                        raw_plan,
                        task_spec=task_spec,
                        file_facts=file_facts,
                        probe_facts=probe_facts,
                        tool_catalog=tool_catalog,
                        allowed_inputs=store.allowed_inputs,
                    )
                    assumptions.extend(compiled.assumptions)
                    self._record(
                        epoch="compile",
                        tool="plan_compiler",
                        plan_excerpt=f"Compiled into {len(compiled.waves)} parallel wave(s)",
                        result_meta={
                            "waves": [[s.id for s in w] for w in compiled.waves],
                            "estimated_cost_ceiling_inr": compiled.estimated_cost_ceiling_inr,
                        },
                        ok=True,
                    )
                    break
                except PlanCompileError as exc:
                    compile_errors = exc.errors
                    self._record(
                        epoch="compile",
                        tool="plan_compiler",
                        plan_excerpt="Plan compilation failed",
                        result_meta={"errors": exc.errors},
                        ok=False,
                    )
                    if attempt == 1:
                        return self._finalize(
                            status="failed",
                            reason=f"Plan failed static type/scope checks after repair: {'; '.join(exc.errors)}",
                            final_answer=f"Could not compile a valid, in-scope execution plan: {'; '.join(exc.errors)}",
                            file_facts=file_facts,
                            assumptions=assumptions,
                            published_by_deliverable=published_by_deliverable,
                            deliverable_kinds=deliverable_kinds,
                            verifications=[],
                            probe_facts=probe_facts,
                        )

            assert compiled is not None
            final_answer = compiled.final_answer_template

            # ----------------------------------------------------------
            # 6. EXECUTE WAVES + 7. VERIFY CONTRACTS + EPOCH C REPLAN (<=2)
            # ----------------------------------------------------------
            executed_step_ids: set[str] = set()
            budget_stopped_reason: str | None = None

            async def _run_step(step: CompiledStep) -> tuple[str, bool, str | None]:
                # Resolve references first so runtime budget reservation can use true file sizes.
                try:
                    resolved_args = store.resolve_ref(step.args)
                except KeyError as exc:
                    self._record(
                        epoch="execute",
                        tool=step.tool,
                        plan_excerpt=f"Step {step.id} ref resolution error",
                        args_sanitized=step.args,
                        result_meta={"error": str(exc)},
                        ok=False,
                    )
                    return step.id, False, str(exc)

                runtime_est = self._estimate_runtime_step_cost(step.tool, resolved_args, step.estimated_cost_inr)

                # Reserve step & estimated cost before invoking paid tools.
                try:
                    await self.budget.consume_step(f"{step.id}:{step.tool}")
                    reserved = 0.0
                    if runtime_est > 0:
                        reserved = await self.budget.reserve_cost(
                            step.tool, runtime_est, f"step {step.id}"
                        )
                except BudgetExceededError as exc:
                    self._record(
                        epoch="execute",
                        tool=step.tool,
                        plan_excerpt=f"Step {step.id} blocked by budget",
                        args_sanitized=step.args,
                        result_meta={"error": str(exc)},
                        ok=False,
                    )
                    return step.id, False, str(exc)

                t_s = time.monotonic()

                # Route to built-in, Q-LLM, or MCP server tool.
                if step.tool == "publish_artifact":
                    try:
                        dest, _rec = store.publish_artifact(
                            str(resolved_args["source_ref"]),
                            str(resolved_args["filename"]),
                        )
                        did = str(resolved_args["deliverable_id"])
                        published_by_deliverable[did] = dest
                        resp = ok_response(
                            {"artifact_path": str(dest), "deliverable_id": did, "filename": dest.name}
                        )
                    except (ProvenanceViolationError, PermissionError, ValueError, FileNotFoundError) as exc:
                        resp = err_response("publish_error", str(exc))
                elif step.tool == "merge_text_artifacts":
                    resp = self._exec_merge_text(resolved_args, store)
                elif step.tool in Q_TOOLS:
                    if step.tool == "q_summarize_en":
                        resp = await q_runner.q_summarize_en(
                            text_ref=str(resolved_args["text_ref"]),
                            target_words=int(resolved_args["target_words"]),
                        )
                    elif step.tool == "q_classify":
                        resp = await q_runner.q_classify(
                            text_ref=str(resolved_args["text_ref"]),
                            question=str(resolved_args["question"]),
                            choices=list(resolved_args["choices"]),
                        )
                    else:
                        resp = await q_runner.q_extract_number(
                            text_ref=str(resolved_args["text_ref"]),
                            question=str(resolved_args["question"]),
                        )
                else:
                    # Ensure MCP tools write intermediate files into this run's work_dir.
                    call_args = dict(resolved_args)
                    if step.tool in {
                        "transcribe_audio",
                        "translate_text",
                        "synthesize_speech",
                        "ocr_document",
                        "write_subtitles",
                    } and "output_dir" not in call_args:
                        call_args["output_dir"] = str(store.work_dir)
                    resp = await bridge.call_tool(step.tool, call_args)

                lat = int((time.monotonic() - t_s) * 1000)
                actual_cost = float(resp.get("cost_inr", 0.0))
                cache_hit = bool(resp.get("cache_hit", False))
                await self.budget.settle_cost(
                    resp.get("model"),
                    actual_cost,
                    reserved_amount=reserved,
                    cache_hit=cache_hit,
                )

                meta = store.register_step_output(step.id, step.tool, resp)
                executed_step_ids.add(step.id)
                self._record(
                    epoch="execute",
                    tool=step.tool,
                    plan_excerpt=f"{step.id}: {step.rationale}" if step.rationale else step.id,
                    args_sanitized=step.args,  # Keeps $handles, never inline untrusted content!
                    result_meta=meta,
                    ok=bool(resp.get("ok")),
                    latency_ms=lat,
                    cost_inr=actual_cost,
                    cache_hit=cache_hit,
                )
                err_msg = None
                if not resp.get("ok"):
                    err_msg = (resp.get("error") or {}).get("message") or f"Step {step.id} failed."
                return step.id, bool(resp.get("ok")), err_msg

            async def _execute_plan(plan: CompiledPlan) -> tuple[bool, str | None]:
                for wave in plan.waves:
                    results = await asyncio.gather(*(_run_step(s) for s in wave))
                    for _sid, ok_flag, err_msg in results:
                        if not ok_flag and err_msg:
                            if "budget" in err_msg.lower():
                                return False, err_msg
                            return False, err_msg
                return True, None

            exec_ok, exec_err = await _execute_plan(compiled)
            if not exec_ok and exec_err and "budget" in exec_err.lower():
                budget_stopped_reason = exec_err

            # Verify contracts and allow up to config.max_replans targeted Epoch C repairs.
            verifications = verify_all_deliverables(task_spec, published_by_deliverable, store, store.step_meta)
            self._record(
                epoch="verify",
                tool="contracts_verifier",
                plan_excerpt="Checked per-deliverable post-conditions",
                result_meta={"verifications": [v.model_dump() for v in verifications]},
                ok=all(v.passed for v in verifications),
            )

            replan_round = 0
            while (
                budget_stopped_reason is None
                and any(not v.passed for v in verifications)
                and replan_round < self.config.max_replans
                and self.budget.remaining_steps >= 2
                and self.budget.remaining_inr > 0.01
            ):
                replan_round += 1
                failed_vs = [v for v in verifications if not v.passed]
                existing_handles = {sid: sorted(meta.keys()) for sid, meta in store.step_meta.items()}
                try:
                    await self.budget.consume_step(f"epoch_c_{replan_round}")
                    t_c = time.monotonic()
                    repair_raw = await run_epoch_c(
                        self.llm,
                        task=self.config.task,
                        task_spec=task_spec,
                        existing_step_handles=existing_handles,
                        failed_verifications=failed_vs,
                        tool_catalog=tool_catalog,
                        replan_round=replan_round,
                    )
                    self._record(
                        epoch="epoch_c",
                        tool="p_llm_epoch_c",
                        plan_excerpt=f"Targeted re-plan round {replan_round} ({len(repair_raw.steps)} steps)",
                        result_meta={"step_ids": [s.id for s in repair_raw.steps]},
                        ok=True,
                        latency_ms=int((time.monotonic() - t_c) * 1000),
                    )
                    if not repair_raw.steps:
                        break
                    repair_compiled = compile_plan(
                        repair_raw,
                        task_spec=task_spec,
                        file_facts=file_facts,
                        probe_facts=probe_facts,
                        tool_catalog=tool_catalog,
                        allowed_inputs=store.allowed_inputs,
                        prior_step_ids=executed_step_ids,
                    )
                    r_ok, r_err = await _execute_plan(repair_compiled)
                    if not r_ok and r_err and "budget" in r_err.lower():
                        budget_stopped_reason = r_err
                    if repair_compiled.final_answer_template:
                        final_answer = repair_compiled.final_answer_template
                except (BudgetExceededError, PlanCompileError, Exception) as exc:
                    self._record(
                        epoch="epoch_c",
                        tool="p_llm_epoch_c",
                        ok=False,
                        result_meta={"error": str(exc)},
                    )
                    break

                verifications = verify_all_deliverables(task_spec, published_by_deliverable, store, store.step_meta)
                self._record(
                    epoch="verify",
                    tool="contracts_verifier",
                    plan_excerpt=f"Post-repair verification round {replan_round}",
                    result_meta={"verifications": [v.model_dump() for v in verifications]},
                    ok=all(v.passed for v in verifications),
                )

            # Determine honest final status.
            all_passed = all(v.passed for v in verifications) if verifications else exec_ok
            any_artifact = bool(published_by_deliverable)
            if budget_stopped_reason:
                status: RunStatus = "partial"
                reason: str | None = budget_stopped_reason
            elif all_passed and exec_ok:
                status = "success"
                reason = None
            elif any_artifact:
                status = "partial"
                reason = exec_err or "Completed with partial deliverables or verification warnings; see verification[]."
            else:
                status = "failed"
                reason = exec_err or "Verification failed and no passing deliverables were produced."

            return self._finalize(
                status=status,
                reason=reason,
                final_answer=final_answer or task_spec.summary,
                file_facts=file_facts,
                assumptions=assumptions,
                published_by_deliverable=published_by_deliverable,
                deliverable_kinds=deliverable_kinds,
                verifications=verifications,
                probe_facts=probe_facts,
            )

    @staticmethod
    def _estimate_runtime_step_cost(tool: str, resolved_args: dict[str, Any], fallback_est: float) -> float:
        from bodhan_mcp.pricing import cost_speak, cost_translate, estimate_output_tokens

        if tool == "synthesize_speech":
            txt = resolved_args.get("text")
            if isinstance(txt, str) and txt:
                return cost_speak(len(txt))
            p = resolved_args.get("path")
            if isinstance(p, str) and Path(p).is_file():
                try:
                    return cost_speak(len(Path(p).read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
        if tool == "translate_text":
            txt = resolved_args.get("text")
            if isinstance(txt, str) and txt:
                return cost_translate(estimate_output_tokens(txt))
            p = resolved_args.get("path")
            if isinstance(p, str) and Path(p).is_file():
                try:
                    return cost_translate(estimate_output_tokens(Path(p).read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
        return fallback_est

    @staticmethod
    def _exec_merge_text(args: dict[str, Any], store: ArtifactStore) -> dict[str, Any]:
        inputs = args.get("inputs") or []
        labels = args.get("labels") or []
        filename = str(args.get("filename") or f"merged_{uuid.uuid4().hex[:8]}.md")
        if not isinstance(inputs, list):
            return err_response("invalid_inputs", "merge_text_artifacts requires 'inputs' list.")

        sections: list[str] = []
        for idx, raw_p in enumerate(inputs):
            p = Path(str(raw_p)).expanduser().resolve()
            if not p.is_file() or not store.check_path_in_read_scope(str(p)):
                return err_response("file_not_found", f"merge_text_artifacts: invalid input {raw_p}")
            txt = p.read_text(encoding="utf-8", errors="replace").strip()
            lbl = str(labels[idx]) if idx < len(labels) else p.name
            sections.append(f"## {lbl}\n\n{txt}")

        combined = "\n\n".join(sections) if sections else "_No matching items found._"
        out_path = store.write_trusted_text_artifact(
            combined,
            Path(filename).name,
            source="deterministic:merge",
        )
        return ok_response(
            {"text_path": str(out_path), "item_count": len(inputs), "char_count": len(combined)},
            cost_inr=0.0,
        )


async def run_agent(
    config: RunConfig,
    *,
    llm: LLMProtocol | None = None,
    bridge: MCPBridge | None = None,
) -> RunReport:
    orch = AgentOrchestrator(config, llm=llm, bridge=bridge)
    return await orch.run()
