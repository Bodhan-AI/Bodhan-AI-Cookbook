"""Run a task from locally scoped file references and record its outcome."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
import json
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol
import unicodedata

from mcp.server.fastmcp.exceptions import ToolError

from bodhan_mcp.ocr import extract_document_text
from bodhan_mcp.request_budget import BudgetExhausted, RequestBudget, active_budget
from bodhan_mcp.server import translate_text
from bodhan_mcp.speech import synthesize_speech
from bodhan_mcp.transcription import transcribe_audio

from .planner import Action, Finish, PlannerError, Read, Save, Speak, Transcribe, Translate, TranslationReview

MAX_TEXT_BYTES = 16_000
PREVIEW_CHARS = 320


class Planner(Protocol):
    def decide(self, state: dict) -> Action: ...
    def review(self, source: str, translation: str, language: str) -> TranslationReview: ...


def _normalized(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFC", text).casefold()
        if unicodedata.category(char)[0] in "LMN"
    )


def _inputs(paths: list[Path]) -> dict[str, Path]:
    files = []
    for path in paths:
        if not path.exists():
            raise ToolError(f"Input does not exist: {path.name}")
        if not path.is_dir():
            files.append(path.resolve())
            continue
        root = path.resolve()
        files.extend(
            item.resolve()
            for item in sorted(root.iterdir())
            if not item.name.startswith(".") and item.is_file() and item.resolve().is_relative_to(root)
        )
    return {f"input-{i}": path for i, path in enumerate(dict.fromkeys(files), start=1)}


@dataclass
class StepTrace:
    number: int
    verification_seconds: float = 0.0
    verification_requests: set[int] = field(default_factory=set)


def _request_summary(requests: list[dict]) -> dict:
    return {
        "provider_requests": requests,
        "cost_inr": sum((request["cost_inr"] for request in requests), Decimal(0)),
    }


@dataclass
class Run:
    output: Path
    budget: RequestBudget
    texts: dict[str, str] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    verification: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

    def record(self, event: str, **fields: object) -> None:
        with (self.output / "trace.jsonl").open("a", encoding="utf-8") as trace:
            trace.write(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str) + "\n")

    def remember(self, text: str) -> dict:
        if not text.strip():
            raise ToolError("The selected source returned no text.")
        reference = f"text-{len(self.texts) + 1}"
        self.texts[reference] = text
        return {"text_id": reference, "characters": len(text), "preview": text[:PREVIEW_CHARS]}

    @contextmanager
    def verify(self, step: StepTrace, kind: str, **details: object) -> Iterator[dict]:
        started = monotonic()
        request_start = len(self.budget.requests)
        result = {"kind": kind, "passed": False, "step": step.number, **details}
        try:
            yield result
        except (BudgetExhausted, PlannerError, ToolError, OSError) as exc:
            result["explanation"] = (
                "Could not access a local file during verification." if isinstance(exc, OSError) else str(exc)
            )
            raise
        finally:
            elapsed = monotonic() - started
            step.verification_seconds += elapsed
            step.verification_requests.update(range(request_start, len(self.budget.requests)))
            self.verification.append(result)
            self.record("verification", **result, latency_seconds=round(elapsed, 3),
                        **_request_summary(self.budget.requests[request_start:]))


def _execute(run: Run, planner: Planner, inputs: dict[str, Path], action: Action, step: StepTrace) -> dict:
    if isinstance(action, (Read, Transcribe)):
        source = inputs.get(action.source)
        if source is None:
            raise ToolError("Select an input ID from the inventory.")
        if isinstance(action, Transcribe):
            return run.remember(transcribe_audio(str(source), action.language).text)
        if action.action == "ocr":
            pages = extract_document_text(str(source)).pages
            return run.remember("\n\n".join(page.markdown for page in pages))
        if source.suffix.lower() not in {".txt", ".md"}:
            raise ToolError("Read supports UTF-8 .txt and .md files. Use OCR for documents.")
        with source.open("rb") as file:
            data = file.read(MAX_TEXT_BYTES + 1)
        if len(data) > MAX_TEXT_BYTES:
            raise ToolError("Text exceeds this increment's input size limit.")
        return run.remember(data.decode("utf-8"))

    text = run.texts.get(action.source)
    if text is None:
        raise ToolError("Select an available text ID.")
    if isinstance(action, Save):
        path = f"artifacts/{action.source}.md"
        (run.output / path).write_text(text, encoding="utf-8")
        run.artifacts.append(path)
        return {"artifact": path}
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ToolError("Text exceeds this increment's translation or speech input limit.")
    if isinstance(action, Translate):
        result = translate_text(text, action.language)
        with run.verify(step, "translation_coverage", method="planner_review",
                        source=action.source, language=action.language) as check:
            review = planner.review(text, result.translation, action.language)
            check.update(passed=review.approved, explanation=review.explanation)
        if not review.approved:
            raise ToolError("Translation review found a potential completeness or meaning issue.")
        return run.remember(result.translation)

    path = f"artifacts/speech-{len(run.artifacts) + 1}.wav"
    synthesis = synthesize_speech(text, action.language, action.voice, str(run.output / path))
    with run.verify(step, "speech_artifact_created", artifact=path, ai_generated=True) as check:
        run.artifacts.append(path)
        check["passed"] = True
    with run.verify(step, "speech_content_match", method="back_transcription",
                    source=action.source, artifact=path, language=action.language) as check:
        heard = transcribe_audio(synthesis.audio_path, action.language).text
        expected = _normalized(text)
        actual = _normalized(heard)
        similarity = SequenceMatcher(None, expected, actual, autojunk=False).ratio()
        passed = bool(expected and actual and similarity >= 0.85)
        check.update(passed=passed, similarity=round(similarity, 3), threshold=0.85,
                     transcript_characters=len(heard))
    if not passed:
        raise ToolError("Speech read-back diverged from the source. The WAV is available for review.")
    return {"artifact": path, "duration_seconds": synthesis.duration_seconds, "ai_generated": True}


def run_task(task: str, files: list[Path], output: Path, max_steps: int, max_cost: Decimal,
             make_planner: Callable[[], Planner]) -> dict:
    started = monotonic()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Choose a new or empty output directory.")
    (output / "artifacts").mkdir()
    run = Run(output, RequestBudget(max_cost))
    run.record("start", task=task, max_steps=max_steps, max_cost_inr=max_cost)
    token = active_budget.set(run.budget)
    status = "step_limit"
    explanation = "The agent reached its maximum number of steps."
    assumptions: list[str] = []
    step = 0
    seen: set[str] = set()
    try:
        inputs = _inputs(files)
        if not inputs:
            raise ToolError("No input files were found.")
        planner = make_planner()
        for step in range(1, max_steps + 1):
            step_start = monotonic()
            action = planner.decide({
                "task": task,
                "inputs": [{"id": ref, "name": path.name} for ref, path in inputs.items()],
                "observations": run.history,
                "remaining_bodhan_inr": str(max_cost - run.budget.total),
                "remaining_steps_after_this": max_steps - step,
            })
            arguments = action.model_dump(exclude={"reason"})
            run.record("plan", step=step, reason=action.reason, arguments=arguments,
                       latency_seconds=round(monotonic() - step_start, 3))
            if isinstance(action, Finish):
                status, explanation, assumptions = action.status, action.reason, action.assumptions
                if status == "completed" and (
                    not run.artifacts or any(not item["passed"] for item in run.verification)
                ):
                    status = "incomplete"
                    explanation = "The run lacks a deliverable or has an unresolved verification failure."
                break

            request_index = len(run.budget.requests)
            tool_start = monotonic()
            timing = StepTrace(step)
            signature = json.dumps(arguments, sort_keys=True)
            observation = {"error": "The action did not finish."}
            try:
                if signature in seen:
                    raise ToolError("This action was already attempted. Reuse its observation.")
                seen.add(signature)
                observation = _execute(run, planner, inputs, action, timing)
            except BudgetExhausted as exc:
                observation = {"error": str(exc)}
                raise
            except (ToolError, UnicodeError, OSError, PlannerError) as exc:
                observation = {"error": str(exc) if isinstance(exc, (ToolError, PlannerError))
                               else "Could not read or write a local file."}
            finally:
                requests = [
                    request for index, request in enumerate(run.budget.requests[request_index:], request_index)
                    if index not in timing.verification_requests
                ]
                elapsed = monotonic() - tool_start - timing.verification_seconds
                run.record("tool", step=step, arguments=arguments, observation=observation,
                           latency_seconds=round(elapsed, 3), **_request_summary(requests))
            run.history.append({"step": step, "action": arguments, "result": observation})
    except BudgetExhausted as exc:
        status, explanation = "budget_exhausted", str(exc)
    except (ToolError, PlannerError, OSError) as exc:
        status = "failed"
        explanation = str(exc) if not isinstance(exc, OSError) else "Could not read a local input."
    finally:
        active_budget.reset(token)

    report = {
        "status": status, "steps": step, "verification": run.verification,
        "cost_inr": str(run.budget.total), "max_cost_inr": str(max_cost),
        "cost_scope": "Bodhan requests only. Unknown charges use the reserved amount.",
        "elapsed_seconds": round(monotonic() - started, 3),
        "artifacts": run.artifacts, "assumptions": assumptions, "explanation": explanation,
    }
    run.record("summary", **report)
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    links = "\n".join(f"- [{Path(path).name}]({path})" for path in run.artifacts) or "No artifacts were produced."
    assumptions_text = "\n".join(f"- {item}" for item in assumptions) or "None recorded."
    markdown = (
        f"# Task result\n\n**{status}**\n\n{explanation}\n\n"
        f"Bodhan cost estimate: ₹{report['cost_inr']}\n\n"
        f"## Artifacts\n\n{links}\n\n## Assumptions\n\n{assumptions_text}\n\n"
        "See [report.json](report.json) for verification and [trace.jsonl](trace.jsonl) for the execution trace.\n"
    )
    (output / "result.md").write_text(markdown, encoding="utf-8")
    return report
