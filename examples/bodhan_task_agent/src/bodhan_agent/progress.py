from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from bodhan_agent.config import RunSettings
from bodhan_agent.schemas.budget import MinimumCost
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.plan import Deliverable, Plan
from bodhan_agent.schemas.progress import DeliverableView, Deviation, Estimate, PlanView, StepProgress
from bodhan_agent.verify import (
    DURATION_TOLERANCE,
    LENGTH_RATIO_RANGE,
    SCRIPT_WARN_SHARE,
    SPEECH_COVERAGE_MIN,
    WORD_RECALL_MIN,
)
from bodhan_mcp.utils.files import write_bytes_atomic

PLAN_MD = "plan.md"
PLAN_JSON = "plan.json"
MARKERS: dict[str, str] = {"pending": "[ ]", "in_progress": "[>]", "done": "[x]", "failed": "[!]", "skipped": "[-]"}
OPEN_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "failed"})


def deliverable_checks(item: Deliverable) -> list[str]:
    constraints = item.constraints
    if item.kind == "audio":
        checks = [
            f"Back-transcription finds at least {WORD_RECALL_MIN:.0%} of the words (one re-synthesis if not)",
            "The audio has a duration and carries the AI-generated speech label",
        ]
        if constraints.duration_s is not None:
            checks.append(f"Length within ±{DURATION_TOLERANCE:.0%} of {constraints.duration_s:.0f} s")
        if constraints.voice_gender is not None:
            checks.append(f"A {constraints.voice_gender} voice")
        return checks
    if item.kind == "subtitles":
        return [
            "The SRT parses and its cues are ordered and do not overlap",
            f"Cues cover at least {SPEECH_COVERAGE_MIN:.0%} of the audio",
            f"At least {SCRIPT_WARN_SHARE:.0%} of letters in the target language's script",
        ]
    if item.kind == "answer":
        return ["Written in English", "Mentions only files produced in this run"]
    low, high = LENGTH_RATIO_RANGE
    checks = [
        f"At least {SCRIPT_WARN_SHARE:.0%} of letters in the target language's script",
        "Bodhan's structural translation checks passed",
        f"Length between {low}x and {high}x the source",
    ]
    if constraints.keep_tables:
        checks.append("Every table keeps its rows and columns")
    return checks


def input_fact(artifact: Artifact) -> str:
    parts = [artifact.kind]
    if artifact.pages is not None:
        parts.append(f"{artifact.pages} page(s)")
    if artifact.duration_s is not None:
        parts.append(f"{artifact.duration_s:.1f} s")
    if artifact.script:
        parts.append(f"{artifact.script} script")
    if artifact.language:
        parts.append(f"language {artifact.language}")
    if artifact.indic_text:
        parts.append("Indian-language text")
    if not artifact.usable:
        parts.append(f"not usable: {artifact.note or artifact.step_status}")
    return f"{artifact.id} {Path(artifact.path).name}: {', '.join(parts)}"


