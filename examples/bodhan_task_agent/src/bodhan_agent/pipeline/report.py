from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bodhan_agent.agent.progress import MARKERS, PLAN_JSON
from bodhan_agent.core import guards
from bodhan_agent.core.budget import BudgetManager
from bodhan_agent.core.config import RunSettings
from bodhan_agent.core.trace import TraceWriter
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.loop import LoopResult
from bodhan_agent.schemas.plan import Deliverable, Plan, PlanOutcome
from bodhan_agent.schemas.preflight import PreflightResult
from bodhan_agent.schemas.progress import PlanView
from bodhan_agent.schemas.report import (
    ARTIFACTS_DIR,
    USABLE_DELIVERABLE,
    Budgets,
    Compliance,
    CostSummary,
    CountUsage,
    DeliverableReport,
    MissedTarget,
    RunReport,
    StopReason,
    Usage,
    VerificationCheck,
    status_for,
)
from bodhan_agent.schemas.trace import LlmTokens, TraceError
from bodhan_agent.schemas.verify import Verdict
from bodhan_agent.tools.base import file_digest
from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.utils.files import write_bytes_atomic

REPORT_NAME = "report.json"
RESULT_NAME = "result.md"
MAX_COPY_ATTEMPTS = 50
MISSED_PREFIXES: tuple[str, ...] = (
    "task_completion",
    "honest_failure",
    "verification",
    "no_waste",
    "budget",
    "latency",
)
NO_ANSWER = "No English answer was produced."
WITHHELD_ANSWER = "The proposed answer was withheld because it failed verification."


def inr(value: float) -> str:
    return f"Rs {value:.2f}" if value == 0 or abs(value) >= 0.1 else f"Rs {value:.4f}"


def _add_tokens(left: LlmTokens, right: LlmTokens) -> LlmTokens:
    def add(a: int | None, b: int | None) -> int | None:
        return b if a is None else a if b is None else a + b

    return LlmTokens(prompt=add(left.prompt, right.prompt), completion=add(left.completion, right.completion))


def _unique_destination(folder: Path, name: str) -> Path:
    candidate = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    for number in range(2, MAX_COPY_ATTEMPTS + 2):
        if not candidate.exists():
            return candidate
        candidate = folder / f"{stem}-{number}{suffix}"
    raise FileExistsError(f"no free name for {name} in {folder}")


def copy_deliverable(settings: RunSettings, artifact: Artifact) -> tuple[str | None, VerificationCheck]:
    source = Path(artifact.path).resolve()
    folder = settings.artifacts_dir.resolve()
    try:
        if source.parent == folder:
            destination = source
        else:
            folder.mkdir(parents=True, exist_ok=True)
            destination = write_bytes_atomic(
                _unique_destination(folder, source.name), source.read_bytes(), overwrite=False
            )
        digest, _ = file_digest(destination)
    except (OSError, BodhanToolError) as exc:
        return None, VerificationCheck(name="copied", status="failed", detail=f"Could not copy: {exc}")
    if artifact.sha256 is not None and digest != artifact.sha256:
        return None, VerificationCheck(name="copied", status="failed", detail="The copy does not match the file.")
    relative = f"{ARTIFACTS_DIR}/{destination.name}"
    return relative, VerificationCheck(name="copied", status="passed", detail=relative)


def deliverable_report(
    settings: RunSettings, ledger: Ledger, item: Deliverable, artifact_id: str | None, verdict: Verdict | None
) -> DeliverableReport:
    verdict = verdict or Verdict(status="missing")
    artifact = ledger.get(artifact_id) if artifact_id else None
    checks = list(verdict.checks)
    status = verdict.status
    path = None
    if item.kind != "answer" and status in USABLE_DELIVERABLE:
        if artifact is None:
            status, checks = "failed", [*checks, VerificationCheck(name="copied", status="failed", detail="No file.")]
        else:
            path, check = copy_deliverable(settings, artifact)
            checks.append(check)
            if path is None:
                status = "failed"
    if item.kind != "answer" and status == "missing":
        artifact = None
    return DeliverableReport(
        id=item.id,
        kind=item.kind,
        language=item.language,
        format=item.format,
        status=status,
        path=path,
        artifact_id=None if artifact is None else artifact.id,
        producer=None if artifact is None else artifact.producer,
        tool=None if artifact is None else artifact.tool,
        label=verdict.label if status in USABLE_DELIVERABLE else None,
        checks=checks,
    )


SINGLE_STEP_TARGET_S = 30.0
MULTI_STEP_TARGET_S = 300.0


