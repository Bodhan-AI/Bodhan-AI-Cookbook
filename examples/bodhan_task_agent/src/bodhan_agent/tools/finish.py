from __future__ import annotations

from pathlib import Path
from typing import Any

from bodhan_agent.core import guards
from bodhan_agent.schemas.ledger import Artifact
from bodhan_agent.schemas.plan import Deliverable
from bodhan_agent.schemas.tools import DeliverableRef, FinishInput, LocalResult
from bodhan_agent.tools.base import LocalTool, LocalToolError, ToolContext, known_file


def _base(code: str) -> str:
    return code.split("-", 1)[0].lower()


def _text_problem(field: str, text: str) -> str | None:
    problem = guards.llm_text_problem(text)
    return None if problem is None else f"{field} {problem[1]}"


def _planned(ctx: ToolContext) -> dict[str, Deliverable] | None:
    return None if ctx.plan is None else {item.id: item for item in ctx.plan.deliverables}


def _check_file(
    ctx: ToolContext, ref: DeliverableRef, planned: Deliverable | None
) -> tuple[Artifact | None, str | None]:
    if planned is not None and planned.kind == "answer":
        if ref.path:
            return None, f"{ref.id} is an answer in result.md; give no path for it."
        return None, None
    if not ref.path:
        return None, f"{ref.id} needs the path of the file that fulfils it."
    try:
        artifact = known_file(ctx, ref.path)
    except LocalToolError as error:
        return None, f"{ref.id}: {error.message}"
    if planned is not None:
        if artifact.kind != planned.kind:
            return None, f"{ref.id} needs a {planned.kind} file, but {artifact.id} is {artifact.kind}."
        if planned.format and Path(artifact.path).suffix.lower().lstrip(".") != planned.format:
            return None, f"{ref.id} needs a .{planned.format} file, but {artifact.id} is {Path(artifact.path).name}."
        if planned.language and artifact.language and _base(planned.language) != _base(artifact.language):
            return None, f"{ref.id} must be in '{planned.language}', but {artifact.id} is in '{artifact.language}'."
    language = planned.language if planned is not None and planned.language else artifact.language
    source = guards.deliverable_source_problem(ctx.ledger, artifact, language)
    return (None, source) if source is not None else (artifact, None)


class FinishTool(LocalTool):
    name = "finish"
    description = (
        "Propose that the task is complete. Give the final answer in English, the assumptions you made, and for "
        "every planned deliverable the file from this run that fulfils it (no path for an 'answer' deliverable). "
        "Code then verifies every deliverable and decides the run's status; problems found now come back to you "
        "to fix. Planned deliverables you leave out are reported as missing."
    )
    Input = FinishInput

    async def run(self, params: FinishInput, ctx: ToolContext) -> LocalResult:
        problems = [
            problem
            for field, text in [
                ("answer_en", params.answer_en),
                *(("assumptions", item) for item in params.assumptions),
            ]
            if (problem := _text_problem(field, text)) is not None
        ]
        planned = _planned(ctx)
        accepted: list[dict[str, Any]] = []
        for ref in params.deliverables:
            deliverable = None if planned is None else planned.get(ref.id)
            if planned is not None and deliverable is None:
                known = ", ".join(planned) or "none"
                problems.append(f"{ref.id} is not a planned deliverable (planned: {known}).")
                continue
            artifact, problem = _check_file(ctx, ref, deliverable)
            if problem is not None:
                problems.append(problem)
                continue
            entry: dict[str, Any] = {"id": ref.id}
            if artifact is not None:
                entry.update(artifact=artifact.id, path=artifact.path)
            accepted.append(entry)
        if problems:
            raise LocalToolError(
                "finish_rejected",
                "The finish proposal has problems: " + " ".join(problems),
                hint="Fix them and call finish again, or keep working.",
                details={"problems": problems},
            )
        given = {ref.id for ref in params.deliverables}
        missing = [] if planned is None else [item for item in planned if item not in given]
        data: dict[str, Any] = {
            "accepted": True,
            "deliverables": accepted,
            "missing": missing,
            "assumptions": len(params.assumptions),
            "next": "Final verification by code; no further tool calls are needed.",
        }
        return LocalResult(data=data, artifact_ids=[entry["artifact"] for entry in accepted if "artifact" in entry])