class PlanTracker:
    def __init__(
        self,
        settings: RunSettings,
        ledger: Ledger,
        plan: Plan,
        input_ids: Iterable[str] = (),
        minimum: MinimumCost | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.plan = plan
        self.input_ids = list(input_ids)
        self.revision = 0
        self.finished = False
        self.steps = [
            StepProgress(
                id=step.id, intent=step.intent, why=step.why, tool=step.tool, condition=step.condition,
                depends_on=list(step.depends_on),
            )
            for step in plan.steps
        ]  # fmt: skip
        self.deviations: list[Deviation] = []
        self.states: dict[str, str] = {}
        self.estimate = (
            None
            if minimum is None
            else Estimate(
                known_cost_inr=minimum.known_inr, unknown=list(minimum.unknown), budget_inr=settings.max_cost_inr
            )
        )

    def step(self, step_id: str | None) -> StepProgress | None:
        return next((item for item in self.steps if item.id == step_id), None) if step_id else None

    def match(self, tool: str, plan_step: str | None) -> StepProgress | None:
        named = self.step(plan_step)
        if named is not None:
            return named
        open_steps = [item for item in self.steps if item.tool == tool and item.status in OPEN_STATUSES]
        if not open_steps:
            return None
        done = {item.id for item in self.steps if item.status == "done"}

        def rank(item: StepProgress) -> tuple[int, int]:
            ready = all(dependency in done for dependency in item.depends_on)
            return (0 if ready else 1, 0 if item.condition is None else 1)

        return min(open_steps, key=rank)

    def begin(self, tool: str, plan_step: str | None) -> list[str]:
        step = self.match(tool, plan_step)
        if step is None or step.status == "in_progress":
            return []
        step.status = "in_progress"
        self.revision += 1
        return [step.id]

    def record(
        self,
        tool: str,
        plan_step: str | None,
        seq: int,
        ok: bool,
        outputs: Iterable[str] = (),
        reason: str | None = None,
        error_code: str | None = None,
    ) -> list[str]:
        step = self.match(tool, plan_step)
        self.revision += 1
        if step is None:
            outcome = "ok" if ok else f"error {error_code}"
            self.deviations.append(Deviation(seq=seq, tool=tool, reason=reason, outcome=outcome))
            return []
        step.calls.append(seq)
        if ok:
            step.status = "done"
            step.outputs.extend(item for item in outputs if item not in step.outputs)
            step.note = None
        else:
            step.status = "failed" if step.status != "done" else "done"
            step.note = f"{tool} failed: {error_code}"
        return [step.id]

    def set_states(self, states: Mapping[str, str]) -> bool:
        if dict(states) == self.states:
            return False
        self.states = dict(states)
        self.revision += 1
        return True

    def close(self) -> list[str]:
        changed = []
        for step in self.steps:
            if step.status in ("pending", "in_progress"):
                step.status = "skipped"
                step.note = "condition not met" if step.condition else "not run"
                changed.append(step.id)
        self.finished = True
        self.revision += 1
        return changed

    def view(self) -> PlanView:
        inputs = [artifact for artifact_id in self.input_ids if (artifact := self.ledger.get(artifact_id))]
        return PlanView(
            revision=self.revision,
            task_en=self.plan.task_en,
            goal=self.plan.goal,
            input_facts=[input_fact(artifact) for artifact in inputs],
            input_notes=list(self.plan.input_notes),
            assumptions=list(self.plan.assumptions),
            deliverables=[
                DeliverableView(
                    id=item.id,
                    kind=item.kind,
                    language=item.language,
                    format=item.format,
                    description=item.description,
                    constraints=item.constraints.model_dump(exclude_defaults=True),
                    checks=deliverable_checks(item),
                    state=self.states.get(item.id, "not started"),
                )
                for item in self.plan.deliverables
            ],  # fmt: skip
            steps=[step.model_copy(deep=True) for step in self.steps],
            risks=[{"risk": item.risk, "mitigation": item.mitigation} for item in self.plan.risks],
            estimate=self.estimate,
            deviations=list(self.deviations),
            finished=self.finished,
        )

    def write(self) -> PlanView:
        view = self.view()
        out = self.settings.out_dir
        out.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(out / PLAN_JSON, (view.model_dump_json(indent=2) + "\n").encode("utf-8"), overwrite=True)
        write_bytes_atomic(out / PLAN_MD, render_markdown(view).encode("utf-8"), overwrite=True)
        return view


def _step_line(step: StepProgress) -> str:
    tool = f" `{step.tool}`" if step.tool else ""
    condition = f" ({step.condition})" if step.condition else ""
    after = f" after {', '.join(step.depends_on)}" if step.depends_on else ""
    lines = [f"- {MARKERS[step.status]} **{step.id}**{tool}: {step.intent}{condition}{after}", f"  - why: {step.why}"]
    if step.outputs:
        lines.append(f"  - produced: {', '.join(step.outputs)}")
    if step.note:
        lines.append(f"  - note: {step.note}")
    return "\n".join(lines)


def render_markdown(view: PlanView) -> str:
    title = "Plan (final)" if view.finished else f"Plan (revision {view.revision})"
    lines = [f"# {title}", "", f"**Goal.** {view.goal}", "", f"**Task.** {view.task_en}", ""]
    if view.input_facts or view.input_notes:
        lines += ["## Inputs", ""]
        lines += [f"- {fact}" for fact in view.input_facts]
        lines += [f"- Note: {note}" for note in view.input_notes]
        lines.append("")
    lines += ["## Steps", "", "`[x]` done · `[>]` in progress · `[ ]` pending · `[!]` failed · `[-]` skipped", ""]
    lines += [_step_line(step) for step in view.steps] or ["- No steps were planned."]
    lines.append("")
    if view.deviations:
        lines += ["## Calls outside the plan", ""]
        lines += [
            f"- seq {item.seq} `{item.tool}`: {item.outcome}" + (f" ({item.reason})" if item.reason else "")
            for item in view.deviations
        ]
        lines.append("")
    lines += ["## Deliverables and checks", ""]
    for item in view.deliverables:
        language = item.language or "any language"
        lines.append(f"- **{item.id}** {item.kind} ({language}): {item.description} — {item.state}")
        lines += [f"  - check: {check}" for check in item.checks]
    lines.append("")
    if view.risks:
        lines += ["## Risks", "", *[f"- {item['risk']} → {item['mitigation']}" for item in view.risks], ""]
    if view.assumptions:
        lines += ["## Assumptions", "", *[f"- {item}" for item in view.assumptions], ""]
    if view.estimate is not None:
        estimate = view.estimate
        lines += [
            "## Estimate",
            "",
            f"- Known Bodhan cost: Rs {estimate.known_cost_inr:.2f} of the Rs {estimate.budget_inr:.2f} budget",
            *[f"- Not yet priced: {item}" for item in estimate.unknown],
            "",
        ]
    return "\n".join(lines)