def _missed(loop: LoopResult | None, cost: Usage, time: Usage, work_steps: int) -> list[MissedTarget]:
    missed: list[MissedTarget] = []
    for entry in loop.missed_targets if loop is not None else []:
        prefix, _, detail = entry.partition(":")
        target = prefix.strip() if prefix.strip() in MISSED_PREFIXES else "no_waste"
        missed.append(MissedTarget(target=target, detail=(detail or entry).strip() or entry))  # type: ignore[arg-type]
    if cost.exceeded:
        missed.append(MissedTarget(target="budget", detail=f"Bodhan cost {inr(cost.used)} exceeded the limit."))
    single = work_steps <= 1
    limit = SINGLE_STEP_TARGET_S if single else MULTI_STEP_TARGET_S
    if time.used > limit:
        kind = "single-step" if single else "multi-step"
        detail = f"This {kind} task took {time.used:.0f} s; the target is under {limit:.0f} s."
        missed.append(MissedTarget(target="latency", detail=detail))
    return missed


def _explanation(deliverables: list[DeliverableReport], stop_reason: StopReason, given: str | None) -> str | None:
    parts = [given] if given else []
    for item in deliverables:
        if item.status == "missing":
            parts.append(f"{item.id} ({item.kind}) was not produced.")
        elif item.status == "failed":
            failed = [check.name for check in item.checks if check.status == "failed"]
            parts.append(f"{item.id} ({item.kind}) failed verification: {', '.join(failed) or 'unknown reason'}.")
    if not parts and stop_reason != "finished":
        parts.append(f"The run stopped early: {stop_reason}.")
    return " ".join(parts) or None


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


