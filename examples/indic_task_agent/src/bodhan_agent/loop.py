"""The agent loop: plan, call tools, verify, respect budgets, write a trace and a report.

Rules that must hold even if the model misbehaves are enforced HERE, in code:
  * step, time and rupee budgets (the last step only offers `finish`);
  * generated speech must be verified before the agent may claim `completed`;
  * claimed output files must exist;
  * tool errors, bad arguments and unknown tools never crash the run.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bodhan_agent.guards import compact_for_llm, scan_result, summarize_result
from bodhan_agent.llm import LLMClient, LLMError, ToolCall, simplify_schema
from bodhan_agent.prompts import FINISH_TOOL, SYSTEM_PROMPT
from bodhan_mcp.errors import BodhanError
from bodhan_mcp.server import build_server
from bodhan_mcp.toolkit import Toolkit

STATUSES = ("completed", "partial", "failed", "impossible")
MAX_FILES_LISTED = 50
FORCE_FINISH = "required"  # only `finish` is offered on those steps, so the model must call it


def load_tool_specs(tk: Toolkit) -> list[dict[str, Any]]:
    """Tool definitions for the LLM, taken from the MCP server so the two can never drift apart."""
    tools = asyncio.run(build_server(tk).list_tools())
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": simplify_schema(t.input_schema)}} for t in tools]


def expand_files(paths: Sequence[str]) -> list[Path]:
    """Turn --files arguments (files or folders) into a flat, sorted list of files."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*") if f.is_file() and not f.name.startswith(".")))
        else:
            out.append(p)
    return out[:MAX_FILES_LISTED]

def finish_payload_from_text(content: str | None) -> dict[str, Any] | None:
    """Some models write the finish arguments as JSON text instead of making the tool call.
    Recover that, so a finished task is not reported as unfinished. It still goes through
    the same safety gate as a real finish call."""
    if not content:
        return None
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        d = json.loads(content[start:end + 1])
    except ValueError:
        return None
    ok = isinstance(d, dict) and d.get("status") in STATUSES and isinstance(d.get("summary"), str)
    return d if ok else None

def _err(code: str, message: str, hint: str | None = None) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": False, "hint": hint}}


