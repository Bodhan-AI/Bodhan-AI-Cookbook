"""Task agent: plan, act, verify, retry, stop.

The agent runs unattended from the command line:

    python -m bodhan_agent --task "..." --files path/to/inputs/ --out ./out

It imports the same tool functions the MCP server exposes, so the agent
and the server always behave identically. The LLM (OpenAI-compatible) is
the planner; deterministic code does what is better done in code
(media inspection, SRT formatting, cost accounting, verification checks).

Budget enforcement: every tool call is costed before it runs. If a call
would push the run over --max-cost-inr, the agent stops cleanly and
reports what was done. Steps are capped by --max-steps. A verification
failure triggers one retry, then an honest report.

Untrusted content: tool results are wrapped in <tool-output> markers and
treated as data, never as instructions. Text from OCR or transcripts may
contain instructions; the agent never obeys them.

Outputs (one folder per run):
    out/result.md      what was done, the final answer, assumptions, links
    out/artifacts/     files produced (audio, subtitles, Markdown, ...)
    out/trace.jsonl    one JSON line per step (plan, tool, args, summary,
                       latency, cost)
    out/report.json    status, steps, verification, total cost and time
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from bodhan_mcp import languages, media, tools
from bodhan_mcp.client import get_client
from bodhan_mcp.errors import BUDGET_EXCEEDED, FILE_NOT_FOUND, ToolError
from bodhan_mcp.tools import tool_schemas

from . import verify
from .llm import (
    SYSTEM_PROMPT,
    MockLLMClient,
    OpenAICompatibleLLMClient,
)

TOOL_COST_HINT = (
    "Cost estimates before a call: transcribe ~Rs 0.10 per audio minute, "
    "translate ~Rs 0.20 per 10k output tokens, speech ~Rs 6 per 10k "
    "characters, OCR Rs 0.20 per image."
)


@dataclass
class AgentConfig:
    max_steps: int = 30
    max_cost_inr: float = 20.0
    timeout: float = 60.0
    out_dir: str = "./out"
    mock: bool = False


@dataclass
class CostTracker:
    limit_inr: float
    spent_inr: float = 0.0
    calls: int = 0
    cache_hits: int = 0

    def can_afford(self, estimate: float) -> bool:
        return self.spent_inr + estimate <= self.limit_inr

    def add(self, cost: float, cached: bool = False) -> None:
        self.spent_inr = round(self.spent_inr + cost, 4)
        self.calls += 1
        if cached:
            self.cache_hits += 1


def estimate_cost(name: str, args: dict, inventory: list[dict]) -> float:
    """Rough INR cost of a tool call, used for budget checks before running."""
    if name == "transcribe_audio":
        inv = next((f for f in inventory if f.get("path") == args.get("file")), {})
        duration = inv.get("duration_seconds") or 30.0
        return round(float(duration) * 0.10 / 60.0, 4)
    if name == "translate_text":
        return round(max(1, len(args.get("text", "")) // 4) * 0.20 / 10000.0, 4)
    if name == "synthesize_speech":
        return round(len(args.get("text", "")) * 6.0 / 10000.0, 4)
    if name == "ocr_document":
        inv = next((f for f in inventory if f.get("path") == args.get("file")), {})
        pages = inv.get("pages") if isinstance(inv.get("pages"), int) else 1
        return round(0.20 * max(1, pages), 4)
    if name == "identify_language" and args.get("audio_file"):
        inv = next((f for f in inventory if f.get("path") == args.get("audio_file")), {})
        duration = inv.get("duration_seconds") or 30.0
        return round(float(duration) * 0.10 / 60.0, 4)
    return 0.0


def collect_files(files_arg: str) -> list[Path]:
    p = Path(files_arg)
    if not p.exists():
        raise ToolError(
            FILE_NOT_FOUND,
            f"Input path does not exist: {files_arg}",
            hint="Pass a file or a folder of input files.",
        )
    if p.is_file():
        return [p]
    found = sorted(
        f for f in p.rglob("*") if f.is_file() and not f.name.startswith(".")
    )
    if not found:
        raise ToolError(
            FILE_NOT_FOUND,
            f"No input files found under: {files_arg}",
            hint="Pass a file or a folder containing input files.",
        )
    return found


def _language_targets_in_task(task: str) -> list[str]:
    """Language codes named in the task, in order of mention."""
    lowered = task.lower()
    found: list[str] = []
    for name, code in languages.LANGUAGE_NAME_TO_CODE.items():
        if re.search(rf"\b{name}\b", lowered) and code not in found:
            found.append(code)
    return found


class Agent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client = get_client(mock=config.mock)
        self.llm = MockLLMClient() if config.mock else None
        self.costs = CostTracker(limit_inr=config.max_cost_inr)
        self.trace: list[dict] = []
        self.results: list[dict] = []  # full tool results (trace holds summaries)
        self.assumptions: list[str] = []
        self.artifacts: list[dict] = []
        self.verification: list[dict] = []
        self._stopped: dict | None = None  # set when a budget/step limit ends the run early
        self._call_seq = 0  # ids for OpenAI-format tool-call history

    # -- trace ---------------------------------------------------------------
    def _log(self, kind: str, **fields) -> None:
        entry = {"step": len(self.trace) + 1, "kind": kind, **fields}
        self.trace.append(entry)

    # -- pre-flight ------------------------------------------------------------
    def _check_llm(self) -> None:
        if self.config.mock:
            return
        try:
            self.llm = OpenAICompatibleLLMClient(timeout=self.config.timeout)
        except ToolError as exc:
            raise ToolError(
                exc.code,
                "Cannot plan without an LLM: " + exc.message,
                hint=exc.hint + " Or run with --mock for a local test run.",
            ) from exc

    def _validate_targets(self, task: str) -> str | None:
        """Return an honest-refusal reason if the task needs an unsupported
        language, else None. Runs before any tool call or spend."""
        lowered = task.lower()
        needs_translate = "translat" in lowered
        needs_speech = any(
            w in lowered for w in ("read aloud", "speak", "audio", "narrat", "mp3")
        )
        for code in _language_targets_in_task(task):
            name = languages.language_name(code)
            if needs_translate and code not in languages.TRANSLATE_LANGUAGES:
                return (
                    f"Cannot complete this task: translation into {name} ({code}) "
                    "is not supported by Indic-Translate, which covers 23 Indian "
                    "languages plus English. No output was fabricated."
                )
            if needs_speech and code not in languages.TTS_LANGUAGES:
                return (
                    f"Cannot complete this task: speech synthesis in {name} ({code}) "
                    "is not supported by Indic-Speak. No output was fabricated."
                )
        return None

    # -- main ------------------------------------------------------------------
    def run(self, task: str, files_arg: str) -> dict:
        out_dir = Path(self.config.out_dir)
        (out_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        started = time.time()
        status = "completed"
        failure: dict | None = None
        try:
            self._check_llm()
            if not task.strip():
                raise ToolError("invalid_input", "The task is empty.")
            files = collect_files(files_arg)
            inventory = [media.inspect_file(f) for f in files]
            text_files = {}
            for f in files:
                info = next(i for i in inventory if i["path"] == str(f))
                if info.get("kind") == "text" and info.get("chars", 0) <= 20000:
                    text_files[str(f)] = Path(f).read_text(
                        encoding="utf-8", errors="replace"
                    )
            refusal = self._validate_targets(task)
            if refusal:
                return self._finish(
                    task, "failed",
                    failure={"code": "unsupported_language", "message": refusal},
                    out_dir=out_dir, started=started,
                )
            self._log("plan", summary=f"Task received; {len(files)} input file(s).")
            messages = self._initial_messages(task, inventory, text_files)
            self._loop(messages, inventory)
            if self._stopped is not None:
                status = "failed"
                failure = self._stopped
        except ToolError as exc:
            status = "failed"
            failure = exc.to_dict()
            self._log("error", summary=exc.message, code=exc.code)
        except Exception as exc:  # never crash without a report
            status = "failed"
            failure = {"code": "internal_error", "message": str(exc)}
            self._log("error", summary=str(exc), code="internal_error")
        return self._finish(
            task, status, failure=failure, out_dir=out_dir, started=started
        )

    def _initial_messages(
        self, task: str, inventory: list[dict], text_files: dict
    ) -> list[dict]:
        user_payload = {
            "task": task,
            "inventory": inventory,
            "text_files": text_files,
            "budget_inr": self.config.max_cost_inr,
            "max_steps": self.config.max_steps,
        }
        return [
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + TOOL_COST_HINT},
            {"role": "user", "content": json.dumps(user_payload)},
        ]

    def _loop(self, messages: list[dict], inventory: list[dict]) -> None:
        assert self.llm is not None
        for _ in range(self.config.max_steps):
            response = self.llm.next_action(messages, tool_schemas())
            if response.text or response.tool_calls:
                # OpenAI-format history: assistant tool calls need an id and
                # a function wrapper; each tool result carries the same id.
                # Groq's validator rejects histories without them. Append
                # even when the text is empty (models often reply with
                # tool calls only); otherwise the tool results that follow
                # would be orphaned and the next call would fail.
                calls_with_ids = []
                for call in response.tool_calls:
                    self._call_seq += 1
                    call_id = call.id or f"call_{self._call_seq}"
                    calls_with_ids.append((call_id, call))
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.text,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": json.dumps(c.arguments),
                                },
                            }
                            for call_id, c in calls_with_ids
                        ],
                    }
                )
                if response.text.startswith("summary-note:"):
                    self._log("plan", summary=response.text[:300])
            if response.done and not response.tool_calls:
                self._log("plan", summary="Planner finished: " + response.text[:300])
                break
            if not response.tool_calls:
                # A note with no action: record it and ask for the next step.
                if response.text and not response.text.startswith("summary-note:"):
                    self.assumptions.append(response.text)
                continue
            stop = False
            for call_id, call in calls_with_ids:
                if not self._execute_call(call, messages, inventory, call_id):
                    stop = True
                    break
            if stop:
                break
        else:
            self._log(
                "error", summary="Max steps reached; stopping.", code="max_steps"
            )
            self._stopped = {
                "code": "max_steps",
                "message": "Max steps reached before the task finished.",
            }
        self._verify_outputs()

    def _execute_call(self, call, messages: list[dict], inventory: list[dict],
                      call_id: str = "") -> bool:
        """Execute one tool call. Returns False when the run must stop."""
        estimate = estimate_cost(call.name, call.arguments, inventory)
        if not self.costs.can_afford(estimate):
            reason = (
                f"Budget stop: {call.name} would cost ~Rs {estimate:.2f}, "
                f"only Rs {self.costs.limit_inr - self.costs.spent_inr:.2f} left."
            )
            self._log("error", summary=reason, code=BUDGET_EXCEEDED)
            self._stopped = {"code": BUDGET_EXCEEDED, "message": reason}
            messages.append(
                {"role": "tool", "tool_call_id": call_id,
                 "content": json.dumps(
                     {"error": {"code": BUDGET_EXCEEDED,
                                "message": "Budget exhausted."}})}
            )
            return False
        started = time.time()
        try:
            result = tools.run_tool(self.client, call.name, dict(call.arguments))
            cached = bool(result.pop("cached", False)) if isinstance(result, dict) else False
            cost = float(result.get("cost_inr", 0.0)) if isinstance(result, dict) else 0.0
            self.costs.add(cost, cached=cached)
            self.results.append(
                {"tool": call.name, "arguments": dict(call.arguments), "result": result}
            )
            latency_ms = round((time.time() - started) * 1000)
            summary = self._summarise_result(call.name, result)
            self._log(
                "tool_call", tool=call.name, arguments=call.arguments,
                result_summary=summary, latency_ms=latency_ms,
                cost_inr=cost, cached=cached,
            )
            messages.append(
                {"role": "tool", "tool_call_id": call_id,
                 "content": (
                     "<tool-output>\n"
                     f"{json.dumps(result, ensure_ascii=False)[:8000]}\n</tool-output>"
                 )}
            )
            return True
        except ToolError as exc:
            latency_ms = round((time.time() - started) * 1000)
            self._log(
                "error", tool=call.name, arguments=call.arguments,
                summary=exc.message, code=exc.code, latency_ms=latency_ms,
            )
            messages.append(
                {"role": "tool", "tool_call_id": call_id,
                 "content": json.dumps({"error": exc.to_dict()})}
            )
            if exc.code == "unsupported_language":
                self._stopped = exc.to_dict()
                return False
            return True

    @staticmethod
    def _summarise_result(name: str, result: dict) -> str:
        if not isinstance(result, dict):
            return str(result)[:300]
        if "segments" in result and "text" in result:
            return f"{name}: {str(result['text'])[:200]}"
        if "translation" in result:
            return (
                f"{name} -> {result.get('target_language')}: "
                f"{str(result['translation'])[:200]}"
            )
        if "audio_path" in result:
            return (
                f"{name}: wrote {result['audio_path']} "
                f"({result.get('duration_seconds')}s)"
            )
        if "markdown" in result:
            return f"{name}: {result.get('page_count')} page(s), {result.get('blocks')} blocks"
        if "transcript" in result:
            return f"{name}: language={result.get('language')}"
        return f"{name}: ok"

    # -- verification -----------------------------------------------------------
    def _verify_outputs(self) -> None:
        """Back-transcribe generated audio; coverage-check translations."""
        for entry in list(self.results):
            if entry["tool"] == "synthesize_speech":
                self._verify_audio(entry["arguments"], entry["result"])
            elif entry["tool"] == "translate_text":
                self._verify_translation(entry["arguments"], entry["result"])

    def _verify_audio(self, args: dict, result: dict) -> None:
        text = args.get("text", "")
        audio_path = result.get("audio_path")
        check = {"check": "audio_fidelity", "text_preview": text[:80]}
        if not audio_path or not os.path.exists(audio_path):
            check.update({"status": "skipped", "reason": "no audio artifact recorded"})
            self.verification.append(check)
            return
        if not self.costs.can_afford(0.05):
            check.update({"status": "skipped", "reason": "budget left is too small"})
            self.verification.append(check)
            return
        try:
            back = tools.transcribe_audio(self.client, audio_path)
            self.costs.add(float(back.get("cost_inr", 0.0)))
            fidelity = verify.audio_fidelity(text, back["text"])
            check.update(
                {"status": "passed" if fidelity["passed"] else "failed", **fidelity}
            )
            if not fidelity["passed"]:
                retried = self._retry_speech(args)
                check["retry"] = retried
                if retried.get("passed"):
                    check["status"] = "passed_after_retry"
        except ToolError as exc:
            check.update({"status": "error", "reason": exc.message})
        self.verification.append(check)

    def _retry_speech(self, args: dict) -> dict:
        """One retry with smaller TTS chunks, then report the outcome."""
        try:
            result = tools.synthesize_speech(
                self.client, args["text"], args["language"],
                voice=args.get("voice"), style=args.get("style"),
                chunk_chars=200,
            )
            self.costs.add(float(result.get("cost_inr", 0.0)))
            back = tools.transcribe_audio(self.client, result["audio_path"])
            self.costs.add(float(back.get("cost_inr", 0.0)))
            fidelity = verify.audio_fidelity(args["text"], back["text"])
            return {"passed": fidelity["passed"], **fidelity}
        except ToolError as exc:
            return {"passed": False, "reason": exc.message}

    def _verify_translation(self, args: dict, result: dict) -> None:
        check = {
            "check": "translation_coverage",
            "target_language": args.get("target_language", ""),
        }
        coverage = verify.translation_coverage(
            args.get("text", ""), result.get("translation", ""),
            truncated=bool(result.get("truncated", False)),
        )
        check.update(
            {"status": "passed" if coverage["passed"] else "failed", **coverage}
        )
        self.verification.append(check)

    # -- outputs ------------------------------------------------------------------
    def _finish(self, task: str, status: str, failure: dict | None,
                out_dir: Path, started: float) -> dict:
        artifacts_dir = out_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._collect_artifacts(artifacts_dir, task)
        duration = round(time.time() - started, 2)
        report = {
            "status": status,
            "task": task,
            "duration_seconds": duration,
            "steps": len(self.trace),
            "tool_calls": sum(1 for e in self.trace if e.get("kind") == "tool_call"),
            "total_cost_inr": round(self.costs.spent_inr, 4),
            "budget": {
                "max_cost_inr": self.config.max_cost_inr,
                "max_steps": self.config.max_steps,
                "cache_hits": self.costs.cache_hits,
            },
            "verification": self.verification,
            "assumptions": self.assumptions,
            "artifacts": self.artifacts,
            "error": failure,
        }
        (out_dir / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (out_dir / "trace.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in self.trace),
            encoding="utf-8",
        )
        (out_dir / "result.md").write_text(
            self._render_result(task, report), encoding="utf-8"
        )
        return report

    def _collect_artifacts(self, artifacts_dir: Path, task: str) -> None:
        """Copy tool outputs into artifacts/ and record them (full results).

        Several tool calls can produce the same kind of artifact (two
        translations into one language, several transcripts); text artifacts
        are appended with a separator instead of overwriting, and audio
        files get a numbered suffix.
        """
        written: set[str] = set()
        for entry in self.results:
            tool = entry["tool"]
            result = entry["result"]
            args = entry["arguments"]
            if tool == "translate_text":
                target = result.get("target_language", args.get("target_language", ""))
                name = f"translation.{target}.md"
                self._append_text(artifacts_dir / name, result.get("translation", ""))
                if name not in written:
                    written.add(name)
                    self.artifacts.append(
                        {"name": name, "kind": "translation", "language": target}
                    )
            elif tool == "synthesize_speech":
                src = result.get("audio_path")
                if src and os.path.exists(src):
                    lang = result.get("language", "")
                    base = f"speech.{lang}" if lang else "speech"
                    name = f"{base}.wav"
                    n = 2
                    while name in written:
                        name = f"{base}.{n}.wav"
                        n += 1
                    written.add(name)
                    shutil.copy(src, artifacts_dir / name)
                    self.artifacts.append(
                        {"name": name, "kind": "audio",
                         "note": "AI-generated speech (Indic-Speak)."}
                    )
            elif tool == "ocr_document":
                self._append_text(artifacts_dir / "source.md", result.get("markdown", ""))
                if "source.md" not in written:
                    written.add("source.md")
                    self.artifacts.append({"name": "source.md", "kind": "ocr_text"})
            elif tool == "transcribe_audio":
                self._append_text(artifacts_dir / "transcript.txt", result.get("text", ""))
                if "transcript.txt" not in written:
                    written.add("transcript.txt")
                    self.artifacts.append(
                        {"name": "transcript.txt", "kind": "transcript"}
                    )
        if "subtitl" in task.lower():
            self._write_subtitles(artifacts_dir)

    @staticmethod
    def _append_text(path: Path, text: str) -> None:
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            path.write_text(
                existing.rstrip("\n") + "\n\n---\n\n" + text, encoding="utf-8"
            )
        else:
            path.write_text(text, encoding="utf-8")

    def _write_subtitles(self, artifacts_dir: Path) -> None:
        """Build an SRT from transcript segments + the translation.

        The translation is distributed across segments in proportion to
        each segment's source length (see verify.align_translation_to_segments).
        Timing is approximate; translating each segment separately is more
        accurate and is noted in the README as a follow-up.
        """
        segments: list[dict] = []
        translation = ""
        for entry in self.results:
            if entry["tool"] == "transcribe_audio":
                segments = entry["result"].get("segments", [])
            elif entry["tool"] == "translate_text":
                translation = entry["result"].get("translation", "")
        if not segments or not translation:
            return
        aligned = verify.align_translation_to_segments(translation, segments)
        (artifacts_dir / "subtitles.en.srt").write_text(
            verify.make_srt(aligned), encoding="utf-8"
        )
        self.artifacts.append(
            {"name": "subtitles.en.srt", "kind": "subtitles",
             "note": "Translation distributed across segments by source "
                     "length; timing is approximate."}
        )

    def _render_result(self, task: str, report: dict) -> str:
        lines = ["# Task result", "", f"**Task:** {task}", "",
                 f"**Status:** {report['status']}"]
        if report["error"]:
            lines += ["", f"**Why it stopped:** {report['error'].get('message', '')}"]
        lines += ["", "## What was done", ""]
        for entry in self.trace:
            if entry.get("kind") == "tool_call":
                # Summaries already start with the tool name.
                lines.append(
                    f"- {entry.get('result_summary', entry.get('tool'))} "
                    f"(Rs {entry.get('cost_inr', 0):.4f})"
                )
        if not any(e.get("kind") == "tool_call" for e in self.trace):
            lines.append("- No tool calls were made.")
        if self.assumptions:
            lines += ["", "## Assumptions", ""]
            lines += [f"- {a}" for a in self.assumptions]
        lines += ["", "## Artifacts", ""]
        if self.artifacts:
            lines += [
                f"- `artifacts/{a['name']}` ({a['kind']})" for a in self.artifacts
            ]
        else:
            lines.append("- None produced.")
        lines += ["", "## Verification", ""]
        if self.verification:
            for check in self.verification:
                lines.append(f"- {check.get('check')}: **{check.get('status')}** — {check}")
        else:
            lines.append("- No verification ran (no audio or translation was produced).")
        lines += [
            "", "## Cost and time", "",
            f"- Total cost: Rs {report['total_cost_inr']:.4f} "
            f"(budget Rs {self.config.max_cost_inr})",
            f"- Duration: {report['duration_seconds']}s, "
            f"{report['tool_calls']} tool call(s), "
            f"{report['budget']['cache_hits']} cache hit(s).",
            "",
            "_Audio artifacts are AI-generated speech (Indic-Speak). "
            "Label them as AI-generated when sharing._",
        ]
        return "\n".join(lines)