class Finalizer:
    def __init__(
        self,
        settings: RunSettings,
        ledger: Ledger,
        budget: BudgetManager,
        trace: TraceWriter | None,
        started_at: datetime,
        elapsed_s: float,
        preflight: PreflightResult | None = None,
        outcome: PlanOutcome | None = None,
        loop: LoopResult | None = None,
        stop_reason: StopReason | None = None,
        explanation: str | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.budget = budget
        self.trace = trace
        self.started_at = started_at
        self.elapsed_s = elapsed_s
        self.preflight = preflight
        self.outcome = outcome
        self.loop = loop
        self.stop_reason = stop_reason
        self.explanation = explanation

    @property
    def plan(self) -> Plan | None:
        return None if self.outcome is None else self.outcome.plan

    def stop(self) -> StopReason:
        if self.stop_reason is not None:
            return self.stop_reason
        if self.loop is not None:
            return self.loop.stop_reason
        if self.outcome is not None and self.outcome.gate.stop_reason is not None:
            return self.outcome.gate.stop_reason
        return "internal_error"

    def given_explanation(self) -> str | None:
        if self.explanation:
            return self.explanation
        if self.loop is not None and self.loop.explanation:
            return self.loop.explanation
        if self.outcome is not None and not self.outcome.gate.feasible:
            return self.outcome.gate.explanation
        return None

    def deliverables(self) -> list[DeliverableReport]:
        if self.plan is None:
            return []
        mapping = self.loop.deliverables if self.loop is not None else {}
        verdicts = self.loop.verdicts if self.loop is not None else {}
        return [
            deliverable_report(self.settings, self.ledger, item, mapping.get(item.id), verdicts.get(item.id))
            for item in self.plan.deliverables
        ]

    def budgets(self, cost: CostSummary) -> Budgets:
        steps = self.loop.steps_used if self.loop is not None else 0
        turns = self.loop.llm_turns if self.loop is not None else 0
        return Budgets(
            cost_inr=Usage(used=round(cost.exposure_inr, 6), limit=self.budget.limit_inr),
            steps=CountUsage(used=steps, limit=self.settings.max_steps),
            llm_turns=CountUsage(used=turns, limit=self.settings.llm_turn_limit),
            time_s=Usage(used=round(self.elapsed_s, 3), limit=self.settings.deadline_s),
        )

    def cost(self) -> CostSummary:
        tokens = LlmTokens()
        if self.outcome is not None:
            tokens = _add_tokens(tokens, self.outcome.llm_tokens)
        if self.loop is not None:
            tokens = _add_tokens(tokens, self.loop.llm_tokens)
        return CostSummary(
            bodhan_inr=self.budget.spent_inr,
            reserved_unsettled_inr=self.budget.reserved_inr,
            billing_uncertain=self.budget.billing_uncertain or bool(self.budget.open_reservations),
            possibly_billed_failed_attempts=self.budget.possibly_billed_failed_attempts,
            cache_hits=self.budget.cache_hits,
            llm_tokens=tokens,
            llm_usage_complete=tokens.total is not None and self.usage_reported(),
        )

    def plan_lines(self) -> list[str]:
        try:
            view = PlanView.model_validate_json((self.settings.out_dir / PLAN_JSON).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        lines = [f"Goal: {view.goal}", ""]
        for step in view.steps:
            tool = f" `{step.tool}`" if step.tool else ""
            note = f" — {step.note}" if step.note else ""
            lines.append(f"- {MARKERS[step.status]} {step.id}{tool}: {step.intent}{note}")
        if view.deviations:
            lines.append(f"- {len(view.deviations)} call(s) outside the plan")
        lines += ["", "Full plan with reasons, checks and risks: [plan.md](plan.md)"]
        return lines

    def work_steps(self) -> int:
        return sum(1 for action in self.ledger.actions if action.tool != "finish")

    def steps_lines(self) -> list[str]:
        lines = []
        for action in self.ledger.actions:
            if action.tool == "finish":
                continue
            outcome = "ok" if action.outcome == "ok" else f"{action.outcome} ({action.error_code})"
            outputs = f" -> {', '.join(action.artifacts)}" if action.artifacts else ""
            cost = f", {inr(action.cost_inr)}" if action.cost_inr else ""
            lines.append(f"{len(lines) + 1}. {action.tool}: {outcome}{outputs}{cost}")
        return lines

    def usage_reported(self) -> bool:
        phases = [(self.outcome.llm_turns, self.outcome.llm_tokens)] if self.outcome is not None else []
        if self.loop is not None:
            phases.append((self.loop.llm_turns, self.loop.llm_tokens))
        return all(tokens.total is not None for turns, tokens in phases if turns > 0)

    def compliance(self) -> Compliance:
        loop = self.loop
        blocked = (self.outcome.blocked_llm_outputs if self.outcome is not None else 0) + (
            loop.blocked_llm_outputs if loop is not None else 0
        )
        uncertain = sum(1 for entry in (loop.missed_targets if loop else []) if "uncertain" in entry)
        return Compliance(
            indic_redactions=loop.redactions if loop is not None else 0,
            blocked_llm_outputs=blocked,
            injection_flags=loop.injection_flags if loop is not None else 0,
            uncertain_retries=uncertain,
        )

    def assumptions(self) -> list[str]:
        items = list(self.plan.assumptions) if self.plan is not None else []
        if self.loop is not None and self.loop.finish is not None:
            items.extend(self.loop.finish.assumptions)
        return _unique(items)

    def warnings(self, deliverables: list[DeliverableReport]) -> list[str]:
        items: list[str] = []
        if self.preflight is not None:
            items.extend(issue.message for issue in self.preflight.issues)
        for report in deliverables:
            artifact = self.ledger.get(report.artifact_id) if report.artifact_id else None
            if artifact is not None:
                items.extend(f"{report.id}: {warning}" for warning in artifact.warnings)
        if self.budget.open_reservations:
            items.append("Some Bodhan calls ended without a known cost; their full reservation is counted.")
        return _unique(items)

    def build(self) -> RunReport:
        deliverables = self.deliverables()
        stop_reason = self.stop()
        cost = self.cost()
        budgets = self.budgets(cost)
        status = status_for(deliverables, stop_reason)
        explanation = _explanation(deliverables, stop_reason, self.given_explanation())
        return RunReport(
            status=status,
            stop_reason=stop_reason,
            explanation=explanation if status != "completed" or explanation else None,
            task=self.settings.task,
            task_en=None if self.outcome is None else self.outcome.task_en,
            assumptions=self.assumptions(),
            planned=[item.id for item in deliverables],
            deliverables=deliverables,
            budgets=budgets,
            cost=cost,
            started_at=self.started_at,
            finished_at=datetime.now(timezone.utc),
            missed_targets=_missed(self.loop, budgets.cost_inr, budgets.time_s, self.work_steps()),
            compliance=self.compliance(),
            warnings=self.warnings(deliverables),
        )

    def fallback(self, error: Exception) -> RunReport:
        cost = self.cost()
        planned = [item.id for item in self.plan.deliverables] if self.plan is not None else []
        missing = []
        if self.plan is not None:
            missing = [
                DeliverableReport(id=item.id, kind=item.kind, language=item.language, format=item.format,
                                  status="missing")
                for item in self.plan.deliverables
            ]  # fmt: skip
        return RunReport(
            status="failed",
            stop_reason="internal_error",
            explanation=f"The report could not be built: {type(error).__name__}: {error}",
            task=self.settings.task,
            task_en=None if self.outcome is None else self.outcome.task_en,
            planned=planned,
            deliverables=missing,
            budgets=self.budgets(cost),
            cost=cost,
            started_at=self.started_at,
            finished_at=datetime.now(timezone.utc),
        )

    def answer(self, report: RunReport) -> str:
        finish = self.loop.finish if self.loop is not None else None
        if finish is None or not finish.answer_en.strip():
            return NO_ANSWER
        answers = [item for item in report.deliverables if item.kind == "answer"]
        if any(item.status == "failed" for item in answers) or guards.llm_text_problem(finish.answer_en):
            return WITHHELD_ANSWER
        return finish.answer_en.strip()

    def result_markdown(self, report: RunReport) -> str:
        lines = [f"# Result: {report.status}", "", self.answer(report), ""]
        if report.explanation:
            lines += ["## What did not work", "", report.explanation, ""]
        lines += ["## Deliverables", ""]
        if not report.deliverables:
            lines.append("- None: the run stopped before a plan was made.")
        for item in report.deliverables:
            where = f" — [{item.path}]({item.path})" if item.path else ""
            label = f" — {item.label}" if item.label else ""
            lines.append(
                f"- **{item.id}** ({item.kind}, {item.language or 'any language'}): {item.status}{where}{label}"
            )
            for check in item.checks:
                if check.status in ("warning", "failed"):
                    lines.append(f"  - {check.status}: {check.name}" + (f" — {check.detail}" if check.detail else ""))
        lines.append("")
        plan = self.plan_lines()
        if plan:
            lines += ["## Plan", "", *plan, ""]
        steps = self.steps_lines()
        if steps:
            lines += ["## What was done", "", *steps, ""]
        if report.assumptions:
            lines += ["## Assumptions", "", *[f"- {item}" for item in report.assumptions], ""]
        notes = list(report.warnings)
        if report.compliance.injection_flags:
            notes.append(
                f"{report.compliance.injection_flags} instruction-like line(s) were found in input content and "
                "treated as data, not instructions."
            )
        notes.extend(f"{item.target}: {item.detail}" for item in report.missed_targets)
        if notes:
            lines += ["## Warnings", "", *[f"- {item}" for item in _unique(notes)], ""]
        cost = report.cost
        tokens = cost.llm_tokens
        token_text = (
            f"{tokens.prompt} prompt + {tokens.completion} completion tokens"
            if tokens.total is not None
            else "not reported by the provider"
        )
        if tokens.total is not None and not cost.llm_usage_complete:
            token_text += " (incomplete: some calls did not report usage)"
        lines += [
            "## Cost and time",
            "",
            f"- Bodhan cost: {inr(cost.bodhan_inr)} of {inr(report.budgets.cost_inr.limit or 0)}"
            + (f" (plus {inr(cost.reserved_unsettled_inr)} unsettled)" if cost.reserved_unsettled_inr else "")
            + (" — some charges are uncertain" if cost.billing_uncertain else ""),
            f"- LLM usage (not part of the Bodhan budget): {token_text}",
            f"- Steps: {report.budgets.steps.used} of {report.budgets.steps.limit}; "
            f"time: {report.budgets.time_s.used:.0f} s of {report.budgets.time_s.limit or 0:.0f} s",
            f"- Stop reason: {report.stop_reason}",
            "",
            "Speech, transcription, translation and OCR were done by Bodhan models; the LLM only planned and wrote "
            "English.",
            "",
        ]
        text = "\n".join(lines)
        redacted, _ = guards.redact_text(text)
        return redacted

    async def write(self) -> RunReport:
        try:
            report = self.build()
        except (ValidationError, ValueError, OSError, KeyError, TypeError) as exc:
            report = self.fallback(exc)
            if self.trace is not None:
                await self.trace.emit(
                    "finalize", "stop", message="Report fell back to internal_error.",
                    error=TraceError(code="internal_error", message=str(exc)),
                )  # fmt: skip
        out = self.settings.out_dir
        out.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(out / REPORT_NAME, (report.to_json() + "\n").encode("utf-8"), overwrite=True)
        write_bytes_atomic(out / RESULT_NAME, self.result_markdown(report).encode("utf-8"), overwrite=True)
        if self.trace is not None:
            await self.trace.emit(
                "finalize", "stop", message=f"status={report.status}, stop_reason={report.stop_reason}",
                artifacts=[item.artifact_id for item in report.deliverables if item.artifact_id],
                result_summary=self.summary(report),
            )  # fmt: skip
        return report

    @staticmethod
    def summary(report: RunReport) -> dict[str, Any]:
        return {
            "status": report.status,
            "deliverables": {item.id: item.status for item in report.deliverables},
            "bodhan_inr": report.cost.bodhan_inr,
        }