class Trace:
    """Append-only JSON Lines log: one line per step."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.write_text("", encoding="utf-8")

    def write(self, **fields: Any) -> None:
        fields = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False, default=str) + "\n")


class Agent:
    def __init__(self, llm: LLMClient, toolkit: Toolkit, *, task: str, files: Sequence[str] = (),
                 max_steps: int = 30, max_cost_inr: float = 20.0, max_seconds: float = 900.0) -> None:
        self.llm, self.tk = llm, toolkit
        self.task, self.files = task, list(files)
        self.max_steps, self.max_cost_inr, self.max_seconds = max_steps, max_cost_inr, max_seconds
        self.out_dir = toolkit.out_dir
        self.trace = Trace(self.out_dir / "trace.jsonl")
        self.specs = load_tool_specs(toolkit)
        self.tool_names = {s["function"]["name"] for s in self.specs}
        # state used by the guards and the report
        self.speech: dict[str, bool | None] = {}  # audio path -> verified? (None = not yet)
        self.verifications: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.injection_flags: list[dict[str, Any]] = []
        self.failures: dict[str, int] = {}
        self.gate_rejections = 0
        self.llm_calls = 0
        self.tokens = {"prompt": 0, "completion": 0}
        self.tool_calls = 0
        self.budget_hit = False
        self.unsupported_evidence = False  # did any tool report an unsupported language/format?

    # ---- input description -----------------------------------------------
    def _describe_inputs(self) -> str:
        files = expand_files(self.files)
        if not files:
            return "(no input files)"
        lines = []
        for f in files:
            try:
                i = self.tk.inspect_file(str(f))
                bits = [i["kind"], f"{i['size_bytes']:,} bytes"]
                if "duration_s" in i:
                    bits.append(f"{i['duration_s']} s")
                if "pages" in i:
                    bits.append(f"{i['pages']} pages")
                lines.append(f"- {i['path']}  ({', '.join(bits)})")
            except BodhanError as e:
                lines.append(f"- {f}  (PROBLEM: {e.message})")
        return "\n".join(lines)

    # ---- tool execution ----------------------------------------------------
    def _execute(self, call: ToolCall) -> dict[str, Any]:
        if call.parse_error:
            return _err("bad_arguments", call.parse_error, "Send the arguments as one valid JSON object.")
        fn = getattr(self.tk, call.name, None) if call.name in self.tool_names else None
        if fn is None:
            return _err("unknown_tool", f"There is no tool named '{call.name}'.",
                        f"Available tools: {sorted(self.tool_names)}")
        args = {k: v for k, v in call.arguments.items() if v is not None}  # models often send nulls
        key = f"{call.name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if self.failures.get(key, 0) >= 2:
            return _err("repeated_failure", "This exact call already failed twice.",
                        "Change the arguments or approach, or call finish and report the problem.")
        try:
            result = fn(**args)
        except BodhanError as e:
            result = e.to_dict()
        except TypeError as e:
            result = _err("bad_arguments", str(e), "Check the tool's parameter names and types.")
        except Exception as e:  # a bug in a tool must not kill the run
            result = _err("internal_error", f"{type(e).__name__}: {e}")
        if not result.get("ok", True):
            self.failures[key] = self.failures.get(key, 0) + 1
            if result.get("error", {}).get("code") == "budget_exceeded":
                self.budget_hit = True
        return result
    
    def _collect_evidence(self, call: ToolCall, result: dict[str, Any]) -> None:
        """Remember whether any tool reported that something is unsupported. `impossible` needs this."""
        err = result.get("error") if isinstance(result.get("error"), dict) else {}
        if str(err.get("code", "")).startswith("unsupported"):
            self.unsupported_evidence = True
        if call.name == "language_support" and result.get("ok"):
            if result.get("recognized") is False or any(result.get(m) is False for m in ("transcribe", "translate", "speak")):
                self.unsupported_evidence = True

    def _observe(self, call: ToolCall, result: dict[str, Any]) -> None:
        """Update guard state from a tool result."""
        if not result.get("ok", True):
            return
        if call.name == "speak_text" and "path" in result:
            self.speech[str(Path(result["path"]).resolve())] = None
        if call.name == "verify_speech" and "passed" in result:
            target = str(Path(self.tk._in(call.arguments["audio_path"])).resolve())
            if target in self.speech:
                self.speech[target] = bool(result["passed"])
            self.verifications.append({"tool": "verify_speech", "target": target, "passed": result["passed"],
                                       "word_recall": result.get("word_recall"),
                                       "word_precision": result.get("word_precision")})
        if result.get("script_ok") is False:
            self.warnings.append(f"{call.name}: output is not in the expected script for "
                                 f"'{result.get('target_language')}'.")
        for p in result.get("pages", []) or []:
            if isinstance(p, dict) and p.get("empty"):
                self.warnings.append(f"ocr_document: page {p.get('page')} returned no text.")

    # ---- finishing -----------------------------------------------------------
    def _output_files(self, declared: list[Any]) -> list[dict[str, Any]]:
        out = []
        for o in declared or []:
            path = o.get("path") if isinstance(o, dict) else str(o)
            if not path:
                continue
            p = Path(path)
            p = p if p.is_absolute() else self.out_dir / "artifacts" / p
            out.append({"path": str(p), "description": (o.get("description", "") if isinstance(o, dict) else ""),
                        "exists": p.is_file() and p.stat().st_size > 0,
                        "bytes": p.stat().st_size if p.is_file() else 0})
        return out

    def _finish_gate(self, args: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """Check a finish request. Returns (rejection_message_or_None, adjusted_args)."""
        status = args.get("status")
        adjusted = dict(args)
        if status not in STATUSES:
            return f"status must be one of {list(STATUSES)}.", adjusted
        if status == "impossible":
            if self.unsupported_evidence:
                adjusted["outputs"] = []
                return None, adjusted
            problem = ("'impossible' means a required language, format or capability is not supported by any "
                       "model, and no tool reported that. If you ran out of budget or a step failed, use "
                       "'failed' or 'partial'.")
            if self.gate_rejections < 1:
                self.gate_rejections += 1
                return problem, adjusted
            adjusted["status"], adjusted["downgraded_from"] = "failed", "impossible"
            adjusted["limitations"] = list(adjusted.get("limitations") or []) + [problem]
            return None, adjusted
        problems = []
        outputs = self._output_files(args.get("outputs") or [])
        missing = [o["path"] for o in outputs if not o["exists"]]
        if missing and status in ("completed", "partial"):
            problems.append(f"These declared output files do not exist or are empty: {missing}.")
        if status == "completed":
            unverified = [p for p, v in self.speech.items() if v is None]
            failed = [p for p, v in self.speech.items() if v is False]
            if unverified:
                problems.append(f"You generated speech but never verified it with verify_speech: {unverified}.")
            if failed:
                problems.append(f"verify_speech FAILED for {failed}. Fix it (or report status partial).")
        if not problems:
            return None, adjusted
        if self.gate_rejections < 1:
            self.gate_rejections += 1
            return " ".join(problems) + " Fix this, or call finish again with an honest status.", adjusted
        # Second attempt: do not loop forever. Accept, but never as "completed".
        if status == "completed":
            adjusted["status"] = "partial"
            adjusted["downgraded_from"] = "completed"
        adjusted["limitations"] = list(adjusted.get("limitations") or []) + problems
        return None, adjusted

    # ---- the loop --------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        t0 = time.monotonic()
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"TASK: {self.task}\n\nINPUT FILES:\n{self._describe_inputs()}\n\n"
                f"Limits: at most {self.max_steps} steps and Rs {self.max_cost_inr:g} total spend.")},
        ]
        final: dict[str, Any] | None = None
        stop_reason = "no_finish"
        steps = nudges = malformed = 0
        force_next = False

        while final is None:
            elapsed = time.monotonic() - t0
            if steps >= self.max_steps:
                stop_reason = "step_budget"
                break
            if elapsed > self.max_seconds:
                stop_reason = "time_budget"
                break
            steps += 1
            force = steps == self.max_steps or self.budget_hit or elapsed > self.max_seconds * 0.9
            stalled = force_next
            force_next = False
            force = steps == self.max_steps or self.budget_hit or elapsed > self.max_seconds * 0.9 or stalled
            if force:
                messages.append({"role": "user", "content": (
                    "You have not called a tool. Call the `finish` tool now with an honest status."
                    if stalled and not self.budget_hit and steps < self.max_steps else
                    "Budget reached. Call `finish` now with an honest status describing what was and "
                    "was not done.")})
            t_llm = time.monotonic()
            try:
                reply = self.llm.chat(messages, [FINISH_TOOL] if force else self.specs + [FINISH_TOOL],
                                      FORCE_FINISH if force else None)
            except LLMError as e:
                self.trace.write(step=steps, type="llm_error", code=e.code, message=e.message[:300])
                if e.code == "tool_use_failed" and malformed < 2:  # the model produced a broken call
                    malformed += 1
                    messages.append({"role": "user", "content": "Your last tool call was malformed. "
                                     "Try again with valid arguments."})
                    continue
                stop_reason = f"llm_error:{e.code}"
                break
            self.llm_calls += 1
            self.tokens["prompt"] += reply.usage.get("prompt_tokens", 0)
            self.tokens["completion"] += reply.usage.get("completion_tokens", 0)
            self.trace.write(step=steps, type="llm", latency_s=round(time.monotonic() - t_llm, 3),
                             thought=(reply.content or "")[:600], reasoning=(reply.reasoning or "")[:400],
                             tool_calls=[c.name for c in reply.tool_calls],
                             finish_reason=reply.finish_reason, forced_finish=force)
            messages.append(reply.assistant_message())

            synthetic = False  # True when the finish call was recovered from message text
            if not reply.tool_calls:
                payload = finish_payload_from_text(reply.content)
                if payload is not None:
                    reply.tool_calls = [ToolCall("text_finish", "finish", payload)]
                    synthetic = True
                    self.trace.write(step=steps, type="note", note="finish payload found in the message text; "
                                     "treated as a finish call")
                else:
                    nudges += 1
                    if nudges > 2:
                        stop_reason = "no_finish"
                        break
                    force_next = nudges >= 2  # the second silent reply: only offer `finish` next time
                    messages.append({"role": "user", "content": (
                        "You did not call a tool. Text in a message is NOT a tool call. Call the next tool, or "
                        "call the `finish` tool (status, summary, outputs...) if you are done.")})
                    continue
            nudges = 0

            for call in reply.tool_calls:
                if call.name == "finish":
                    rejection, adjusted = self._finish_gate(call.arguments)
                    self.trace.write(step=steps, type="finish_request", status=call.arguments.get("status"),
                                     accepted=rejection is None, rejection=rejection, from_text=synthetic)
                    if rejection:
                        reject_json = json.dumps(_err("finish_rejected", rejection))
                        messages.append({"role": "user", "content": reject_json} if synthetic else
                                        {"role": "tool", "tool_call_id": call.id, "content": reject_json})
                    else:
                        final, stop_reason = adjusted, "finished"
                        if not synthetic:
                            messages.append({"role": "tool", "tool_call_id": call.id, "content": '{"ok": true}'})
                    continue
                    rejection, adjusted = self._finish_gate(call.arguments)
                    self.trace.write(step=steps, type="finish_request", status=call.arguments.get("status"),
                                     accepted=rejection is None, rejection=rejection)
                    if rejection:
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": json.dumps(_err("finish_rejected", rejection))})
                    else:
                        final, stop_reason = adjusted, "finished"
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": '{"ok": true}'})
                    continue
                if final is not None:  # tools requested after an accepted finish are ignored
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": json.dumps(_err("ignored", "The task is already finished."))})
                    continue
                if force:  # only `finish` was offered; refuse anything else, whatever the model says
                    self.trace.write(step=steps, type="guard", note=f"refused {call.name}: budget reached")
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(
                        _err("budget_reached", "No more tools are available. Call finish."))})
                    continue
                spent_before, t_tool = self.tk.client.tracker.total, time.monotonic()
                result = self._execute(call)
                self.tool_calls += 1
                self._collect_evidence(call, result)
                self._observe(call, result)
                source = " ".join(str(result.get(k, "")) for k in ("text", "preview"))
                hits = scan_result(call.name, result, source)
                if hits:
                    self.injection_flags.append({"step": steps, "tool": call.name, "phrases": hits})
                shown_args = call.arguments if len(json.dumps(call.arguments, default=str)) < 600 else "(large)"
                self.trace.write(step=steps, type="tool", tool=call.name, arguments=shown_args,
                                 ok=result.get("ok", True), result=summarize_result(result),
                                 latency_s=round(time.monotonic() - t_tool, 3),
                                 cost_inr=round(self.tk.client.tracker.total - spent_before, 5),
                                 injection_suspected=bool(hits))
                messages.append({"role": "tool", "tool_call_id": call.id, "content": compact_for_llm(result)})

        return self._write_outputs(final, stop_reason, steps, time.monotonic() - t0, started)

    # ---- outputs -----------------------------------------------------------------
    def _write_outputs(self, final: dict[str, Any] | None, stop_reason: str, steps: int,
                       elapsed: float, started: str) -> dict[str, Any]:
        tracker = self.tk.client.tracker
        artifacts = sorted(p for p in (self.out_dir / "artifacts").rglob("*") if p.is_file())
        if final is None:  # the model never finished: report only what the code can prove
            final = {
                "status": "partial" if artifacts else "failed",
                "summary": (f"The run stopped before the agent reported a result (reason: {stop_reason}). "
                            f"{self.tool_calls} tool calls were made; {len(artifacts)} file(s) were produced."),
                "outputs": [{"path": str(p), "description": "produced before the run stopped"} for p in artifacts],
                "limitations": [f"Stopped early: {stop_reason}. The task may be incomplete."],
            }
        outputs = self._output_files(final.get("outputs") or [])
        unverified = [p for p, v in self.speech.items() if v is None]
        if unverified:
            self.warnings.append(f"Speech was generated but not verified: {unverified}")
        misses = []
        if steps >= self.max_steps:
            misses.append("used all allowed steps")
        if elapsed > 300:
            misses.append("multi-step run took more than 5 minutes")
        report = {
            "status": final["status"], "stop_reason": stop_reason, "task": self.task,
            "summary": final.get("summary", ""), "outputs": outputs,
            "assumptions": final.get("assumptions") or [], "verification_notes": final.get("verification") or [],
            "verifications": self.verifications, "limitations": final.get("limitations") or [],
            "warnings": self.warnings, "injection_flags": self.injection_flags,
            "downgraded_from": final.get("downgraded_from"),
            "steps": steps, "llm_calls": self.llm_calls, "tool_calls": self.tool_calls,
            "elapsed_s": round(elapsed, 2), "started_at": started,
            "cost_inr": round(tracker.total, 4), "cost_by_model": {k: round(v, 4) for k, v in tracker.by_model.items()},
            "budget_inr": self.max_cost_inr, "max_steps": self.max_steps, "llm_tokens": self.tokens,
            "llm_model": self.llm.model, "quality_target_misses": misses,
        }
        (self.out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.out_dir / "result.md").write_text(_render_result_md(report), encoding="utf-8")
        self.trace.write(type="end", status=report["status"], stop_reason=stop_reason, cost_inr=report["cost_inr"])
        return report


def _render_result_md(r: dict[str, Any]) -> str:
    def bullets(items: list[Any], empty: str = "None.") -> str:
        return "\n".join(f"- {i}" for i in items) if items else empty

    outs = [f"- `{o['path']}`" + (f" - {o['description']}" if o.get("description") else "")
            + ("" if o["exists"] else "  **(missing!)**") for o in r["outputs"]]
    ver = [f"verify_speech `{v['target']}`: {'PASSED' if v['passed'] else 'FAILED'} "
           f"(recall {v['word_recall']}, precision {v['word_precision']})" for v in r["verifications"]]
    return f"""# Result: {r['status'].upper()}

**Task:** {r['task']}

## Summary
{r['summary']}

## Outputs
{chr(10).join(outs) if outs else 'No output files.'}

## Assumptions
{bullets(r['assumptions'])}

## Verification
{bullets(ver + list(r['verification_notes']), 'No verification was recorded.')}

## Limitations and warnings
{bullets(list(r['limitations']) + list(r['warnings']))}

## Security
{bullets([f"Step {f['step']} ({f['tool']}): text resembling instructions to an AI was found in the file content and was ignored: {f['phrases']}" for f in r['injection_flags']], 'No suspicious instructions were found in the processed files.')}

## Run details
- Steps: {r['steps']} of {r['max_steps']} | LLM calls: {r['llm_calls']} | Tool calls: {r['tool_calls']} | Time: {r['elapsed_s']} s
- Cost: Rs {r['cost_inr']} of Rs {r['budget_inr']:g} allowed ({r['cost_by_model']})
- Stop reason: {r['stop_reason']}
"""