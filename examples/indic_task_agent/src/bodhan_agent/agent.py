"""The task agent loop: intake, plan and act with tools, check results, stop.

The LLM decides what to do. Code enforces the rules the LLM must not be trusted with:
budgets, file-path limits, where Indian-language text may come from, and what counts as a finished output.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bodhan_mcp.bodhan import BodhanClient
from bodhan_mcp.server import build_server
from bodhan_mcp.tools import Context, make_tools

from .llm import LLMClient, LLMError
from .prompts import SYSTEM_PROMPT, first_message
from .runlog import RunLog, redact_paths, shorten

# Indian scripts (Devanagari to Malayalam), Perso-Arabic (Urdu, Kashmiri, Sindhi), Ol Chiki and Meetei Mayek.
INDIC = "؀-ۿऀ-෿᱐-᱿ꯀ-꯿"
INDIC_CHAR = re.compile(f"[{INDIC}]")
INDIC_RUN = re.compile(f"[{INDIC}][{INDIC}\\s\\d.,!?;:'\"()\\-।॥]*")
# Only these tools make outputs; reading a file later must not replace its check result.
OUTPUT_TOOLS = {"translate_text", "speak_text", "make_subtitles", "ocr_document"}
SRT_TIMING = re.compile(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->")
MAX_INPUT_FILES = 100
LOOP_WARNING_TURNS = 4
PARALLEL_TOOLS = 4
TEXT_ARGS_CHECKED = {"translate_text", "speak_text"}
OUTPUT_KEYS = ("output_path", "audio_path", "subtitles_path", "source_subtitles_path", "markdown_path", "path")

STATUSES = ["completed", "partial", "impossible", "failed"]
SAVE_TEXT_TOOL = {
    "type": "function",
    "function": {
        "name": "save_english_text",
        "description": "Save English text you wrote (a summary, an answer, a list) as a .md or .txt file in the artifacts folder. "
        "Indian-language text is refused: write English and use translate_text to get another language.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "File name such as summary.en.md"},
                "content": {"type": "string", "description": "English text"},
            },
            "required": ["filename", "content"],
        },
    },
}
FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "End the task. List every output file the user asked for. Use status impossible, with a clear reason, when the task cannot be done.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": STATUSES},
                "answer": {"type": "string", "description": "Short English answer or explanation for the user."},
                "assumptions": {"type": "array", "items": {"type": "string"}, "description": "Assumptions you made, such as a language or voice you chose."},
                "artifacts": {"type": "array", "items": {"type": "string"}, "description": "Paths of the output files, as tools returned them."},
                "notes": {"type": "string", "description": "Anything that went wrong or is unverified."},
            },
            "required": ["status", "answer"],
        },
    },
}


@dataclass
class RunConfig:
    task: str
    inputs: list[Path]
    out_dir: Path
    max_steps: int = 30
    max_cost_inr: float = 20.0
    keep_work: bool = False


def _err(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": False}}


def _norm(text: str) -> str:
    return " ".join(text.split())


class Agent:
    def __init__(self, config: RunConfig, *, llm: LLMClient | None, client: BodhanClient, llm_error: str | None = None):
        self.cfg = config
        self.llm = llm
        self.llm_error = llm_error
        self.client = client
        self.out = config.out_dir
        self._reset_previous_run()
        self.ctx = Context(client=client, work_dir=self.out, tmp_root=self.out / ".work")
        self.functions = {f.__name__: f for f in make_tools(self.ctx)}
        self.schemas, self.params = self._schemas()
        self.log = RunLog(self.out)
        self.corpus: list[str] = []
        self.checks: dict[str, dict] = {}
        self.roots = [p.expanduser().resolve() for p in config.inputs if p.expanduser().exists()] + [self.out.resolve()]
        self.steps_used = 0

    # ----- setup ----------------------------------------------------------
    def _reset_previous_run(self) -> None:
        """If ``out`` holds an earlier run of this agent, clear it so stale files are never reported as new outputs."""
        self.out.mkdir(parents=True, exist_ok=True)
        if (self.out / "report.json").exists():
            for name in ("artifacts", ".work"):
                shutil.rmtree(self.out / name, ignore_errors=True)
            for name in ("result.md", "report.json", "trace.jsonl"):
                (self.out / name).unlink(missing_ok=True)

    def _schemas(self) -> tuple[list[dict], dict[str, dict]]:
        """The LLM sees exactly the schemas any MCP client sees, plus two agent-only tools."""
        server = build_server(self.ctx)
        tools = asyncio.run(server.list_tools())
        schemas, params = [], {}
        for tool in tools:
            schemas.append({"type": "function", "function": {"name": tool.name, "description": tool.description or "", "parameters": tool.inputSchema}})
            params[tool.name] = tool.inputSchema
        for extra in (SAVE_TEXT_TOOL, FINISH_TOOL):
            schemas.append(extra)
            params[extra["function"]["name"]] = extra["function"]["parameters"]
        return schemas, params

    def _intake(self) -> list[dict]:
        """Inspect every input file in code. The LLM starts from facts, not guesses."""
        files: list[Path] = []
        manifest: list[dict] = []
        for given in self.cfg.inputs:
            path = given.expanduser()
            if path.is_dir():
                found = sorted(p for p in path.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.relative_to(path).parts))
                files.extend(found[:MAX_INPUT_FILES])
            elif path.exists():
                files.append(path)
            else:
                manifest.append({"path": str(given), "error": "file_not_found"})
        for path in files:
            info = self.functions["inspect_media"](path=str(path))
            if info.get("ok"):
                keep = {k: info[k] for k in ("path", "kind", "duration_s", "pages", "characters", "size_bytes", "has_audio") if k in info}
                manifest.append(keep)
            else:
                manifest.append({"path": str(path), "error": info["error"]["code"], "message": info["error"]["message"]})
        self.log.event("intake", files=manifest)
        return manifest

    # ----- the loop --------------------------------------------------------
    def run(self) -> dict:
        started = time.monotonic()
        self.log.event("start", task=self.cfg.task, inputs=[str(p) for p in self.cfg.inputs], max_steps=self.cfg.max_steps,
                       max_cost_inr=self.cfg.max_cost_inr, llm_model=getattr(self.llm, "model", None))
        manifest = self._intake()
        if self.llm is None:
            return self._write_outputs(self._stop("failed", f"The LLM is not configured: {self.llm_error}"), started)

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": first_message(self.cfg.task, manifest, artifacts_dir=str(self.ctx.artifacts),
                                                      max_steps=self.cfg.max_steps, max_cost_inr=self.cfg.max_cost_inr)},
        ]
        nudges = streak = 0
        last_names: list[str] = []
        outcome: dict | None = None
        for step in range(1, self.cfg.max_steps + 1):
            self.steps_used = step
            t0 = time.monotonic()
            try:
                reply = self.llm.chat(messages, self.schemas)
            except LLMError as exc:
                self.log.event("llm_error", step=step, code=exc.code, message=exc.message)
                outcome = self._stop("failed", f"The LLM failed ({exc.code}), so the task stopped. Outputs made so far are listed.")
                break
            calls = reply["tool_calls"]
            self.log.event("llm", step=step, latency_ms=int((time.monotonic() - t0) * 1000), thought=reply["content"][:2000],
                           tool_calls=[{"name": c.get("function", {}).get("name"), "arguments": c.get("function", {}).get("arguments")} for c in calls])
            assistant: dict[str, Any] = {"role": "assistant", "content": reply["content"]}
            if calls:
                assistant["tool_calls"] = calls
            messages.append(assistant)
            if not calls:
                nudges += 1
                if nudges > 2:
                    outcome = self._stop("failed", "The agent stopped without calling finish.")
                    break
                messages.append({"role": "user", "content": "Continue with a tool call, or call finish if the task is done or cannot be done."})
                continue

            results = self._run_calls(step, calls)
            finish_outcome = None
            names = sorted({c.get("function", {}).get("name", "") for c in calls})
            streak = streak + 1 if names == last_names else 1
            last_names = names
            for call, result in results:
                shown = self._for_llm(result, call.get("function", {}).get("name", ""))
                if streak >= LOOP_WARNING_TURNS:
                    shown["_warning"] = (f"You have called {', '.join(names)} {streak} turns in a row. "
                                         "Use what you have and move to the next step, or finish.")
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(shown, ensure_ascii=False)})
                if call.get("function", {}).get("name") == "finish" and result.get("ok"):
                    finish_outcome = result["outcome"]
            if finish_outcome:
                outcome = finish_outcome
                break
            if any(r.get("error", {}).get("code") == "budget_exceeded" for _, r in results):
                outcome = self._stop("budget_exhausted", f"The INR {self.cfg.max_cost_inr:.2f} budget ran out before the task was finished. "
                                     "The next model call would have gone over it, so it was not made.")
                break
        else:
            outcome = self._stop("step_limit_reached", f"Stopped after {self.cfg.max_steps} turns without finishing.")
        return self._write_outputs(outcome, started)

    def _run_calls(self, step: int, calls: list[dict]) -> list[tuple[dict, dict]]:
        parsed = []
        for call in calls:
            fn = call.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                if not isinstance(args, dict):
                    args = None
            except (json.JSONDecodeError, TypeError, ValueError):
                args = None
            parsed.append((call, fn.get("name", ""), args))
        ordinary = [p for p in parsed if p[1] != "finish"]
        finals = [p for p in parsed if p[1] == "finish"]
        results: dict[int, dict] = {}
        if len(ordinary) > 1:
            with ThreadPoolExecutor(max_workers=PARALLEL_TOOLS) as pool:
                futures = {id(p[0]): pool.submit(self._execute, step, p[1], p[2]) for p in ordinary}
                for p in ordinary:
                    results[id(p[0])] = futures[id(p[0])].result()
        else:
            for p in ordinary:
                results[id(p[0])] = self._execute(step, p[1], p[2])
        for p in finals:
            results[id(p[0])] = self._execute(step, p[1], p[2])
        return [(p[0], results[id(p[0])]) for p in parsed]

    def _execute(self, step: int, name: str, args: dict | None) -> dict:
        t0 = time.monotonic()
        if args is None:
            result = _err("bad_arguments", "The arguments were not a valid JSON object.")
        elif name == "finish":
            result = self._finish(args)
        elif name == "save_english_text":
            result = self._save_text(args)
        elif name not in self.functions:
            result = _err("unknown_tool", f"There is no tool named {name!r}.")
        else:
            result = self._check_args(name, args) or self.functions[name](**args)
            self._remember(name, result)
        self.log.event("tool", step=step, tool=name, arguments=shorten(args, max_str=300), ok=result.get("ok"),
                       result=shorten({k: v for k, v in result.items() if k != "outcome"}, max_str=300, max_items=5),
                       latency_ms=int((time.monotonic() - t0) * 1000), cost_inr=result.get("cost_inr", 0.0))
        return result

    # ----- rules enforced in code ----------------------------------------
    def _check_args(self, name: str, args: dict) -> dict | None:
        schema = self.params.get(name, {})
        props = set(schema.get("properties", {}))
        unknown = sorted(set(args) - props)
        if unknown:
            return _err("bad_arguments", f"Unknown argument(s) {', '.join(unknown)}. {name} takes: {', '.join(sorted(props))}.")
        missing = [k for k in schema.get("required", []) if k not in args]
        if missing:
            return _err("bad_arguments", f"Missing required argument(s): {', '.join(missing)}.")
        if "path" in args and args["path"] is not None and not self._path_allowed(str(args["path"])):
            return _err("path_not_allowed", "Only the input files and this run's output folder can be used.")
        if name in TEXT_ARGS_CHECKED and isinstance(args.get("text"), str):
            stray = self._unsourced_indic(args["text"])
            if stray:
                return _err("indic_text_not_from_tool",
                            f"Indian-language text in your arguments must be copied from a tool result, and this is not: {stray[:60]!r}. "
                            "Pass the file path a tool returned, or write English and translate it.")
        return None

    def _path_allowed(self, path: str) -> bool:
        target = Path(path).expanduser().resolve()
        return any(target == root or root in target.parents for root in self.roots)

    def _unsourced_indic(self, text: str) -> str | None:
        corpus = _norm("\n".join(self.corpus))
        for run in INDIC_RUN.findall(text):
            piece = _norm(run).strip(" .,!?;:'\"()-")
            if piece and piece not in corpus:
                return piece
        return None

    def _remember(self, name: str, result: dict) -> None:
        """Keep tool text for provenance checks, and keep each output's check result."""
        def strings(value: Any):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for v in value.values():
                    yield from strings(v)
            elif isinstance(value, list):
                for v in value:
                    yield from strings(v)

        if not result.get("ok"):
            return
        self.corpus.extend(s for s in strings(result) if INDIC_CHAR.search(s))
        artifacts_dir = self.ctx.artifacts.resolve()
        for key in OUTPUT_KEYS:
            path = result.get(key)
            if not isinstance(path, str) or not Path(path).is_file():
                continue
            self._read_back(Path(path))
            if name in OUTPUT_TOOLS and artifacts_dir in Path(path).resolve().parents:
                passed = result.get("verified", result.get("passed"))
                self.checks[str(Path(path).resolve())] = {
                    "tool": name,
                    "passed": passed,
                    "warnings": result.get("warnings", []) or [f"page {p} flagged" for p in result.get("flagged_pages", [])],
                    "checks": result.get("checks", {}),
                }

    def _read_back(self, path: Path) -> None:
        """Text files a tool wrote count as tool output too (the LLM may quote them)."""
        if path.suffix.lower() in {".md", ".txt", ".srt"} and path.stat().st_size < 2_000_000:
            content = path.read_text(encoding="utf-8", errors="replace")
            if INDIC_CHAR.search(content):
                self.corpus.append(content)

    def _save_text(self, args: dict) -> dict:
        filename, content = args.get("filename"), args.get("content")
        if not isinstance(filename, str) or not isinstance(content, str) or not content.strip():
            return _err("bad_arguments", "filename and non-empty content are required.")
        if INDIC_CHAR.search(content):
            return _err("indic_text_refused", "save_english_text only stores English. Write English and translate it with translate_text.")
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name).strip("-.") or "notes"
        suffix = Path(name).suffix.lower()
        if suffix and suffix not in {".md", ".txt"}:
            return _err("bad_arguments", f"save_english_text writes .md or .txt notes, not {suffix} files. "
                        "Subtitles come from make_subtitles, translations from translate_text: use their files as they are.")
        if SRT_TIMING.search(content):
            return _err("bad_arguments", "This looks like subtitles. Use the .srt file make_subtitles wrote; do not rebuild it.")
        if not suffix:
            name += ".md"
        target = self.ctx.artifacts / name
        target.write_text(content, encoding="utf-8")
        self.checks[str(target.resolve())] = {"tool": "save_english_text", "passed": None, "warnings": [], "checks": {"written_by": "llm (English)"}}
        return {"ok": True, "path": str(target), "characters": len(content)}

    def _finish(self, args: dict) -> dict:
        status = args.get("status")
        answer = args.get("answer")
        if status not in STATUSES or not isinstance(answer, str) or not answer.strip():
            return _err("bad_arguments", f"status must be one of {', '.join(STATUSES)}, and answer must be non-empty English.")
        stray = self._unsourced_indic(answer)
        if stray:
            return _err("indic_text_not_from_tool", f"The answer contains Indian-language text that no tool produced: {stray[:60]!r}. "
                        "Answer in English, and leave Indian-language text in the output files.")
        artifacts = [a for a in (args.get("artifacts") or []) if isinstance(a, str)]
        if status == "impossible" and artifacts:
            return _err("bad_arguments", "An impossible task must not list output files. Remove them, or use status partial.")
        artifacts_dir = self.ctx.artifacts.resolve()
        problems = []
        for a in artifacts:
            p = Path(a).expanduser().resolve()
            if not p.is_file():
                problems.append(f"{a} does not exist")
            elif artifacts_dir not in p.parents:
                problems.append(f"{a} is not in the artifacts folder")
        if problems:
            return _err("bad_artifacts", "; ".join(problems) + ". List only files that tools wrote.")
        outcome = {
            "status": status,
            "answer": answer.strip(),
            "assumptions": [a for a in (args.get("assumptions") or []) if isinstance(a, str)],
            "artifacts": [str(Path(a).expanduser().resolve()) for a in artifacts],
            "notes": args.get("notes") or "",
            "stopped_by": "agent",
        }
        return {"ok": True, "outcome": outcome}

    def _stop(self, status: str, reason: str) -> dict:
        made = sorted(str(p.resolve()) for p in self.ctx.artifacts.glob("*") if p.is_file())
        return {"status": status, "answer": reason, "assumptions": [], "artifacts": made, "notes": "", "stopped_by": "runtime"}

    def _for_llm(self, result: dict, name: str = "") -> dict:
        body = {k: v for k, v in result.items() if k != "outcome"}
        full_text = body.pop("text", None) if name == "read_text" else None
        shown = shorten(body)
        if full_text is not None:
            shown["text"] = full_text  # read_text already caps its size; the LLM needs all of it
        shown["_budget"] = {
            "spent_inr": round(self.client.budget.spent, 4),
            "remaining_inr": round(max(self.cfg.max_cost_inr - self.client.budget.spent, 0.0), 4),
            "turns_left": self.cfg.max_steps - self.steps_used,
        }
        return shown

    # ----- outputs ---------------------------------------------------------
    def _write_outputs(self, outcome: dict, started: float) -> dict:
        duration = round(time.monotonic() - started, 2)
        verification = []
        for path in outcome["artifacts"]:
            check = self.checks.get(path, {"tool": None, "passed": None, "warnings": ["no check recorded"], "checks": {}})
            verification.append({"artifact": self._rel(path), **check})
        failed_checks = [v["artifact"] for v in verification if v["passed"] is False]
        ledger = self.client.ledger
        by_model: dict[str, dict] = {}
        for e in ledger:
            m = by_model.setdefault(e["model"], {"calls": 0, "cache_hits": 0, "cost_inr": 0.0})
            m["calls"] += 1
            m["cache_hits"] += int(e["cached"])
            m["cost_inr"] = round(m["cost_inr"] + e["cost_inr"], 4)
        report = {
            "status": outcome["status"],
            "stopped_by": outcome["stopped_by"],
            "task": self.cfg.task,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duration_s": duration,
            "steps_used": self.steps_used,
            "max_steps": self.cfg.max_steps,
            "cost_inr": round(self.client.budget.spent, 4),
            "max_cost_inr": self.cfg.max_cost_inr,
            "within_budget": self.client.budget.spent <= self.cfg.max_cost_inr,
            "bodhan_calls": {"total": len(ledger), "paid": sum(1 for e in ledger if not e["cached"]),
                             "cache_hits": sum(1 for e in ledger if e["cached"]), "by_model": by_model},
            "translate_route": self.client.translate_route,
            "llm": {"model": getattr(self.llm, "model", None), "calls": getattr(self.llm, "calls", 0),
                    "prompt_tokens": getattr(self.llm, "prompt_tokens", 0), "completion_tokens": getattr(self.llm, "completion_tokens", 0)},
            "artifacts": [self._rel(p) for p in outcome["artifacts"]],
            "verification": verification,
            "all_checks_passed": not failed_checks,
            "assumptions": outcome["assumptions"],
            "targets": {
                "verification": "met" if not failed_checks else f"missed: {', '.join(failed_checks)} failed a check",
                "budget": "met" if self.client.budget.spent <= self.cfg.max_cost_inr else "missed",
                "no_waste": "met (each input sent to a model at most once; repeats come from the cache)",
            },
        }
        (self.out / "report.json").write_text(redact_paths(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")
        (self.out / "result.md").write_text(redact_paths(self._result_md(outcome, report)), encoding="utf-8")
        self.log.event("finish", status=outcome["status"], stopped_by=outcome["stopped_by"], cost_inr=report["cost_inr"],
                       duration_s=duration, artifacts=report["artifacts"])
        if not self.cfg.keep_work:
            shutil.rmtree(self.out / ".work", ignore_errors=True)
        return report

    def _rel(self, path: str) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.out.resolve()))
        except ValueError:
            return path

    def _result_md(self, outcome: dict, report: dict) -> str:
        lines = [f"# Result: {outcome['status']}", "", f"**Task:** {self.cfg.task}", "", "## Answer", "", outcome["answer"], ""]
        if outcome["assumptions"]:
            lines += ["## Assumptions", ""] + [f"- {a}" for a in outcome["assumptions"]] + [""]
        lines += ["## Outputs", ""]
        if report["verification"]:
            for v in report["verification"]:
                mark = {True: "passed", False: "FAILED", None: "not checked"}[v["passed"]]
                warn = f" ({'; '.join(v['warnings'])})" if v["warnings"] else ""
                lines.append(f"- [{v['artifact']}]({v['artifact']}): check {mark}{warn}")
        else:
            lines.append("- None.")
        if any(v["artifact"].endswith((".wav", ".mp3")) for v in report["verification"]):
            lines += ["", "Audio files are AI-generated speech (Indic-Speak), not recordings of a real person."]
        if outcome["notes"]:
            lines += ["", "## Notes", "", outcome["notes"]]
        lines += [
            "",
            "## Run",
            "",
            f"- Steps: {report['steps_used']} of {report['max_steps']}",
            f"- Bodhan cost: INR {report['cost_inr']:.4f} of {report['max_cost_inr']:.2f} "
            f"({report['bodhan_calls']['paid']} paid calls, {report['bodhan_calls']['cache_hits']} from cache)",
            f"- Time: {report['duration_s']} s",
            "- Trace: [trace.jsonl](trace.jsonl), report: [report.json](report.json)",
            "",
        ]
        return "\n".join(lines)
